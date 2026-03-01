"""HTTP file server for sharing files and receiving uploads.

Serves workspace files via signed URLs and provides upload pages.
Designed to sit behind a Cloudflare quick tunnel.

Routes:
  GET  /f/{token}/{path}     — file download/preview
  GET  /p/{token}/           — directory preview (index.html or listing)
  GET  /u/{token}            — upload page
  POST /u/{token}/upload     — receive uploaded files
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_mod
import json
import logging
import mimetypes
import os
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

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


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


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
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/f/{token}/{path:.*}", self._handle_file)
        self._app.router.add_get("/p/{token}/{path:.*}", self._handle_preview)
        self._app.router.add_get("/u/{token}", self._handle_upload_page)
        self._app.router.add_post("/u/{token}/upload", self._handle_upload)

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
            if item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                items_data.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if not item.is_dir() else None,
                    "mtime": int(stat.st_mtime),
                })
            except OSError:
                items_data.append({"name": item.name, "is_dir": item.is_dir(), "size": None, "mtime": None})

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

    # -- Server lifecycle --

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await site.start()
        logger.info("Share server listening on http://localhost:%d", self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Share server stopped")
