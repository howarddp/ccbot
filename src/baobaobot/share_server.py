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

import hashlib
import hmac
import html as html_mod
import logging
import mimetypes
import os
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

# Token format: {signature_hex[:32]}-{expires_timestamp}
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


def generate_token(path: str, ttl: int = _DEFAULT_TTL, secret: str = "") -> str:
    """Generate a signed token for a path with expiry."""
    if not secret:
        secret = _load_secret()
    expires = int(time.time()) + ttl
    msg = f"{path}:{expires}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    return f"{sig}-{expires}"


def verify_token(token: str, path: str, secret: str = "") -> bool:
    """Verify a token is valid and not expired."""
    if not secret:
        secret = _load_secret()
    try:
        sig, expires_str = token.rsplit("-", 1)
        expires = int(expires_str)
    except (ValueError, AttributeError):
        return False
    if time.time() > expires:
        return False
    msg = f"{path}:{expires}"
    expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    return hmac.compare_digest(sig, expected)


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
# Upload page HTML (mobile-friendly)
# ---------------------------------------------------------------------------

_UPLOAD_HTML = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload Files</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #f5f5f5;
         padding: 20px; max-width: 600px; margin: 0 auto; }
  h1 { font-size: 1.4em; margin-bottom: 16px; color: #333; }
  .drop-zone { border: 2px dashed #ccc; border-radius: 12px; padding: 40px 20px;
               text-align: center; color: #888; cursor: pointer; transition: .2s;
               background: #fff; margin-bottom: 16px; }
  .drop-zone.drag { border-color: #4a9eff; background: #f0f7ff; color: #4a9eff; }
  .drop-zone input { display: none; }
  .file-list { margin-bottom: 16px; }
  .file-item { display: flex; justify-content: space-between; align-items: center;
               padding: 8px 12px; background: #fff; border-radius: 8px;
               margin-bottom: 6px; font-size: 0.9em; }
  .file-item .name { flex: 1; overflow: hidden; text-overflow: ellipsis;
                     white-space: nowrap; margin-right: 8px; }
  .file-item .size { color: #888; font-size: 0.85em; white-space: nowrap; }
  .file-item .remove { color: #e55; cursor: pointer; margin-left: 8px;
                       font-size: 1.1em; padding: 0 4px; }
  textarea { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 8px;
             font-size: 1em; resize: vertical; min-height: 60px; margin-bottom: 16px; }
  button { width: 100%; padding: 14px; background: #4a9eff; color: #fff; border: none;
           border-radius: 8px; font-size: 1.1em; cursor: pointer; }
  button:disabled { background: #ccc; }
  .progress { display: none; margin-top: 12px; }
  .progress-bar { height: 6px; background: #e0e0e0; border-radius: 3px; overflow: hidden; }
  .progress-fill { height: 100%; background: #4a9eff; width: 0%; transition: width .3s; }
  .status { text-align: center; margin-top: 12px; color: #888; }
  .done { color: #4caf50; font-size: 1.2em; text-align: center; margin-top: 20px; }
  .expired { color: #e55; font-size: 1.2em; text-align: center; margin-top: 40px; }
</style>
</head>
<body>
<div id="app">
  <h1>Upload Files</h1>
  <div class="drop-zone" id="dropZone">
    <p>Tap to select or drag files here</p>
    <input type="file" id="fileInput" multiple>
  </div>
  <div class="file-list" id="fileList"></div>
  <textarea id="description" placeholder="Description (optional)"></textarea>
  <button id="uploadBtn" disabled>Upload</button>
  <div class="progress" id="progress">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="status" id="statusText">Uploading...</div>
  </div>
</div>
<script>
const token = location.pathname.split('/')[2];
const dz = document.getElementById('dropZone');
const fi = document.getElementById('fileInput');
const fl = document.getElementById('fileList');
const btn = document.getElementById('uploadBtn');
let files = [];

function formatSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
}

function render() {
  fl.innerHTML = '';
  files.forEach((f, i) => {
    const d = document.createElement('div');
    d.className = 'file-item';
    d.innerHTML = `<span class="name">${f.name}</span><span class="size">${formatSize(f.size)}</span><span class="remove" data-i="${i}">&times;</span>`;
    fl.appendChild(d);
  });
  btn.disabled = files.length === 0;
}

function addFiles(newFiles) {
  for (const f of newFiles) files.push(f);
  render();
}

dz.addEventListener('click', () => fi.click());
fi.addEventListener('change', () => { addFiles(fi.files); fi.value = ''; });
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); addFiles(e.dataTransfer.files); });
fl.addEventListener('click', e => {
  if (e.target.classList.contains('remove')) { files.splice(+e.target.dataset.i, 1); render(); }
});

btn.addEventListener('click', async () => {
  btn.disabled = true;
  const pg = document.getElementById('progress');
  const pf = document.getElementById('progressFill');
  const st = document.getElementById('statusText');
  pg.style.display = 'block';

  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  fd.append('description', document.getElementById('description').value);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/u/${token}/upload`);
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) pf.style.width = (e.loaded/e.total*100)+'%';
  };
  xhr.onload = () => {
    if (xhr.status === 200) {
      document.getElementById('app').innerHTML = '<div class="done">Upload complete!</div>';
    } else {
      st.textContent = 'Upload failed: ' + xhr.statusText;
      btn.disabled = false;
    }
  };
  xhr.onerror = () => { st.textContent = 'Network error'; btn.disabled = false; };
  xhr.send(fd);
});
</script>
</body>
</html>
"""

_EXPIRED_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link Expired</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;
align-items:center;min-height:80vh;color:#999;}</style>
</head><body><h2>This link has expired.</h2></body></html>
"""


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


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
    ) -> Path | None:
        """Try to verify token against each registered workspace root.

        Token payload format: '{prefix}:{workspace_abs}:{rel_path}'
        Returns the matched workspace Path, or None if no match.
        """
        for root in self._workspace_roots:
            token_path = f"{prefix}:{root}:{rel_path}"
            if verify_token(token, token_path):
                return root
        return None

    def _verify_upload_workspace(self, token: str) -> Path | None:
        """Try to verify upload token against each registered workspace root.

        Token payload format: 'upload:{workspace_abs}'
        Returns the matched workspace Path, or None if no match.
        """
        for root in self._workspace_roots:
            if verify_token(token, f"upload:{root}"):
                return root
        return None

    def add_workspace(self, workspace: Path) -> None:
        """Register a workspace root dynamically (e.g., when a new topic is created)."""
        ws = workspace.resolve()
        if ws not in [r.resolve() for r in self._workspace_roots]:
            self._workspace_roots.append(ws)
            logger.info("Registered workspace root: %s", ws)

    # -- File download/preview --

    async def _handle_file(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        path = request.match_info["path"]

        # Try workspace-aware verification first
        workspace = self._verify_with_workspace(token, "f", path)
        if workspace is None:
            # Fall back to legacy format (no workspace)
            if not verify_token(token, f"f:{path}"):
                return web.Response(text=_EXPIRED_HTML, content_type="text/html", status=410)

        file_path = self._find_file(path, workspace)
        if not file_path:
            raise web.HTTPNotFound()

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"

        # Inline display for images and PDFs; HTML and others as attachment
        _INLINE_TYPES = ("image/", "application/pdf")
        disposition = "inline" if content_type.startswith(_INLINE_TYPES) else "attachment"

        # RFC 6266 safe filename encoding
        safe_filename = urllib.parse.quote(file_path.name, safe="")

        headers = {
            "Content-Type": content_type,
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{safe_filename}",
            "X-Content-Type-Options": "nosniff",
        }
        # Add CSP for HTML files (served as attachment but belt-and-suspenders)
        if content_type.startswith("text/html"):
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:;"

        return web.FileResponse(file_path, headers=headers)

    # -- Directory preview --

    async def _handle_preview(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        path = request.match_info.get("path", "")

        # 1. Exact path verification
        workspace = self._verify_with_workspace(token, "p", path)

        # 2. Parent path backtracking: directory token grants access to sub-paths
        if workspace is None:
            parent = path
            while "/" in parent:
                parent = parent.rsplit("/", 1)[0]
                workspace = self._verify_with_workspace(token, "p", parent)
                if workspace:
                    break

        # 3. Legacy fallback (no workspace in token) with parent backtracking
        if workspace is None:
            if not verify_token(token, f"p:{path}"):
                parent = path
                verified = False
                while "/" in parent:
                    parent = parent.rsplit("/", 1)[0]
                    if verify_token(token, f"p:{parent}"):
                        verified = True
                        break
                if not verified:
                    return web.Response(text=_EXPIRED_HTML, content_type="text/html", status=410)

        dir_path = self._find_dir(path, workspace)
        if not dir_path:
            # Path might be a file under a shared directory — serve it directly
            file_path = self._find_file(path, workspace)
            if file_path:
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
                    headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:;"
                return web.FileResponse(file_path, headers=headers)
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

        # Generate directory listing — escape all dynamic values
        safe_token = html_mod.escape(token)
        safe_path = html_mod.escape(path)
        items = sorted(dir_path.iterdir())
        listing = "\n".join(
            f'<li><a href="/p/{safe_token}/{safe_path}/{html_mod.escape(item.name)}">{html_mod.escape(item.name)}{"/" if item.is_dir() else ""}</a></li>'
            for item in items
            if not item.name.startswith(".")
        )
        dir_title = html_mod.escape(Path(path).name or "Files")
        page_html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Files</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;padding:20px;max-width:600px;margin:0 auto;}}
li{{padding:4px 0;}}</style>
</head><body><h2>{dir_title}</h2><ul>{listing}</ul></body></html>
"""
        return web.Response(
            text=page_html,
            content_type="text/html",
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline';",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # -- Upload page --

    async def _handle_upload_page(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]

        # Verify: workspace-aware or legacy
        workspace = self._verify_upload_workspace(token)
        if workspace is None and not verify_token(token, "upload"):
            return web.Response(text=_EXPIRED_HTML, content_type="text/html", status=410)

        return web.Response(text=_UPLOAD_HTML, content_type="text/html")

    # -- Upload handler --

    async def _handle_upload(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]

        # Verify and determine target workspace
        workspace = self._verify_upload_workspace(token)
        if workspace is None:
            if not verify_token(token, "upload"):
                return web.Response(text=_EXPIRED_HTML, content_type="text/html", status=410)
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
