"""Cloudflare Tunnel manager — auto-downloads cloudflared and runs quick tunnel.

Provides a public URL for the local HTTP share server via Cloudflare's
free quick tunnel (*.trycloudflare.com). Auto-downloads the cloudflared
binary if not found on PATH or in ~/.baobaobot/bin/.

Future: when CF_API_TOKEN + CF_TUNNEL_DOMAIN are set in .env, switches
to a named tunnel with a fixed domain.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import shutil
import stat
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_CLOUDFLARED_DIR = Path.home() / ".baobaobot" / "bin"
_CLOUDFLARED_PATH = _CLOUDFLARED_DIR / "cloudflared"

# Regex to extract the public URL from cloudflared stderr
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _resolve_cloudflared() -> str | None:
    """Find cloudflared binary: PATH → ~/.baobaobot/bin/ → None."""
    path_bin = shutil.which("cloudflared")
    if path_bin:
        return path_bin
    if _CLOUDFLARED_PATH.is_file():
        return str(_CLOUDFLARED_PATH)
    return None


def _download_url() -> str:
    """Build the download URL for the current platform."""
    system = platform.system().lower()  # darwin / linux
    machine = platform.machine().lower()  # arm64 / x86_64 / aarch64

    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    if system == "darwin":
        # macOS: single binary (no tar)
        return f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{arch}.tgz"
    elif system == "linux":
        return f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")


def _download_cloudflared() -> str:
    """Download cloudflared binary to ~/.baobaobot/bin/."""
    url = _download_url()
    _CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading cloudflared from %s ...", url)

    if url.endswith(".tgz"):
        # macOS: download tar and extract safely (no tar.extract to prevent symlink attacks)
        import io
        import tarfile
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".tgz", delete=False)
        tgz_path = Path(tmp.name)
        tmp.close()
        try:
            urllib.request.urlretrieve(url, tgz_path)
            with tarfile.open(tgz_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name == "cloudflared" or member.name.endswith(
                        "/cloudflared"
                    ):
                        # Reject symlinks/hardlinks
                        if member.issym() or member.islnk():
                            raise RuntimeError(
                                "cloudflared tar member is a symlink — refusing to extract"
                            )
                        # Safe extraction: read content and write manually
                        f_obj = tar.extractfile(member)
                        if f_obj is None:
                            raise RuntimeError("Could not read cloudflared from archive")
                        with open(_CLOUDFLARED_PATH, "wb") as out:
                            while True:
                                chunk = f_obj.read(65536)
                                if not chunk:
                                    break
                                out.write(chunk)
                        break
                else:
                    raise RuntimeError("cloudflared not found in tar archive")
        finally:
            tgz_path.unlink(missing_ok=True)
    else:
        # Linux: direct binary download
        urllib.request.urlretrieve(url, _CLOUDFLARED_PATH)

    _CLOUDFLARED_PATH.chmod(_CLOUDFLARED_PATH.stat().st_mode | stat.S_IEXEC)
    logger.info("cloudflared installed at %s", _CLOUDFLARED_PATH)
    return str(_CLOUDFLARED_PATH)


def ensure_cloudflared() -> str:
    """Return path to cloudflared, downloading if needed."""
    existing = _resolve_cloudflared()
    if existing:
        return existing
    return _download_cloudflared()


async def ensure_cloudflared_async() -> str:
    """Non-blocking version of ensure_cloudflared (runs download in thread)."""
    existing = _resolve_cloudflared()
    if existing:
        return existing
    return await asyncio.to_thread(_download_cloudflared)


class TunnelManager:
    """Manages a cloudflared quick tunnel subprocess with auto-restart."""

    def __init__(
        self,
        local_port: int = 8787,
        on_url_change: Callable[[str], None] | None = None,
    ) -> None:
        self._local_port = local_port
        self._on_url_change = on_url_change
        self._process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._monitor_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stopping = False

    @property
    def public_url(self) -> str | None:
        return self._public_url

    async def start(self) -> str:
        """Start the tunnel and return the public URL."""
        self._stopping = False
        cloudflared = await ensure_cloudflared_async()
        logger.info(
            "Starting cloudflared quick tunnel on port %d...", self._local_port
        )

        self._process = await asyncio.create_subprocess_exec(
            cloudflared,
            "tunnel",
            "--url",
            f"http://localhost:{self._local_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._monitor_task = asyncio.create_task(self._read_stderr())

        # Wait for URL to appear (timeout 30s)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for cloudflared URL")
            await self.stop()
            raise RuntimeError("cloudflared failed to start within 30 seconds")

        logger.info("Tunnel active: %s", self._public_url)
        return self._public_url  # type: ignore[return-value]

    async def _read_stderr(self) -> None:
        """Read cloudflared stderr to extract the public URL and log output."""
        assert self._process and self._process.stderr
        while True:
            line_bytes = await self._process.stderr.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line:
                logger.debug("[cloudflared] %s", line)

            if not self._public_url:
                match = _URL_RE.search(line)
                if match:
                    self._public_url = match.group(0)
                    self._ready.set()

        # Process exited — auto-restart if not intentionally stopped
        rc = self._process.returncode if self._process else None
        if rc is not None:
            logger.warning("cloudflared exited with code %d", rc)

        if not self._stopping:
            asyncio.create_task(self._auto_restart())

    async def _auto_restart(self) -> None:
        """Restart the tunnel with exponential backoff."""
        delays = [2, 5, 10, 30, 60]
        for attempt, delay in enumerate(delays):
            if self._stopping:
                return
            logger.info(
                "Auto-restarting cloudflared in %ds (attempt %d/%d)...",
                delay, attempt + 1, len(delays),
            )
            await asyncio.sleep(delay)
            if self._stopping:
                return
            try:
                self._public_url = None
                self._ready.clear()
                new_url = await self.start()
                if self._on_url_change:
                    self._on_url_change(new_url)
                logger.info("Tunnel auto-restarted: %s", new_url)
                return
            except Exception:
                logger.exception("Auto-restart attempt %d failed", attempt + 1)
        logger.error("All auto-restart attempts exhausted, tunnel is down")

    async def stop(self) -> None:
        """Stop the tunnel subprocess."""
        self._stopping = True
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("cloudflared stopped")
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._process = None
        self._public_url = None
        self._ready.clear()

    async def restart(self) -> str:
        """Restart the tunnel (URL will change)."""
        await self.stop()
        return await self.start()
