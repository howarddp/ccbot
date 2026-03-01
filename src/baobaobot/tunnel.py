"""Cloudflare Tunnel manager — auto-downloads cloudflared and runs quick tunnel.

Provides a public URL for the local HTTP share server via Cloudflare's
free quick tunnel (*.trycloudflare.com). Auto-downloads the cloudflared
binary if not found on PATH or in ~/.baobaobot/bin/.

Supports tunnel reuse across bot restarts via a state file, and infinite
background retry when all initial restart attempts are exhausted.

Future: when CF_API_TOKEN + CF_TUNNEL_DOMAIN are set in .env, switches
to a named tunnel with a fixed domain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_CLOUDFLARED_DIR = Path.home() / ".baobaobot" / "bin"
_CLOUDFLARED_PATH = _CLOUDFLARED_DIR / "cloudflared"

# Regex to extract the public URL from cloudflared stderr
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Default tunnel state file location
_DEFAULT_STATE_FILE = Path.home() / ".baobaobot" / ".tunnel_state.json"

# Background retry interval after initial exponential backoff exhausted
_BACKGROUND_RETRY_INTERVAL = 600  # 10 minutes


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


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_cloudflared_process(pid: int) -> bool:
    """Check if the process is actually cloudflared (not a recycled PID)."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=5,
        )
        return "cloudflared" in result.stdout.strip()
    except Exception:
        return False


async def _check_url_healthy(url: str, timeout: float = 5.0) -> bool:
    """Quick HTTP check to verify the tunnel URL is reachable."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            # Any response (even 404) means cloudflared is proxying
            return resp.status_code < 600
    except Exception:
        return False


def _find_pid_on_port(port: int) -> int | None:
    """Find PID of process listening on the given port, if it's cloudflared."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        # Could be multiple PIDs — find the cloudflared one
        for pid_str in result.stdout.strip().split("\n"):
            pid = int(pid_str.strip())
            if _is_cloudflared_process(pid):
                return pid
    except Exception:
        logger.debug("Failed to find PID on port %d", port, exc_info=True)
    return None


class TunnelManager:
    """Manages a cloudflared quick tunnel subprocess with auto-restart."""

    def __init__(
        self,
        local_port: int = 8787,
        on_url_change: Callable[[str], None] | None = None,
        state_file: Path | None = None,
    ) -> None:
        self._local_port = local_port
        self._on_url_change = on_url_change
        self._process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._monitor_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stopping = False
        self._state_file = state_file or _DEFAULT_STATE_FILE
        # Track adopted process PID (when we didn't spawn it ourselves)
        self._adopted_pid: int | None = None
        # Prevent nested auto-restart loops
        self._auto_restarting = False

    @property
    def public_url(self) -> str | None:
        return self._public_url

    async def start(self) -> str:
        """Start the tunnel and return the public URL.

        Tries to adopt an existing healthy tunnel first, then falls back
        to spawning a new cloudflared process. Handles port conflicts by
        killing orphaned cloudflared processes.
        """
        self._stopping = False

        # Try to adopt an existing tunnel from a previous bot instance
        adopted_url = await self._try_adopt()
        if adopted_url:
            return adopted_url

        # Spawn a new cloudflared process
        try:
            return await self._spawn()
        except Exception as exc:
            # Check for port conflict — kill orphaned cloudflared and retry once
            err_msg = str(exc).lower()
            if "address already in use" in err_msg or "bind" in err_msg:
                orphan_pid = await asyncio.to_thread(_find_pid_on_port, self._local_port)
                if orphan_pid:
                    logger.warning(
                        "Port %d in use by orphaned cloudflared (PID %d), killing it",
                        self._local_port, orphan_pid,
                    )
                    try:
                        os.kill(orphan_pid, signal.SIGTERM)
                        await asyncio.sleep(1)
                        if _is_process_alive(orphan_pid):
                            os.kill(orphan_pid, signal.SIGKILL)
                            await asyncio.sleep(0.5)
                    except OSError:
                        pass
                    # Retry once after killing orphan
                    return await self._spawn()
            raise

    async def _spawn(self) -> str:
        """Spawn a new cloudflared subprocess and wait for URL."""
        cloudflared = await ensure_cloudflared_async()
        logger.info(
            "Starting cloudflared quick tunnel on port %d...", self._local_port
        )

        self._adopted_pid = None
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
            await self._kill_process()
            raise RuntimeError("cloudflared failed to start within 30 seconds")

        logger.info("Tunnel active: %s", self._public_url)
        self._save_state()
        return self._public_url  # type: ignore[return-value]

    async def _try_adopt(self) -> str | None:
        """Try to adopt an existing cloudflared from a previous bot instance.

        Returns the public URL if adoption succeeds, None otherwise.
        """
        if not self._state_file.is_file():
            return None

        try:
            state = json.loads(self._state_file.read_text())
            pid = state.get("pid")
            url = state.get("url")
            port = state.get("port")
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read tunnel state file")
            self._state_file.unlink(missing_ok=True)
            return None

        if not pid or not url:
            return None

        # Port mismatch — can't adopt
        if port and port != self._local_port:
            logger.debug("State file port %d != our port %d, skipping adoption", port, self._local_port)
            return None

        # Check if PID is alive and actually cloudflared
        if not _is_process_alive(pid):
            logger.debug("Previous cloudflared (PID %d) is no longer alive", pid)
            self._state_file.unlink(missing_ok=True)
            return None

        if not _is_cloudflared_process(pid):
            logger.debug("PID %d is alive but not cloudflared, skipping adoption", pid)
            self._state_file.unlink(missing_ok=True)
            return None

        # Verify URL is reachable
        healthy = await _check_url_healthy(url)
        if not healthy:
            logger.warning(
                "Previous cloudflared (PID %d) alive but URL %s unreachable, killing it",
                pid, url,
            )
            try:
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(1)
                if _is_process_alive(pid):
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            self._state_file.unlink(missing_ok=True)
            return None

        # Adoption successful!
        self._adopted_pid = pid
        self._process = None
        self._public_url = url
        self._ready.set()
        logger.info("Adopted existing cloudflared tunnel (PID %d): %s", pid, url)

        # Start a background health checker for the adopted process
        self._monitor_task = asyncio.create_task(self._monitor_adopted(pid))

        return url

    async def _monitor_adopted(self, pid: int) -> None:
        """Monitor an adopted cloudflared process for unexpected exit."""
        while not self._stopping:
            await asyncio.sleep(10)
            if self._stopping:
                return
            if not _is_process_alive(pid):
                logger.warning("Adopted cloudflared (PID %d) has exited", pid)
                self._adopted_pid = None
                self._public_url = None
                self._ready.clear()
                asyncio.create_task(self._auto_restart())
                return

    async def _kill_process(self) -> None:
        """Kill the current cloudflared subprocess without setting _stopping.

        Used for cleanup on spawn failure so auto-restart can continue.
        """
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._process = None
        self._public_url = None
        self._ready.clear()

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

        if not self._stopping and not self._auto_restarting:
            asyncio.create_task(self._auto_restart())

    async def _auto_restart(self) -> None:
        """Restart the tunnel with exponential backoff, then infinite slow retry.

        Uses _spawn() directly (not start()) to avoid nested adopt/restart loops.
        """
        if self._auto_restarting:
            return
        self._auto_restarting = True
        try:
            await self._auto_restart_loop()
        finally:
            self._auto_restarting = False

    async def _auto_restart_loop(self) -> None:
        """Inner restart loop — separated so _auto_restarting flag is properly managed."""
        # Phase 1: exponential backoff (5 attempts)
        delays = [10, 30, 60, 120, 300]
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
                new_url = await self._spawn()
                if self._on_url_change:
                    self._on_url_change(new_url)
                logger.info("Tunnel auto-restarted: %s", new_url)
                return
            except Exception:
                logger.exception("Auto-restart attempt %d failed", attempt + 1)

        # Phase 2: infinite slow retry every 5 minutes
        logger.warning(
            "Initial auto-restart attempts exhausted. "
            "Entering persistent background retry (every %ds)...",
            _BACKGROUND_RETRY_INTERVAL,
        )
        attempt_num = len(delays)
        while not self._stopping:
            await asyncio.sleep(_BACKGROUND_RETRY_INTERVAL)
            if self._stopping:
                return
            attempt_num += 1
            try:
                self._public_url = None
                self._ready.clear()
                new_url = await self._spawn()
                if self._on_url_change:
                    self._on_url_change(new_url)
                logger.info(
                    "Tunnel recovered after background retry (attempt %d): %s",
                    attempt_num, new_url,
                )
                return
            except Exception:
                logger.warning(
                    "Background retry attempt %d failed, will retry in %ds",
                    attempt_num, _BACKGROUND_RETRY_INTERVAL,
                )

    def _save_state(self) -> None:
        """Persist tunnel state for reuse across bot restarts."""
        pid = None
        if self._process and self._process.pid:
            pid = self._process.pid
        elif self._adopted_pid:
            pid = self._adopted_pid

        if not pid or not self._public_url:
            return

        state = {
            "pid": pid,
            "url": self._public_url,
            "port": self._local_port,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(state))
            logger.debug("Saved tunnel state: PID %d, URL %s", pid, self._public_url)
        except OSError:
            logger.warning("Failed to save tunnel state file")

    async def stop(self) -> None:
        """Stop the tunnel subprocess and clean up state."""
        self._stopping = True

        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("cloudflared stopped")
        elif self._adopted_pid:
            # Kill the adopted process
            try:
                os.kill(self._adopted_pid, signal.SIGTERM)
                # Wait briefly for graceful shutdown
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    if not _is_process_alive(self._adopted_pid):
                        break
                else:
                    os.kill(self._adopted_pid, signal.SIGKILL)
                logger.info("Adopted cloudflared (PID %d) stopped", self._adopted_pid)
            except OSError:
                logger.debug("Adopted cloudflared (PID %d) already gone", self._adopted_pid)

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        self._process = None
        self._adopted_pid = None
        self._public_url = None
        self._ready.clear()
        self._state_file.unlink(missing_ok=True)

    async def detach(self) -> None:
        """Detach from the tunnel without killing cloudflared.

        Saves the tunnel state so the next bot instance can adopt it.
        Used during bot shutdown to preserve the tunnel for reuse.
        """
        self._stopping = True
        self._save_state()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        # Don't kill the process — just detach
        self._process = None
        self._adopted_pid = None
        self._public_url = None
        self._ready.clear()
        logger.info("Detached from cloudflared tunnel (preserved for next instance)")

    async def restart(self) -> str:
        """Restart the tunnel (URL will change)."""
        await self.stop()
        return await self.start()
