"""HTTP file server for sharing files, uploads, and web terminals.

Serves workspace files via signed URLs and provides upload pages.
Designed to sit behind a Cloudflare quick tunnel.

Routes:
  GET  /f/{token}/{path}     — file download/preview
  GET  /p/{token}/           — directory preview (index.html or listing)
  GET  /u/{token}            — upload page
  POST /u/{token}/upload     — receive uploaded files
  GET  /term/{token}/        — web terminal page (xterm.js)
  GET  /term/{token}/ws      — web terminal WebSocket (PTY bridge)
  GET  /tmux/{token}/        — tmux attach page (xterm.js)
  GET  /tmux/{token}/ws      — tmux attach WebSocket (grouped session)
  GET  /code/{token}/        — VS Code Web (code-server reverse proxy)
  *    /code/{token}/{path}  — code-server HTTP/WebSocket proxy
  GET  /port/{token}/        — reverse proxy to local port (landing)
  *    /port/{token}/{path}  — local port HTTP/WebSocket proxy
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import hmac
import html as html_mod
import json
import logging
import mimetypes
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path

from contextlib import contextmanager

import aiohttp
from aiohttp import WSMsgType, web


@contextmanager
def _suppress_os():
    """Suppress OSError (e.g. bad file descriptor after close)."""
    try:
        yield
    except OSError:
        pass

logger = logging.getLogger(__name__)

# Token format: {sig}-{expires} or {sig}-{expires}-{base64url_name}
# Name is optional metadata (e.g. topic name) encoded into the token.
_DEFAULT_TTL = 1800  # 30 minutes
_SIG_LENGTH = 32  # 128-bit HMAC truncation (32 hex chars)

# Upload limits
_MAX_UPLOAD_FILES = 20
_MAX_UPLOAD_FILE_SIZE = 50 * 1024 * 1024  # 50MB per file


def parse_ttl(ttl_str: str) -> int:
    """Parse TTL string like '30m', '2h', '1d' into seconds."""
    s = ttl_str.strip().lower()
    try:
        if s.endswith("m"):
            return int(s[:-1]) * 60
        elif s.endswith("h"):
            return int(s[:-1]) * 3600
        elif s.endswith("d"):
            return int(s[:-1]) * 86400
        elif s.endswith("s"):
            return int(s[:-1])
        else:
            return int(s)
    except ValueError:
        return _DEFAULT_TTL


def _load_secret() -> str:
    """Load SHARE_SECRET from env (set via .env / _load_env)."""
    secret = os.environ.get("SHARE_SECRET", "")
    if not secret:
        # Generate a random secret and warn
        import secrets

        secret = secrets.token_hex(32)
        os.environ["SHARE_SECRET"] = secret
        logger.warning(
            "SHARE_SECRET not set in .env — generated ephemeral secret "
            "(links will break on restart)"
        )
    return secret


def _encode_name(name: str) -> str:
    """Encode a display name to URL-safe base64 (no padding)."""
    return base64.urlsafe_b64encode(name.encode()).rstrip(b"=").decode()


def _decode_name(encoded: str) -> str:
    """Decode a URL-safe base64 name (re-add padding)."""
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded).decode()


def generate_token(
    path: str, ttl: int = _DEFAULT_TTL, secret: str = "", *, name: str = ""
) -> str:
    """Generate a signed token for a path with expiry.

    Token format: {sig}-{expires} or {sig}-{expires}-{base64url_name}
    The name (if provided) participates in the HMAC signature.
    """
    if not secret:
        secret = _load_secret()
    expires = int(time.time()) + ttl
    name_part = _encode_name(name) if name else ""
    msg = f"{path}:{expires}:{name_part}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    if name_part:
        return f"{sig}-{expires}-{name_part}"
    return f"{sig}-{expires}"


def verify_token(token: str, path: str, secret: str = "") -> bool:
    """Verify a token is valid and not expired."""
    return check_token(token, path, secret) == "ok"


def check_token(token: str, path: str, secret: str = "") -> str:
    """Check a token and return its status.

    Returns:
        "ok"      — valid and not expired
        "expired" — signature matches but TTL exceeded
        "invalid" — signature mismatch or malformed token
    """
    if not secret:
        secret = _load_secret()
    try:
        parts = token.split("-", 2)
        sig = parts[0]
        expires = int(parts[1])
        name_part = parts[2] if len(parts) > 2 else ""
    except (ValueError, AttributeError, IndexError):
        return "invalid"
    msg = f"{path}:{expires}:{name_part}"
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    if not hmac.compare_digest(sig, expected):
        return "invalid"
    if time.time() > expires:
        return "expired"
    return "ok"


def extract_token_name(token: str) -> str:
    """Extract the display name from a token (empty string if none)."""
    try:
        parts = token.split("-", 2)
        if len(parts) > 2:
            return _decode_name(parts[2])
    except Exception:
        pass
    return ""


def _resolve_relative(workspace_roots: list[Path], abs_path: Path) -> tuple[Path, str] | None:
    """Find which workspace root contains abs_path and return (root, relative_path).

    Returns None if path is not under any workspace root.
    """
    abs_resolved = abs_path.resolve()
    for root in workspace_roots:
        root_resolved = root.resolve()
        try:
            rel = abs_resolved.relative_to(root_resolved)
            return root_resolved, str(rel)
        except ValueError:
            continue
    return None


def _safe_resolve(base: Path, rel_path: str) -> Path | None:
    """Resolve a relative path safely within base directory (prevent traversal).

    Rejects absolute paths to prevent bypassing workspace restrictions.
    """
    # Reject absolute paths — only relative paths allowed
    if rel_path.startswith("/"):
        return None
    try:
        target = (base / rel_path).resolve()
        base_resolved = base.resolve()
        # Use os.sep suffix to prevent prefix collision (e.g. /tmp/workspace vs /tmp/workspace_evil)
        if target == base_resolved or str(target).startswith(str(base_resolved) + os.sep):
            return target
    except (ValueError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# HTML templates — loaded once at import time from templates/ directory
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_UPLOAD_HTML = (_TEMPLATES_DIR / "upload.html").read_text(encoding="utf-8")
_EXPIRED_HTML = (_TEMPLATES_DIR / "expired.html").read_text(encoding="utf-8")
_INVALID_HTML = (_TEMPLATES_DIR / "invalid.html").read_text(encoding="utf-8")
_DIRECTORY_HTML = (_TEMPLATES_DIR / "directory.html").read_text(encoding="utf-8")
_TERMINAL_HTML = (_TEMPLATES_DIR / "terminal.html").read_text(encoding="utf-8")
_HUB_HTML = (_TEMPLATES_DIR / "web.html").read_text(encoding="utf-8")
_TODO_HTML = (_TEMPLATES_DIR / "todo.html").read_text(encoding="utf-8")
_CRON_HTML = (_TEMPLATES_DIR / "cron.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


class CodeServerManager:
    """Manages code-server processes for VS Code Web access.

    Each directory gets its own code-server instance on a unique port.
    Uses code-server's built-in idle timeout for auto-shutdown.
    """

    _BASE_PORT = 13370
    _MAX_INSTANCES = 10
    _DEFAULT_IDLE_TIMEOUT = 3600  # 1 hour

    def __init__(self, idle_timeout: int = _DEFAULT_IDLE_TIMEOUT) -> None:
        self._idle_timeout = idle_timeout
        # directory_abs -> (port, process)
        self._instances: dict[str, tuple[int, asyncio.subprocess.Process]] = {}
        self._lock = asyncio.Lock()
        self._install_lock = asyncio.Lock()
        self._install_checked = False

    async def get_or_start(self, directory: Path) -> int:
        """Get or start a code-server for the given directory. Returns port."""
        async with self._lock:
            return await self._get_or_start_locked(directory)

    async def _get_or_start_locked(self, directory: Path) -> int:
        key = str(directory.resolve())
        if key in self._instances:
            port, proc = self._instances[key]
            if proc.returncode is None:  # still running
                return port
            # Process died, clean up
            del self._instances[key]

        if len(self._instances) >= self._MAX_INSTANCES:
            raise RuntimeError("Maximum code-server instances reached")

        port = self._allocate_port()
        cs_bin = self._find_code_server_bin() or "code-server"
        proc = await asyncio.create_subprocess_exec(
            cs_bin,
            "--auth", "none",
            "--bind-addr", f"127.0.0.1:{port}",
            "--disable-telemetry", "--disable-update-check",
            "--idle-timeout-seconds", str(self._idle_timeout),
            "--trusted-origins", "*",
            str(directory),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._instances[key] = (port, proc)
        logger.info(
            "Starting code-server on port %d for %s (PID %d)", port, key, proc.pid
        )
        await self._wait_ready(port)
        return port

    def _allocate_port(self) -> int:
        """Find the next available port."""
        used = {p for p, _ in self._instances.values()}
        for port in range(self._BASE_PORT, self._BASE_PORT + self._MAX_INSTANCES):
            if port not in used:
                return port
        raise RuntimeError("No available ports for code-server")

    async def _wait_ready(self, port: int, timeout: float = 30.0) -> None:
        """Wait for code-server to respond to HTTP requests."""
        deadline = asyncio.get_event_loop().time() + timeout
        session = aiohttp.ClientSession()
        try:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    async with session.get(
                        f"http://127.0.0.1:{port}/healthz",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        if resp.status == 200:
                            logger.info("code-server ready on port %d", port)
                            return
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(0.5)
        finally:
            await session.close()
        raise RuntimeError(
            f"code-server on port {port} failed to start within {timeout}s"
        )

    def _find_code_server_bin(self) -> str | None:
        """Find code-server binary, checking PATH and standalone install."""
        import shutil

        path = shutil.which("code-server")
        if path:
            return path
        # Standalone install goes to ~/.local/bin/code-server
        standalone = Path.home() / ".local" / "bin" / "code-server"
        if standalone.exists() and os.access(standalone, os.X_OK):
            return str(standalone)
        return None

    async def ensure_installed(self) -> None:
        """Install code-server if not already present."""
        if self._install_checked:
            return
        async with self._install_lock:
            if self._find_code_server_bin():
                self._install_checked = True
                return

            logger.info("code-server not found, installing (standalone)...")
            install = await asyncio.create_subprocess_shell(
                "curl -fsSL https://code-server.dev/install.sh | sh -s -- --method standalone",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(install.communicate(), timeout=300)
            if install.returncode != 0:
                output = stdout.decode("utf-8", errors="replace") if stdout else ""
                raise RuntimeError(
                    f"code-server installation failed (exit {install.returncode}): "
                    f"{output[-500:]}"
                )
            logger.info("code-server installed successfully")
            self._install_checked = True

    async def stop_all(self) -> None:
        """Stop all running code-server instances."""
        for key in list(self._instances):
            port, proc = self._instances.pop(key)
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (OSError, asyncio.TimeoutError):
                try:
                    proc.kill()
                except OSError:
                    pass
            logger.info("Stopped code-server on port %d", port)


def _deny_response(reason: str) -> web.Response:
    """Return an appropriate error page based on token check result."""
    if reason == "expired":
        return web.Response(text=_EXPIRED_HTML, content_type="text/html", status=410)
    return web.Response(text=_INVALID_HTML, content_type="text/html", status=403)


class ShareServer:
    """aiohttp-based file share server.

    Workspace-aware: each token encodes the workspace root it belongs to.
    This allows multiple topics/sessions to share files from their own workspace.

    Token path formats:
      upload:{workspace_abs_path}     — upload to specific workspace
      f:{workspace_abs_path}:{rel}    — serve file from specific workspace
      p:{workspace_abs_path}:{rel}    — serve directory from specific workspace

    Legacy formats (without workspace) are also supported for backward compat:
      upload                          — upload to first workspace root
      f:{rel_path}                    — serve file (search all roots)
      p:{rel_path}                    — serve directory (search all roots)
    """

    def __init__(
        self,
        *,
        port: int = 8787,
        workspace_roots: list[Path] | None = None,
        on_upload: Callable[[Path, list[str], str], Awaitable[None]] | None = None,
    ) -> None:
        self._port = port
        self._workspace_roots = workspace_roots or []
        self._on_upload = on_upload
        self._app = web.Application(client_max_size=100 * 1024 * 1024)  # 100MB
        self._runner: web.AppRunner | None = None
        self._code_manager = CodeServerManager()
        self._proxy_session: aiohttp.ClientSession | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/f/{token}/{path:.*}", self._handle_file)
        self._app.router.add_get("/p/{token}/{path:.*}", self._handle_preview)
        self._app.router.add_delete("/p/{token}/{path:.*}", self._handle_delete)
        self._app.router.add_get("/u/{token}", self._handle_upload_page)
        self._app.router.add_post("/u/{token}/upload", self._handle_upload)
        self._app.router.add_get("/term/{token}/ws", self._handle_terminal_ws)
        self._app.router.add_get("/term/{token}/", self._handle_terminal_page)
        self._app.router.add_get("/term/{token}", self._handle_terminal_page)
        self._app.router.add_get("/tmux/{token}/ws", self._handle_tmux_ws)
        self._app.router.add_get("/tmux/{token}/", self._handle_tmux_page)
        self._app.router.add_get("/tmux/{token}", self._handle_tmux_page)
        self._app.router.add_get("/code/{token}", self._handle_code_redirect)
        self._app.router.add_route("*", "/code/{token}/{path:.*}", self._handle_code_proxy)
        self._app.router.add_get("/port/{token}", self._handle_port_redirect)
        self._app.router.add_route("*", "/port/{token}/{path:.*}", self._handle_port_proxy)
        self._app.router.add_get("/hub/{token}/urls", self._handle_hub_urls)
        self._app.router.add_get("/hub/{token}/stats", self._handle_hub_stats)
        self._app.router.add_get("/hub/{token}/", self._handle_hub_page)
        self._app.router.add_get("/hub/{token}", self._handle_hub_redirect)
        # TODO management UI
        self._app.router.add_get("/todo/{token}/", self._handle_todo_page)
        self._app.router.add_get("/todo/{token}", self._handle_todo_redirect)
        self._app.router.add_get("/todo/{token}/api", self._handle_todo_list)
        self._app.router.add_post("/todo/{token}/api", self._handle_todo_add)
        self._app.router.add_put("/todo/{token}/api/{todo_id}", self._handle_todo_update)
        self._app.router.add_delete("/todo/{token}/api/{todo_id}", self._handle_todo_delete)
        self._app.router.add_post(
            "/todo/{token}/api/{todo_id}/done", self._handle_todo_done
        )
        # Cron management UI
        self._app.router.add_get("/cron/{token}/", self._handle_cron_page)
        self._app.router.add_get("/cron/{token}", self._handle_cron_redirect)
        self._app.router.add_get("/cron/{token}/api", self._handle_cron_list)
        self._app.router.add_post(
            "/cron/{token}/api/{job_id}/toggle", self._handle_cron_toggle
        )

    def _find_file(self, rel_path: str, workspace: Path | None = None) -> Path | None:
        """Find a file in a specific workspace or across all roots."""
        roots = [workspace] if workspace else self._workspace_roots
        for root in roots:
            resolved = _safe_resolve(root, rel_path)
            if resolved and resolved.is_file():
                return resolved
        return None

    def _find_dir(self, rel_path: str, workspace: Path | None = None) -> Path | None:
        """Find a directory in a specific workspace or across all roots."""
        roots = [workspace] if workspace else self._workspace_roots
        for root in roots:
            resolved = _safe_resolve(root, rel_path)
            if resolved and resolved.is_dir():
                return resolved
        return None

    def _verify_with_workspace(
        self, token: str, prefix: str, rel_path: str
    ) -> tuple[Path | None, str]:
        """Try to verify token against each registered workspace root.

        Token payload format: '{prefix}:{workspace_abs}:{rel_path}'
        Returns (workspace_path, status) where status is "ok", "expired", or "invalid".
        """
        worst = "invalid"
        for root in self._workspace_roots:
            token_path = f"{prefix}:{root}:{rel_path}"
            status = check_token(token, token_path)
            if status == "ok":
                return root, "ok"
            if status == "expired":
                worst = "expired"
        return None, worst

    def _verify_upload_workspace(self, token: str) -> tuple[Path | None, str]:
        """Try to verify upload token against each registered workspace root.

        Token payload format: 'upload:{workspace_abs}'
        Returns (workspace_path, status) where status is "ok", "expired", or "invalid".
        """
        worst = "invalid"
        for root in self._workspace_roots:
            status = check_token(token, f"upload:{root}")
            if status == "ok":
                return root, "ok"
            if status == "expired":
                worst = "expired"
        return None, worst

    def add_workspace(self, workspace: Path) -> None:
        """Register a workspace root dynamically (e.g., when a new topic is created)."""
        ws = workspace.resolve()
        if ws not in [r.resolve() for r in self._workspace_roots]:
            self._workspace_roots.append(ws)
            logger.info("Registered workspace root: %s", ws)

    # -- Shared file response --

    @staticmethod
    def _file_response(file_path: Path) -> web.FileResponse:
        """Build a FileResponse with appropriate headers for any file type."""
        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"

        _INLINE_TYPES = ("image/", "application/pdf")
        disposition = "inline" if content_type.startswith(_INLINE_TYPES) else "attachment"
        safe_filename = urllib.parse.quote(file_path.name, safe="")

        headers = {
            "Content-Type": content_type,
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{safe_filename}",
            "X-Content-Type-Options": "nosniff",
        }
        if content_type.startswith("text/html"):
            headers["Content-Disposition"] = "inline"
            headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'unsafe-inline' https:; "
                "style-src 'unsafe-inline' https:; "
                "img-src data: blob: https: http:; "
                "connect-src https: http:; "
                "font-src https:;"
            )

        return web.FileResponse(file_path, headers=headers)

    # -- File download/preview --

    async def _handle_file(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        path = request.match_info["path"]

        # Try workspace-aware verification first
        workspace, ws_status = self._verify_with_workspace(token, "f", path)
        if workspace is None:
            # Fall back to legacy format (no workspace)
            legacy = check_token(token, f"f:{path}")
            if legacy != "ok":
                # Use the more specific reason (expired > invalid)
                reason = ws_status if ws_status == "expired" else legacy
                return _deny_response(reason)

        file_path = self._find_file(path, workspace)
        if not file_path:
            raise web.HTTPNotFound()

        return self._file_response(file_path)

    # -- Directory preview --

    async def _handle_preview(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        path = request.match_info.get("path", "")

        # 1. Exact path verification
        workspace, worst_status = self._verify_with_workspace(token, "p", path)

        # 2. Parent path backtracking: directory token grants access to sub-paths
        if workspace is None:
            parent = path
            while "/" in parent:
                parent = parent.rsplit("/", 1)[0]
                workspace, st = self._verify_with_workspace(token, "p", parent)
                if st == "expired":
                    worst_status = "expired"
                if workspace:
                    break
            # Also check root (empty path) as parent — handles files/dirs at workspace root
            if workspace is None and path:
                workspace, st = self._verify_with_workspace(token, "p", "")
                if st == "expired":
                    worst_status = "expired"

        # 3. Legacy fallback (no workspace in token) with parent backtracking
        if workspace is None:
            legacy = check_token(token, f"p:{path}")
            if legacy == "expired":
                worst_status = "expired"
            if legacy != "ok":
                parent = path
                verified = False
                while "/" in parent:
                    parent = parent.rsplit("/", 1)[0]
                    st = check_token(token, f"p:{parent}")
                    if st == "expired":
                        worst_status = "expired"
                    if st == "ok":
                        verified = True
                        break
                # Also check root (empty path) as parent
                if not verified and path:
                    st = check_token(token, "p:")
                    if st == "expired":
                        worst_status = "expired"
                    if st == "ok":
                        verified = True
                if not verified:
                    return _deny_response(worst_status)

        dir_path = self._find_dir(path, workspace)
        if not dir_path:
            # Path might be a file under a shared directory — serve it directly
            file_path = self._find_file(path, workspace)
            if file_path:
                return self._file_response(file_path)
            raise web.HTTPNotFound()

        # If index.html exists, serve it with restrictive CSP
        index = dir_path / "index.html"
        if index.is_file():
            return web.FileResponse(
                index,
                headers={
                    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; script-src 'self';",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        # Build item list with metadata for the file manager template
        items_data = []
        for item in sorted(dir_path.iterdir()):
            is_hidden = item.name.startswith(".")
            try:
                stat = item.stat()
                items_data.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if not item.is_dir() else None,
                    "mtime": int(stat.st_mtime),
                    "hidden": is_hidden,
                })
            except OSError:
                items_data.append({"name": item.name, "is_dir": item.is_dir(), "size": None, "mtime": None, "hidden": is_hidden})

        # Display name embedded in token
        source_name = extract_token_name(token)

        data = {
            "title": Path(path).name or "Files",
            "token": token,
            "path": path,
            "items": items_data,
            "source": source_name,
        }
        page_html = _DIRECTORY_HTML.replace(
            '/*__DATA__*/{"title":"","token":"","path":"","items":[]}/*__END__*/',
            json.dumps(data, ensure_ascii=False),
        )
        return web.Response(
            text=page_html,
            content_type="text/html",
            headers={
                "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data: blob:;",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # -- Delete handler --

    async def _handle_delete(self, request: web.Request) -> web.StreamResponse:
        """Delete a file or directory within a browse token's scope."""
        token = request.match_info["token"]
        path = request.match_info.get("path", "")

        if not path:
            return web.Response(text="Cannot delete root", status=400)

        # Require workspace-aware token — reject legacy tokens for destructive ops
        workspace, worst_status = self._verify_with_workspace(token, "p", path)
        if workspace is None:
            parent = path
            while "/" in parent:
                parent = parent.rsplit("/", 1)[0]
                workspace, st = self._verify_with_workspace(token, "p", parent)
                if st == "expired":
                    worst_status = "expired"
                if workspace:
                    break
            if workspace is None and path:
                workspace, st = self._verify_with_workspace(token, "p", "")
                if st == "expired":
                    worst_status = "expired"
        if workspace is None:
            # Do NOT fall back to legacy tokens for delete — require workspace scope
            return _deny_response(worst_status)

        # Resolve target path (workspace is guaranteed non-None)
        target = self._find_file(path, workspace) or self._find_dir(path, workspace)
        if not target or not target.exists():
            return web.Response(text="Not found", status=404)

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            logger.info("Deleted via browse: %s", target)
            return web.Response(text="OK", status=200)
        except OSError as e:
            logger.error("Delete failed: %s — %s", target, e)
            return web.Response(text="Delete failed", status=500)

    # -- Upload page --

    async def _handle_upload_page(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]

        # Verify: workspace-aware or legacy
        workspace, ws_status = self._verify_upload_workspace(token)
        if workspace is None:
            legacy = check_token(token, "upload")
            if legacy != "ok":
                reason = ws_status if ws_status == "expired" else legacy
                return _deny_response(reason)

        # Display name embedded in token
        source_name = extract_token_name(token)
        page_html = _UPLOAD_HTML.replace(
            "/*__SOURCE__*/''/*__END__*/",
            json.dumps(source_name, ensure_ascii=False),
        )
        return web.Response(text=page_html, content_type="text/html")

    # -- Upload handler --

    async def _handle_upload(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]

        # Verify and determine target workspace
        workspace, ws_status = self._verify_upload_workspace(token)
        if workspace is None:
            legacy = check_token(token, "upload")
            if legacy != "ok":
                reason = ws_status if ws_status == "expired" else legacy
                return _deny_response(reason)
            # Legacy token: use first workspace root as fallback
            workspace = self._workspace_roots[0] if self._workspace_roots else None
        if workspace is None:
            raise web.HTTPInternalServerError(text="No workspace configured")

        reader = await request.multipart()
        if reader is None:
            raise web.HTTPBadRequest(text="No multipart data")

        # Create upload directory under the target workspace
        import secrets as _secrets

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = _secrets.token_hex(3)
        upload_dir = workspace / "tmp" / "uploads" / f"{timestamp}-{suffix}"
        upload_dir.mkdir(parents=True, exist_ok=True)

        filenames: list[str] = []
        description = ""

        try:
            async for part in reader:
                if part.name == "description":
                    description = (await part.read()).decode("utf-8", errors="replace")
                elif part.name == "files" and part.filename:
                    if len(filenames) >= _MAX_UPLOAD_FILES:
                        raise web.HTTPBadRequest(
                            text=f"Too many files (max {_MAX_UPLOAD_FILES})"
                        )

                    # Sanitize filename — deduplicate same-name files
                    safe_name = Path(part.filename).name
                    if not safe_name or safe_name.startswith("."):
                        safe_name = f"file_{len(filenames)}"
                    if safe_name in filenames:
                        stem = Path(safe_name).stem
                        ext = Path(safe_name).suffix
                        safe_name = f"{stem}_{len(filenames)}{ext}"

                    file_path = upload_dir / safe_name
                    bytes_written = 0
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk(8192)
                            if not chunk:
                                break
                            bytes_written += len(chunk)
                            if bytes_written > _MAX_UPLOAD_FILE_SIZE:
                                raise web.HTTPRequestEntityTooLarge(
                                    max_size=_MAX_UPLOAD_FILE_SIZE,
                                    actual_size=bytes_written,
                                )
                            f.write(chunk)
                    filenames.append(safe_name)
                    logger.info("Uploaded: %s (%s, %d bytes)", safe_name, file_path, bytes_written)
        except (web.HTTPBadRequest, web.HTTPRequestEntityTooLarge):
            # Clean up partially uploaded files on limit errors
            import shutil

            shutil.rmtree(upload_dir, ignore_errors=True)
            raise

        if not filenames:
            upload_dir.rmdir()
            raise web.HTTPBadRequest(text="No files uploaded")

        # Notify callback
        if self._on_upload:
            try:
                await self._on_upload(upload_dir, filenames, description)
            except Exception:
                logger.exception("Upload callback failed")

        return web.json_response({"status": "ok", "files": filenames})

    # -- Terminal (web shell) --

    def _verify_terminal_workspace(self, token: str) -> tuple[Path | None, str]:
        """Verify terminal token against each registered workspace root.

        Token payload format: 'term:{workspace_abs}'
        Returns (workspace_path, status).
        """
        worst = "invalid"
        for root in self._workspace_roots:
            status = check_token(token, f"term:{root}")
            if status == "ok":
                return root, "ok"
            if status == "expired":
                worst = "expired"
        return None, worst

    async def _handle_terminal_page(self, request: web.Request) -> web.Response:
        """Serve the xterm.js terminal page."""
        token = request.match_info["token"]
        workspace, ws_status = self._verify_terminal_workspace(token)
        if workspace is None:
            return _deny_response(ws_status)
        return web.Response(text=_TERMINAL_HTML, content_type="text/html")

    async def _handle_terminal_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket handler: bridge browser ↔ PTY."""
        token = request.match_info["token"]
        workspace, ws_status = self._verify_terminal_workspace(token)
        if workspace is None:
            raise web.HTTPForbidden()

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Create PTY
        master_fd, slave_fd = pty.openpty()

        # Set default terminal size
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Spawn shell — use start_new_session instead of preexec_fn
        # (preexec_fn is unsafe with threads, can deadlock after fork)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env.pop("CLAUDECODE", None)
        shell = os.environ.get("SHELL", "/bin/bash")
        proc = subprocess.Popen(
            [shell, "-l"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(workspace),
            env=env,
            start_new_session=True,
        )
        os.close(slave_fd)
        logger.info("Terminal session started: PID %d in %s", proc.pid, workspace)

        # Set master_fd to non-blocking for async reading
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        loop = asyncio.get_event_loop()

        async def pty_reader() -> None:
            """Read PTY output and send to WebSocket using event loop I/O."""
            while not ws.closed:
                # Wait for data using event loop's I/O multiplexer
                readable = loop.create_future()
                loop.add_reader(master_fd, readable.set_result, True)
                try:
                    await readable
                except asyncio.CancelledError:
                    with _suppress_os():
                        loop.remove_reader(master_fd)
                    raise
                finally:
                    with _suppress_os():
                        loop.remove_reader(master_fd)
                # Read available data
                try:
                    data = os.read(master_fd, 32768)
                    if not data:
                        break
                    await ws.send_bytes(data)
                except OSError:
                    break

        async def ping_sender() -> None:
            """Send WebSocket ping every 20s to prevent idle timeout."""
            while not ws.closed:
                try:
                    await asyncio.sleep(20)
                    if not ws.closed:
                        await ws.ping()
                except (asyncio.CancelledError, ConnectionResetError):
                    break
                except Exception:
                    break

        reader_task = asyncio.create_task(pty_reader())
        ping_task = asyncio.create_task(ping_sender())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "resize":
                            cols = data.get("cols", 80)
                            rows = data.get("rows", 24)
                            ws_pack = struct.pack("HHHH", rows, cols, 0, 0)
                            try:
                                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ws_pack)
                                os.kill(proc.pid, signal.SIGWINCH)
                            except OSError:
                                pass
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif msg.type == WSMsgType.BINARY:
                    try:
                        os.write(master_fd, msg.data)
                    except OSError:
                        break
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            # Cleanup: close PTY, cancel reader, terminate shell
            try:
                os.close(master_fd)
            except OSError:
                pass
            reader_task.cancel()
            ping_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await ping_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            logger.info("Terminal session ended: PID %d", proc.pid)

        return ws

    # -- Tmux attach (web tmux) --

    def _verify_tmux_token(self, token: str) -> tuple[str | None, str]:
        """Verify tmux token and return (payload, status).

        Token payload format: 'tmux:{session}:{window_id}' or 'log:{agent_name}'
        Returns (payload, status).
        """
        # Extract the name part from the token to reconstruct the payload
        try:
            parts = token.split("-", 2)
            name_part = parts[2] if len(parts) > 2 else ""
        except (IndexError, ValueError):
            return None, "invalid"
        if not name_part:
            return None, "invalid"
        # Decode the name to get the payload
        name = base64.urlsafe_b64decode(name_part + "==").decode()
        # Try tmux payload
        tmux_payload = f"tmux:{name}"
        status = check_token(token, tmux_payload)
        if status == "ok":
            return tmux_payload, "ok"
        if status == "expired":
            return None, "expired"
        # Try log payload
        log_payload = f"log:{name}"
        status = check_token(token, log_payload)
        if status == "ok":
            return log_payload, "ok"
        if status == "expired":
            return None, "expired"
        return None, "invalid"

    async def _handle_tmux_page(self, request: web.Request) -> web.Response:
        """Serve the xterm.js terminal page for tmux attach."""
        token = request.match_info["token"]
        payload, status = self._verify_tmux_token(token)
        if payload is None:
            return _deny_response(status)
        return web.Response(text=_TERMINAL_HTML, content_type="text/html")

    async def _handle_tmux_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket handler: bridge browser ↔ tmux attach via PTY."""
        token = request.match_info["token"]
        payload, status = self._verify_tmux_token(token)
        if payload is None:
            raise web.HTTPForbidden()

        # Parse payload to determine tmux target
        parts = payload.split(":", 2)
        mode = parts[0]  # "tmux" or "log"

        if mode == "tmux":
            # payload: tmux:{session}:{window_id}
            tmux_session = parts[1]
            window_id = parts[2]
        elif mode == "log":
            # payload: log:{agent_name}
            # Connect to the agent's main tmux window (__main__)
            agent_name = parts[1]
            tmux_session = agent_name
            window_id = None  # will target main window
        else:
            raise web.HTTPForbidden()

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Create a grouped session for isolated attach
        import secrets as _secrets

        temp_session = f"web-{_secrets.token_hex(4)}"

        # Build tmux new-session command that groups with the target session
        # and selects the target window
        cmd = ["tmux", "new-session", "-d", "-s", temp_session, "-t", tmux_session]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=5)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("Failed to create grouped tmux session: %s", exc)
            await ws.close(message=b"Failed to create tmux session")
            return ws

        # Select the target window
        if window_id:
            try:
                subprocess.run(
                    ["tmux", "select-window", "-t", f"{temp_session}:{window_id}"],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.warning("Failed to select window %s, using default", window_id)

        # Create PTY and spawn tmux attach
        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        proc = subprocess.Popen(
            ["tmux", "attach-session", "-t", temp_session],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            start_new_session=True,
        )
        os.close(slave_fd)
        logger.info(
            "Tmux web session started: PID %d, session=%s, target=%s:%s",
            proc.pid,
            temp_session,
            tmux_session,
            window_id or "__main__",
        )

        # Set master_fd to non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        loop = asyncio.get_event_loop()

        async def pty_reader() -> None:
            while not ws.closed:
                readable = loop.create_future()
                loop.add_reader(master_fd, readable.set_result, True)
                try:
                    await readable
                except asyncio.CancelledError:
                    with _suppress_os():
                        loop.remove_reader(master_fd)
                    raise
                finally:
                    with _suppress_os():
                        loop.remove_reader(master_fd)
                try:
                    data = os.read(master_fd, 32768)
                    if not data:
                        break
                    await ws.send_bytes(data)
                except OSError:
                    break

        async def ping_sender() -> None:
            while not ws.closed:
                try:
                    await asyncio.sleep(20)
                    if not ws.closed:
                        await ws.ping()
                except (asyncio.CancelledError, ConnectionResetError):
                    break
                except Exception:
                    break

        reader_task = asyncio.create_task(pty_reader())
        ping_task = asyncio.create_task(ping_sender())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "resize":
                            cols = data.get("cols", 80)
                            rows = data.get("rows", 24)
                            ws_pack = struct.pack("HHHH", rows, cols, 0, 0)
                            try:
                                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ws_pack)
                                os.kill(proc.pid, signal.SIGWINCH)
                            except OSError:
                                pass
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif msg.type == WSMsgType.BINARY:
                    try:
                        os.write(master_fd, msg.data)
                    except OSError:
                        break
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            reader_task.cancel()
            ping_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await ping_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            # Kill the temporary grouped session
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", temp_session],
                    capture_output=True,
                    timeout=5,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            logger.info("Tmux web session ended: PID %d, session=%s", proc.pid, temp_session)

        return ws

    # -- VS Code Web (code-server reverse proxy) --

    def _verify_code_workspace(self, token: str) -> tuple[Path | None, str]:
        """Verify code-server token.

        Token payload format: 'code:{directory_abs}' with directory encoded in name.
        Returns (directory_path, status).
        """
        # Extract directory path from token name field
        dir_path = extract_token_name(token)
        if not dir_path:
            return None, "invalid"

        # Verify HMAC signature against the expected payload
        status = check_token(token, f"code:{dir_path}")
        if status != "ok":
            return None, status

        # Security: ensure directory is under a registered workspace root
        resolved = Path(dir_path).resolve()
        for root in self._workspace_roots:
            root_resolved = root.resolve()
            try:
                resolved.relative_to(root_resolved)
                return resolved, "ok"
            except ValueError:
                continue
        return None, "invalid"

    async def _handle_code_redirect(self, request: web.Request) -> web.Response:
        """Redirect /code/{token} to /code/{token}/ (add trailing slash)."""
        raise web.HTTPFound(f"/code/{request.match_info['token']}/")

    async def _handle_code_proxy(self, request: web.Request) -> web.StreamResponse:
        """Reverse proxy for code-server: handles both HTTP and WebSocket."""
        token = request.match_info["token"]
        path = request.match_info.get("path", "")

        # Verify token
        workspace, ws_status = self._verify_code_workspace(token)
        if workspace is None:
            return _deny_response(ws_status)

        # Get or start code-server (auto-install if missing)
        try:
            await self._code_manager.ensure_installed()
            port = await self._code_manager.get_or_start(workspace)
        except RuntimeError as e:
            return web.Response(text=str(e), status=503)

        # Build target URL
        target_url = f"http://127.0.0.1:{port}/{path}"
        if request.query_string:
            target_url += f"?{request.query_string}"
        elif not path:
            # Initial page load: redirect browser to include ?folder= in the URL.
            # VS Code's JS reads window.location.search to determine which folder
            # to open, so the param must be in the browser URL, not just upstream.
            from urllib.parse import quote
            folder_qs = f"?folder={quote(str(workspace), safe='')}"
            raise web.HTTPFound(f"/code/{token}/{folder_qs}")

        # WebSocket upgrade
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_code_ws(request, target_url)

        return await self._proxy_code_http(request, target_url, token)

    async def _proxy_code_http(
        self, request: web.Request, target_url: str, token: str
    ) -> web.StreamResponse:
        """Forward HTTP request to code-server and return response."""
        if self._proxy_session is None or self._proxy_session.closed:
            self._proxy_session = aiohttp.ClientSession()

        # Forward headers (skip hop-by-hop and encoding negotiation)
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() not in (
                "host", "content-length", "transfer-encoding", "accept-encoding",
            ):
                headers[key] = value
        # Request uncompressed responses to avoid brotli/zstd decode issues
        headers["Accept-Encoding"] = "gzip, deflate"

        body = await request.read() if request.can_read_body else None

        try:
            async with self._proxy_session.request(
                request.method,
                target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as upstream:
                # Filter response headers
                resp_headers: dict[str, str] = {}
                for key, value in upstream.headers.items():
                    if key.lower() in (
                        "transfer-encoding",
                        "content-encoding",
                        "content-length",
                    ):
                        continue
                    resp_headers[key] = value

                content_type = upstream.headers.get("Content-Type", "")
                prefix = f"/code/{token}"

                if "text/html" in content_type:
                    # Read full body for HTML path rewriting
                    raw = await upstream.read()
                    text = raw.decode("utf-8", errors="replace")
                    # Inject <base> tag for relative URL resolution
                    if "<head>" in text:
                        text = text.replace(
                            "<head>", f'<head><base href="{prefix}/">', 1
                        )
                    elif "<HEAD>" in text:
                        text = text.replace(
                            "<HEAD>", f'<HEAD><base href="{prefix}/">', 1
                        )
                    else:
                        text = f'<base href="{prefix}/">' + text
                    # Remove Content-Type from forwarded headers to avoid conflict
                    html_headers = {
                        k: v for k, v in resp_headers.items()
                        if k.lower() != "content-type"
                    }
                    return web.Response(
                        body=text.encode("utf-8"),
                        status=upstream.status,
                        headers=html_headers,
                        content_type="text/html",
                        charset="utf-8",
                    )

                # Stream non-HTML response
                response = web.StreamResponse(
                    status=upstream.status, headers=resp_headers
                )
                if content_type:
                    response.content_type = content_type
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(32768):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except aiohttp.ClientError as e:
            logger.error("code-server proxy error: %s", e)
            return web.Response(text="Bad Gateway", status=502)

    async def _proxy_code_ws(
        self, request: web.Request, target_url: str
    ) -> web.WebSocketResponse:
        """Bridge WebSocket between browser and code-server."""
        ws_client = web.WebSocketResponse(
            protocols=request.headers.getall("Sec-WebSocket-Protocol", []),
        )
        await ws_client.prepare(request)

        ws_url = target_url.replace("http://", "ws://")

        if self._proxy_session is None or self._proxy_session.closed:
            self._proxy_session = aiohttp.ClientSession()

        try:
            async with self._proxy_session.ws_connect(
                ws_url,
                protocols=request.headers.getall("Sec-WebSocket-Protocol", []),
            ) as ws_upstream:

                async def client_to_server() -> None:
                    async for msg in ws_client:
                        if msg.type == WSMsgType.TEXT:
                            await ws_upstream.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await ws_upstream.send_bytes(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                async def server_to_client() -> None:
                    async for msg in ws_upstream:
                        if msg.type == WSMsgType.TEXT:
                            await ws_client.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await ws_client.send_bytes(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                await asyncio.gather(
                    client_to_server(),
                    server_to_client(),
                    return_exceptions=True,
                )
        except (aiohttp.ClientError, OSError) as e:
            logger.error("code-server WebSocket proxy error: %s", e)

        return ws_client

    # -- Port proxy (reverse proxy to local port) --

    async def _handle_port_redirect(self, request: web.Request) -> web.Response:
        """Redirect /port/{token} to /port/{token}/."""
        raise web.HTTPFound(f"/port/{request.match_info['token']}/")

    async def _handle_port_proxy(self, request: web.Request) -> web.StreamResponse:
        """Reverse proxy to a local port."""
        token = request.match_info["token"]
        path = request.match_info.get("path", "")

        # Extract port from token: token name contains "port:{N}"
        name = extract_token_name(token)
        if not name or not name.startswith("port:"):
            return _deny_response("invalid")
        try:
            port = int(name.split(":", 1)[1])
        except (ValueError, IndexError):
            return _deny_response("invalid")

        if not (1024 <= port <= 65535):
            return _deny_response("invalid")

        # Verify HMAC
        status = check_token(token, f"port:{port}")
        if status != "ok":
            return _deny_response(status)

        # Build target URL
        target_url = f"http://127.0.0.1:{port}/{path}"
        if request.query_string:
            target_url += f"?{request.query_string}"

        # WebSocket upgrade
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_port_ws(request, target_url)

        return await self._proxy_port_http(request, target_url, token)

    async def _proxy_port_http(
        self, request: web.Request, target_url: str, token: str
    ) -> web.StreamResponse:
        """Forward HTTP request to local port and return response."""
        if self._proxy_session is None or self._proxy_session.closed:
            self._proxy_session = aiohttp.ClientSession()

        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() not in (
                "host", "content-length", "transfer-encoding", "accept-encoding",
            ):
                headers[key] = value
        headers["Accept-Encoding"] = "gzip, deflate"

        body = await request.read() if request.can_read_body else None

        try:
            async with self._proxy_session.request(
                request.method,
                target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as upstream:
                resp_headers: dict[str, str] = {}
                for key, value in upstream.headers.items():
                    if key.lower() in (
                        "transfer-encoding",
                        "content-encoding",
                        "content-length",
                    ):
                        continue
                    resp_headers[key] = value

                content_type = upstream.headers.get("Content-Type", "")
                prefix = f"/port/{token}"

                if "text/html" in content_type:
                    raw = await upstream.read()
                    text = raw.decode("utf-8", errors="replace")
                    # Inject <base> tag for relative URL resolution
                    if "<head>" in text:
                        text = text.replace(
                            "<head>", f'<head><base href="{prefix}/">', 1
                        )
                    elif "<HEAD>" in text:
                        text = text.replace(
                            "<HEAD>", f'<HEAD><base href="{prefix}/">', 1
                        )
                    else:
                        text = f'<base href="{prefix}/">' + text
                    html_headers = {
                        k: v for k, v in resp_headers.items()
                        if k.lower() != "content-type"
                    }
                    return web.Response(
                        body=text.encode("utf-8"),
                        status=upstream.status,
                        headers=html_headers,
                        content_type="text/html",
                        charset="utf-8",
                    )

                # Stream non-HTML response
                response = web.StreamResponse(
                    status=upstream.status, headers=resp_headers
                )
                if content_type:
                    response.content_type = content_type
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(32768):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except aiohttp.ClientError as e:
            logger.error("port proxy error: %s", e)
            return web.Response(text="Bad Gateway", status=502)

    async def _proxy_port_ws(
        self, request: web.Request, target_url: str
    ) -> web.WebSocketResponse:
        """Bridge WebSocket between browser and local port."""
        ws_client = web.WebSocketResponse(
            protocols=request.headers.getall("Sec-WebSocket-Protocol", []),
        )
        await ws_client.prepare(request)

        ws_url = target_url.replace("http://", "ws://")

        if self._proxy_session is None or self._proxy_session.closed:
            self._proxy_session = aiohttp.ClientSession()

        try:
            async with self._proxy_session.ws_connect(
                ws_url,
                protocols=request.headers.getall("Sec-WebSocket-Protocol", []),
            ) as ws_upstream:

                async def client_to_server() -> None:
                    async for msg in ws_client:
                        if msg.type == WSMsgType.TEXT:
                            await ws_upstream.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await ws_upstream.send_bytes(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                async def server_to_client() -> None:
                    async for msg in ws_upstream:
                        if msg.type == WSMsgType.TEXT:
                            await ws_client.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await ws_client.send_bytes(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                await asyncio.gather(
                    client_to_server(),
                    server_to_client(),
                    return_exceptions=True,
                )
        except (aiohttp.ClientError, OSError) as e:
            logger.error("port proxy WebSocket error: %s", e)

        return ws_client

    # -- Hub (all-in-one dashboard) --

    def _verify_hub_token(self, token: str) -> tuple[dict[str, str] | None, str]:
        """Verify hub token and extract payload components.

        Hub token payload: hub:{workspace_path}:{tmux_session}:{window_id}
        Token name: {tmux_session}:{window_id}
        Returns (payload_dict, status). payload_dict has keys:
          workspace, tmux_session, window_id
        """
        name = extract_token_name(token)
        if not name:
            return None, "invalid"

        # The name is "{tmux_session}:{window_id}" or "{tmux_session}:"
        parts = name.split(":", 1)
        tmux_session = parts[0] if parts else ""
        window_id = parts[1] if len(parts) > 1 else ""

        # Try each workspace root's agent dir for matching workspace_ dirs
        worst = "invalid"
        checked_dirs: set[str] = set()
        for ws_root in self._workspace_roots:
            agent_dir = ws_root.parent
            if not agent_dir.is_dir():
                continue
            agent_key = str(agent_dir)
            if agent_key in checked_dirs:
                continue
            checked_dirs.add(agent_key)
            for ws_dir in agent_dir.iterdir():
                if not ws_dir.is_dir() or not ws_dir.name.startswith("workspace_"):
                    continue
                ws_path = str(ws_dir.resolve())
                payload = f"hub:{ws_path}:{name}"
                status = check_token(token, payload)
                if status == "ok":
                    return {
                        "workspace": ws_path,
                        "tmux_session": tmux_session,
                        "window_id": window_id,
                    }, "ok"
                if status == "expired":
                    worst = "expired"

        return None, worst

    async def _handle_hub_redirect(self, request: web.Request) -> web.Response:
        """Redirect /hub/{token} to /hub/{token}/."""
        token = request.match_info["token"]
        raise web.HTTPFound(f"/hub/{token}/")

    async def _handle_hub_page(self, request: web.Request) -> web.Response:
        """Serve the hub dashboard page."""
        token = request.match_info["token"]
        payload, status = self._verify_hub_token(token)
        if payload is None:
            return _deny_response(status)

        ws_path = payload["workspace"]
        display = Path(ws_path).name.removeprefix("workspace_")

        # Compute remaining TTL for the JS countdown
        try:
            parts = token.split("-", 2)
            expires = int(parts[1])
            remaining = max(0, expires - int(time.time()))
        except (ValueError, IndexError):
            remaining = 600

        html = _HUB_HTML.replace("{{NAME}}", display).replace(
            "{{TTL}}", str(remaining)
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_hub_urls(self, request: web.Request) -> web.Response:
        """Return JSON with sub-URLs for each tool."""
        token = request.match_info["token"]
        payload, status = self._verify_hub_token(token)
        if payload is None:
            return web.json_response({"error": status}, status=403)

        ws_path = payload["workspace"]
        tmux_session = payload["tmux_session"]
        window_id = payload["window_id"]

        # Compute remaining TTL from the hub token
        try:
            parts = token.split("-", 2)
            expires = int(parts[1])
            remaining = max(60, expires - int(time.time()))
        except (ValueError, IndexError):
            remaining = 600

        display = Path(ws_path).name.removeprefix("workspace_")
        urls: dict[str, str] = {}

        # Browse
        browse_token = generate_token(f"p:{ws_path}:", ttl=remaining, name=display)
        urls["browse"] = f"/p/{browse_token}/"

        # Upload
        upload_token = generate_token(
            f"upload:{ws_path}", ttl=remaining, name=display
        )
        urls["upload"] = f"/u/{upload_token}"

        # Terminal
        term_token = generate_token(
            f"term:{ws_path}", ttl=remaining, name=display
        )
        urls["term"] = f"/term/{term_token}/"

        # Tmux (topic window)
        if tmux_session and window_id:
            tmux_payload = f"tmux:{tmux_session}:{window_id}"
            tmux_name = f"{tmux_session}:{window_id}"
            tmux_token = generate_token(
                tmux_payload, ttl=remaining, name=tmux_name
            )
            urls["tmux"] = f"/tmux/{tmux_token}/"

        # Log (main window)
        if tmux_session:
            log_payload = f"log:{tmux_session}"
            log_token = generate_token(
                log_payload, ttl=remaining, name=tmux_session
            )
            urls["log"] = f"/tmux/{log_token}/"

        # Code (VS Code Web)
        ws_resolved = Path(ws_path).resolve()
        projects_dir = ws_resolved / "projects"
        # Only use projects/ if it has actual contents
        code_dir = str(
            projects_dir
            if projects_dir.is_dir() and any(projects_dir.iterdir())
            else ws_resolved
        )
        code_token = generate_token(
            f"code:{code_dir}", ttl=remaining, name=code_dir
        )
        urls["code"] = f"/code/{code_token}/"

        # TODO management
        todo_token = generate_token(
            f"todo:{ws_path}", ttl=remaining, name=display
        )
        urls["todo"] = f"/todo/{todo_token}/"

        # Cron management
        cron_token = generate_token(
            f"cron:{ws_path}", ttl=remaining, name=display
        )
        urls["cron"] = f"/cron/{cron_token}/"

        return web.json_response(urls)

    async def _handle_hub_stats(self, request: web.Request) -> web.Response:
        """Return dashboard statistics for the hub home page."""
        token = request.match_info["token"]
        payload, status = self._verify_hub_token(token)
        if payload is None:
            return web.json_response({"error": status}, status=403)

        ws_path = Path(payload["workspace"])
        stats: dict[str, object] = {}

        # TODO stats
        import sqlite3 as _sqlite3

        db_path = ws_path / "memory.db"
        if db_path.exists():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            try:
                # TODOs
                has_todos = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='todos'"
                ).fetchone()
                if has_todos:
                    todo_open = conn.execute(
                        "SELECT COUNT(*) FROM todos WHERE status='open'"
                    ).fetchone()[0]
                    today = time.strftime("%Y-%m-%d")
                    todo_overdue = conn.execute(
                        "SELECT COUNT(*) FROM todos WHERE status='open' AND deadline < ?",
                        (today,),
                    ).fetchone()[0]
                    todo_done = conn.execute(
                        "SELECT COUNT(*) FROM todos WHERE status='done'"
                    ).fetchone()[0]
                    stats["todo"] = {
                        "open": todo_open,
                        "overdue": todo_overdue,
                        "done": todo_done,
                    }

                # Cron
                has_cron = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_jobs'"
                ).fetchone()
                if has_cron:
                    cron_active = conn.execute(
                        "SELECT COUNT(*) FROM cron_jobs WHERE enabled=1"
                    ).fetchone()[0]
                    cron_total = conn.execute(
                        "SELECT COUNT(*) FROM cron_jobs"
                    ).fetchone()[0]
                    stats["cron"] = {
                        "active": cron_active,
                        "total": cron_total,
                    }
            finally:
                conn.close()

        # Memory stats
        memory_dir = ws_path / "memory"
        daily_count = 0
        experience_count = 0
        if memory_dir.is_dir():
            daily_dir = memory_dir / "daily"
            if daily_dir.is_dir():
                for month_dir in daily_dir.iterdir():
                    if month_dir.is_dir():
                        daily_count += sum(
                            1 for f in month_dir.iterdir() if f.suffix == ".md"
                        )
            exp_dir = memory_dir / "experience"
            if exp_dir.is_dir():
                experience_count = sum(
                    1 for f in exp_dir.iterdir() if f.suffix == ".md"
                )
        stats["memory"] = {"daily": daily_count, "experience": experience_count}

        # Workspace disk usage
        try:
            result = subprocess.run(
                ["du", "-sm", str(ws_path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                mb = int(result.stdout.split()[0])
                if mb >= 1024:
                    stats["disk"] = f"{mb / 1024:.1f} GB"
                else:
                    stats["disk"] = f"{mb} MB"
        except Exception:
            pass

        # Projects (scan projects/ directory, up to 2 levels deep, nested)
        projects_dir = ws_path / "projects"
        projects: list[dict[str, object]] = []
        if projects_dir.is_dir():
            for entry in sorted(projects_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                is_git = (entry / ".git").exists()
                item: dict[str, object] = {"name": entry.name, "git": is_git}
                if not is_git:
                    children: list[dict[str, object]] = []
                    for sub in sorted(entry.iterdir()):
                        if not sub.is_dir() or sub.name.startswith("."):
                            continue
                        children.append({
                            "name": sub.name,
                            "git": (sub / ".git").exists(),
                        })
                    if children:
                        item["children"] = children
                projects.append(item)
        if projects:
            stats["projects"] = projects

        return web.json_response(stats)

    # -- TODO management --

    def _verify_todo_token(self, token: str) -> tuple[Path | None, str]:
        """Verify a todo token and return (workspace_path, status)."""
        worst = "invalid"
        for root in self._workspace_roots:
            status = check_token(token, f"todo:{root}")
            if status == "ok":
                return root, "ok"
            if status == "expired":
                worst = "expired"
        return None, worst

    def _get_todo_db(self, workspace: Path):
        """Get a todo DB connection for the workspace."""
        import sqlite3 as _sqlite3

        from .workspace.bin._todo_common import connect_db

        return connect_db(workspace)

    async def _handle_todo_redirect(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        raise web.HTTPFound(f"/todo/{token}/")

    async def _handle_todo_page(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return _deny_response(status)

        # Extract language from token name (format: "lang:xx-YY" or just workspace name)
        name = extract_token_name(token)
        lang = "en"
        if name.startswith("lang:"):
            lang = name[5:]

        html = _TODO_HTML.replace("{{LANG}}", lang)
        return web.Response(text=html, content_type="text/html")

    async def _handle_todo_list(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        conn = self._get_todo_db(workspace)
        try:
            from .workspace.bin._todo_common import list_todos

            rows = list_todos(conn, status="all")
            result = []
            ws_root = str(workspace.resolve())
            # Compute remaining TTL from the todo token
            try:
                parts = token.split("-", 2)
                remaining = max(60, int(parts[1]) - int(time.time()))
            except (ValueError, IndexError):
                remaining = 600
            for r in rows:
                d = dict(r)
                # Generate file share URLs for attachments
                atts_raw = d.get("attachments", "[]")
                atts = json.loads(atts_raw) if isinstance(atts_raw, str) else atts_raw
                if atts:
                    att_urls = []
                    memory_dir = workspace / "memory"
                    for att_path in atts:
                        full = (memory_dir / att_path).resolve()
                        if full.is_file():
                            rel = str(full.relative_to(workspace.resolve()))
                            file_token = generate_token(
                                f"f:{ws_root}:{rel}", ttl=remaining
                            )
                            att_urls.append({
                                "name": Path(att_path).name,
                                "path": att_path,
                                "url": f"/f/{file_token}/{rel}",
                            })
                        else:
                            att_urls.append({
                                "name": Path(att_path).name,
                                "path": att_path,
                                "url": "",
                            })
                    d["attachment_urls"] = att_urls
                else:
                    d["attachment_urls"] = []
                result.append(d)
            return web.json_response(result)
        finally:
            conn.close()

    async def _handle_todo_add(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        data = await request.json()
        title = data.get("title", "").strip()
        if not title:
            return web.json_response({"error": "title required"}, status=400)

        conn = self._get_todo_db(workspace)
        try:
            from .workspace.bin._todo_common import add_todo

            todo_id = add_todo(
                conn,
                title,
                todo_type=data.get("type", "task"),
                start_date=data.get("start_date"),
                deadline=data.get("deadline"),
                location=data.get("location", ""),
                content=data.get("content", ""),
            )
            return web.json_response({"id": todo_id})
        finally:
            conn.close()

    async def _handle_todo_update(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        todo_id = request.match_info["todo_id"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        data = await request.json()
        conn = self._get_todo_db(workspace)
        try:
            from .workspace.bin._todo_common import update_todo

            fields = {}
            for key in ("title", "type", "start_date", "deadline", "location", "content", "status"):
                if key in data:
                    fields[key] = data[key] or ""
            ok = update_todo(conn, todo_id, **fields)
            if not ok:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"ok": True})
        finally:
            conn.close()

    async def _handle_todo_delete(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        todo_id = request.match_info["todo_id"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        conn = self._get_todo_db(workspace)
        try:
            from .workspace.bin._todo_common import remove_todo

            ok = remove_todo(conn, todo_id)
            if not ok:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"ok": True})
        finally:
            conn.close()

    async def _handle_todo_done(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        todo_id = request.match_info["todo_id"]
        workspace, status = self._verify_todo_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        conn = self._get_todo_db(workspace)
        try:
            from .workspace.bin._todo_common import done_todo

            ok = done_todo(conn, todo_id)
            if not ok:
                return web.json_response({"error": "not found or already done"}, status=404)
            return web.json_response({"ok": True})
        finally:
            conn.close()

    # -- Cron management --

    def _verify_cron_token(self, token: str) -> tuple[Path | None, str]:
        """Verify a cron token and return (workspace_path, status)."""
        worst = "invalid"
        for root in self._workspace_roots:
            status = check_token(token, f"cron:{root}")
            if status == "ok":
                return root, "ok"
            if status == "expired":
                worst = "expired"
        return None, worst

    async def _handle_cron_redirect(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        raise web.HTTPFound(f"/cron/{token}/")

    async def _handle_cron_page(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        workspace, status = self._verify_cron_token(token)
        if workspace is None:
            return _deny_response(status)

        name = extract_token_name(token)
        lang = "en"
        if name.startswith("lang:"):
            lang = name[5:]

        html = _CRON_HTML.replace("{{LANG}}", lang)
        return web.Response(text=html, content_type="text/html")

    async def _handle_cron_list(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        workspace, status = self._verify_cron_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        import sqlite3 as _sqlite3

        db_path = workspace / "memory.db"
        if not db_path.exists():
            return web.json_response({"jobs": [], "history": {}})

        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        try:
            # Check if cron_jobs table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_jobs'"
            ).fetchone()
            if not table_check:
                return web.json_response({"jobs": [], "history": {}})

            rows = conn.execute(
                "SELECT * FROM cron_jobs ORDER BY created_at"
            ).fetchall()
            jobs = []
            for r in rows:
                jobs.append({
                    "id": r["id"],
                    "name": r["name"],
                    "schedule": {
                        "kind": r["schedule_kind"],
                        "expr": r["schedule_expr"],
                        "tz": r["schedule_tz"],
                        "every_seconds": r["schedule_every_s"],
                        "at": r["schedule_at"],
                    },
                    "message": r["message"],
                    "enabled": bool(r["enabled"]),
                    "delete_after_run": bool(r["delete_after_run"]),
                    "system": bool(r["system"]),
                    "creator_user_id": r["creator_user_id"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "state": {
                        "next_run_at": r["next_run_at"],
                        "running_at": r["running_at"],
                        "last_run_at": r["last_run_at"],
                        "last_status": r["last_status"],
                        "last_error": r["last_error"],
                        "last_duration_s": r["last_duration_s"],
                        "consecutive_errors": r["consecutive_errors"],
                    },
                })

            # Load recent history (last 20 per job)
            hist: dict[str, list] = {}
            table_check2 = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_history'"
            ).fetchone()
            if table_check2:
                for job in jobs:
                    h_rows = conn.execute(
                        "SELECT * FROM cron_history WHERE job_id = ? "
                        "ORDER BY started_at DESC LIMIT 20",
                        (job["id"],),
                    ).fetchall()
                    if h_rows:
                        hist[job["id"]] = [
                            {
                                "started_at": h["started_at"],
                                "finished_at": h["finished_at"],
                                "status": h["status"],
                                "error": h["error"],
                                "duration_s": h["duration_s"],
                            }
                            for h in h_rows
                        ]

            return web.json_response({"jobs": jobs, "history": hist})
        finally:
            conn.close()

    async def _handle_cron_toggle(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        job_id = request.match_info["job_id"]
        workspace, status = self._verify_cron_token(token)
        if workspace is None:
            return web.json_response({"error": status}, status=403)

        data = await request.json()
        enabled = bool(data.get("enabled", True))

        import sqlite3 as _sqlite3

        db_path = workspace / "memory.db"
        if not db_path.exists():
            return web.json_response({"error": "not found"}, status=404)

        conn = _sqlite3.connect(str(db_path))
        try:
            result = conn.execute(
                "UPDATE cron_jobs SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, job_id),
            )
            conn.commit()
            if result.rowcount == 0:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"ok": True})
        finally:
            conn.close()

    # -- Server lifecycle --

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await site.start()
        logger.info("Share server listening on http://localhost:%d", self._port)

    async def stop(self) -> None:
        await self._code_manager.stop_all()
        if self._proxy_session and not self._proxy_session.closed:
            await self._proxy_session.close()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Share server stopped")
