"""Hook subcommand for CLI session tracking (Claude Code + Gemini CLI).

Called by CLI SessionStart hooks to maintain a window↔session mapping in
<BAOBAOBOT_DIR>/agents/<agent>/session_map.json. Also provides ``--install``
to auto-configure hooks in ``~/.claude/settings.json`` and
``~/.gemini/settings.json``.

Supports two hook mechanisms:
- **Claude Code**: receives JSON payload via stdin (session_id, cwd, hook_event_name)
- **Gemini CLI**: receives context via env vars (GEMINI_PROJECT_DIR, TMUX_PANE);
  session_id must be discovered from session files; must output JSON to stdout.

This module must NOT import config.py (which requires TELEGRAM_BOT_TOKEN),
since hooks run inside tmux panes where bot env vars are not set.
Config directory resolution uses utils.baobaobot_dir() (shared with config.py).

Key functions: hook_main() (CLI entry), _install_hook().
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Validate session_id looks like a UUID
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# The hook command suffix for detection
_HOOK_COMMAND_SUFFIX = "baobaobot hook"


def _find_baobaobot_path() -> str:
    """Find the full path to the baobaobot executable.

    Priority:
    1. shutil.which("baobaobot") - if baobaobot is in PATH
    2. Same directory as the Python interpreter (for venv installs)
    """
    # Try PATH first
    baobaobot_path = shutil.which("baobaobot")
    if baobaobot_path:
        return baobaobot_path

    # Fall back to the directory containing the Python interpreter
    # This handles the case where baobaobot is installed in a venv
    python_dir = Path(sys.executable).parent
    baobaobot_in_venv = python_dir / "baobaobot"
    if baobaobot_in_venv.exists():
        return str(baobaobot_in_venv)

    # Last resort: assume it will be in PATH
    return "baobaobot"


def _is_hook_installed(settings: dict) -> bool:
    """Check if baobaobot hook is already installed in the settings.

    Detects both 'baobaobot hook' and full paths like '/path/to/baobaobot hook'.
    """
    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            # Match 'baobaobot hook' or paths ending with 'baobaobot hook'
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _install_hook() -> int:
    """Install the baobaobot hook into Claude's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error reading %s: %s", settings_file, e)
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    # Check if already installed
    if _is_hook_installed(settings):
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    # Find the full path to baobaobot
    baobaobot_path = _find_baobaobot_path()
    hook_command = f"{baobaobot_path} hook"
    hook_config = {"type": "command", "command": hook_command, "timeout": 5}
    logger.info("Installing hook command: %s", hook_command)

    # Install the hook
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    settings["hooks"]["SessionStart"].append({"hooks": [hook_config]})

    # Write back
    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        logger.error("Error writing %s: %s", settings_file, e)
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    logger.info("Hook installed successfully in %s", settings_file)
    print(f"Hook installed successfully in {settings_file}")
    return 0


_GEMINI_SETTINGS_FILE = Path.home() / ".gemini" / "settings.json"


def _is_gemini_hook_installed(settings: dict) -> bool:
    """Check if baobaobot hook is already installed in Gemini settings."""
    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _install_gemini_hook() -> int:
    """Install the baobaobot hook into Gemini's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _GEMINI_SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error reading %s: %s", settings_file, e)
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    if _is_gemini_hook_installed(settings):
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    baobaobot_path = _find_baobaobot_path()
    hook_command = f"{baobaobot_path} hook"
    # Gemini CLI uses milliseconds for timeout (unlike Claude Code which uses seconds)
    hook_config = {"type": "command", "command": hook_command, "timeout": 10000}
    logger.info("Installing Gemini hook command: %s", hook_command)

    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    settings["hooks"]["SessionStart"].append({"hooks": [hook_config]})

    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        logger.error("Error writing %s: %s", settings_file, e)
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    logger.info("Hook installed successfully in %s", settings_file)
    print(f"Hook installed successfully in {settings_file}")
    return 0


def install_all_hooks(agent_types: set[str] | None = None) -> int:
    """Install hooks for all supported backends.

    Args:
        agent_types: Set of agent types from config (e.g. {"claude", "gemini"}).
                     If provided, installs hooks for those backends.
                     If None, falls back to PATH detection.

    Returns 0 if all succeed, 1 if any fail.
    """
    result = _install_hook()
    # Install Gemini hook if any agent uses gemini, or if gemini is in PATH
    needs_gemini = (agent_types and "gemini" in agent_types) or shutil.which("gemini")
    if needs_gemini:
        gemini_result = _install_gemini_hook()
        if gemini_result != 0:
            result = gemini_result
    return result


def _discover_gemini_session_id(cwd: str) -> str:
    """Find the most recent Gemini session ID for the given cwd.

    Looks in ``~/.gemini/tmp/<project>/chats/`` for the newest ``.json`` file
    and extracts its ``sessionId`` field.  Tries both the named project
    directory (from ``projects.json``) and the sha256-hashed directory.
    """
    gemini_tmp = Path.home() / ".gemini" / "tmp"
    if not gemini_tmp.exists():
        return ""

    project_dirs: list[Path] = []

    # Try project name from projects.json
    projects_file = Path.home() / ".gemini" / "projects.json"
    if projects_file.exists():
        try:
            data = json.loads(projects_file.read_text())
            name = data.get("projects", {}).get(cwd)
            if name:
                named_dir = gemini_tmp / name
                if named_dir.is_dir():
                    project_dirs.append(named_dir)
        except (json.JSONDecodeError, OSError):
            pass

    # Try hash-based directory
    hash_dir = gemini_tmp / hashlib.sha256(cwd.encode()).hexdigest()
    if hash_dir.is_dir() and hash_dir not in project_dirs:
        project_dirs.append(hash_dir)

    if not project_dirs:
        logger.debug("No Gemini project dirs found for cwd=%s", cwd)
        return ""

    # Find most recently modified chat file across all project dirs
    latest_file: Path | None = None
    latest_mtime = 0.0

    for project_dir in project_dirs:
        chats_dir = project_dir / "chats"
        if not chats_dir.is_dir():
            continue
        for f in chats_dir.glob("*.json"):
            try:
                mtime = f.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_file = f
            except OSError:
                continue

    if not latest_file:
        logger.debug("No chat files found in Gemini project dirs for cwd=%s", cwd)
        return ""

    try:
        data = json.loads(latest_file.read_text())
        sid = data.get("sessionId", "")
        if sid:
            logger.debug("Discovered Gemini session_id=%s from %s", sid, latest_file)
        return sid
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read Gemini chat file %s: %s", latest_file, e)
        return ""


def _resolve_tmux_window(
    pane_id: str,
) -> tuple[str, str, str] | None:
    """Resolve tmux session name, window ID, and window name from a pane ID.

    Returns ``(tmux_session_name, window_id, window_name)`` or ``None``.
    """
    result = subprocess.run(
        [
            "tmux",
            "display-message",
            "-t",
            pane_id,
            "-p",
            "#{session_name}:#{window_id}:#{window_name}",
        ],
        capture_output=True,
        text=True,
    )
    raw_output = result.stdout.strip()
    # Expected format: "session_name:@id:window_name"
    parts = raw_output.split(":", 2)
    if len(parts) < 3:
        logger.warning(
            "Failed to parse session:window_id:window_name from tmux "
            "(pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return None
    tmux_session_name, window_id, window_name = parts

    # When a share_server web tmux session is linked to this window,
    # display-message may resolve to the web session (e.g. "web-abc123")
    # instead of the real bot session. Find the real session by listing
    # all sessions that contain this window.
    if tmux_session_name.startswith("web-"):
        try:
            real_result = subprocess.run(
                [
                    "tmux",
                    "list-panes",
                    "-a",
                    "-F",
                    "#{session_name}:#{window_id}",
                ],
                capture_output=True,
                text=True,
            )
            for line in real_result.stdout.strip().splitlines():
                sess, wid = line.split(":", 1)
                if wid == window_id and not sess.startswith("web-"):
                    logger.debug(
                        "Resolved web session %s -> real session %s",
                        tmux_session_name,
                        sess,
                    )
                    tmux_session_name = sess
                    break
        except Exception:
            pass  # fall through with original name

    return tmux_session_name, window_id, window_name


def _write_session_map(
    session_window_key: str,
    tmux_session_name: str,
    session_id: str,
    cwd: str,
    window_name: str,
) -> None:
    """Write a session_map.json entry with file locking.

    Routes to per-agent ``session_map.json`` by parsing agent name from the
    window name (format ``agent_name/topic_name``).
    """
    from .utils import baobaobot_dir

    config_root = baobaobot_dir()
    if "/" in window_name:
        agent_name = window_name.split("/", 1)[0]
        agent_dir = config_root / "agents" / agent_name
        map_file = agent_dir / "session_map.json"
    else:
        # Skip non-agent windows (e.g. __main__)
        logger.debug("Window '%s' has no agent prefix, skipping", window_name)
        return
    map_file.parent.mkdir(parents=True, exist_ok=True)

    lock_path = map_file.with_suffix(".lock")
    try:
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            logger.debug("Acquired lock on %s", lock_path)
            try:
                session_map: dict[str, dict[str, str]] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                session_map[session_window_key] = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                }

                # Clean up old-format key ("session:window_name") if it exists.
                # Previous versions keyed by window_name instead of window_id.
                old_key = f"{tmux_session_name}:{window_name}"
                if old_key != session_window_key and old_key in session_map:
                    del session_map[old_key]
                    logger.info("Removed old-format session_map key: %s", old_key)

                from .utils import atomic_write_json

                atomic_write_json(map_file, session_map)
                logger.info(
                    "Updated session_map: %s -> session_id=%s, cwd=%s",
                    session_window_key,
                    session_id,
                    cwd,
                )
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)


def hook_main() -> None:
    """Process a CLI hook event (Claude Code or Gemini CLI), or install hooks."""
    # Configure logging for the hook subprocess (main.py logging doesn't apply here)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="baobaobot hook",
        description="CLI session tracking hook (Claude Code + Gemini CLI)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the hook into ~/.claude/settings.json",
    )
    # Parse only known args to avoid conflicts with stdin JSON
    args, _ = parser.parse_known_args(sys.argv[2:])

    if args.install:
        logger.info("Hook install requested")
        sys.exit(_install_hook())

    # Detect hook mode: Gemini CLI uses env vars, Claude Code uses stdin JSON.
    # GEMINI_PROJECT_DIR is set by Gemini CLI for hook subprocesses.
    gemini_project_dir = os.environ.get("GEMINI_PROJECT_DIR", "")

    if gemini_project_dir:
        _process_gemini_hook(gemini_project_dir)
    else:
        _process_claude_hook()


def _process_gemini_hook(cwd: str) -> None:
    """Process a Gemini CLI SessionStart hook event.

    Gemini CLI sends JSON via stdin (same fields as Claude Code: session_id,
    cwd, hook_event_name) AND sets env vars (GEMINI_PROJECT_DIR, TMUX_PANE).
    We try stdin first for the session_id; fall back to file discovery.
    Gemini expects JSON output on stdout.
    """
    logger.debug("Processing Gemini hook event (cwd=%s)", cwd)

    if not os.path.isabs(cwd):
        logger.warning("GEMINI_PROJECT_DIR is not absolute: %s", cwd)
        print("{}")
        return

    # Try to read session_id from stdin JSON (Gemini does send it)
    session_id = ""
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id", "")
        # Also pick up cwd from stdin if env var was missing
        if not cwd:
            cwd = payload.get("cwd", cwd)
        logger.debug("Gemini stdin payload: session_id=%s, cwd=%s", session_id, cwd)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.debug("Failed to read Gemini stdin JSON (expected): %s", e)

    # Fall back to discovering session_id from Gemini session files (with retry)
    if not session_id:
        import time

        for attempt in range(4):
            session_id = _discover_gemini_session_id(cwd)
            if session_id:
                break
            # Session file may not be written yet; wait briefly
            logger.debug(
                "Gemini session discovery attempt %d/4 failed, retrying...",
                attempt + 1,
            )
            time.sleep(1)

    if not session_id:
        logger.warning("Could not determine Gemini session_id for cwd=%s", cwd)
        print("{}")
        return

    if not _UUID_RE.match(session_id):
        logger.warning("Invalid Gemini session_id format: %s", session_id)
        print("{}")
        return

    # Resolve tmux window from TMUX_PANE env var
    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.warning("TMUX_PANE not set, cannot determine window")
        print("{}")
        return

    tmux_info = _resolve_tmux_window(pane_id)
    if not tmux_info:
        print("{}")
        return
    tmux_session_name, window_id, window_name = tmux_info

    session_window_key = f"{tmux_session_name}:{window_id}"
    logger.debug(
        "Gemini hook: tmux key=%s, window_name=%s, session_id=%s, cwd=%s",
        session_window_key,
        window_name,
        session_id,
        cwd,
    )

    _write_session_map(
        session_window_key, tmux_session_name, session_id, cwd, window_name
    )

    # Gemini expects JSON output on stdout
    print("{}")


def _process_claude_hook() -> None:
    """Process a Claude Code SessionStart hook event (stdin JSON)."""
    logger.debug("Processing Claude Code hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    event = payload.get("hook_event_name", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    if not _UUID_RE.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return

    if event != "SessionStart":
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.warning("TMUX_PANE not set, cannot determine window")
        return

    tmux_info = _resolve_tmux_window(pane_id)
    if not tmux_info:
        return
    tmux_session_name, window_id, window_name = tmux_info

    session_window_key = f"{tmux_session_name}:{window_id}"
    logger.debug(
        "Claude hook: tmux key=%s, window_name=%s, session_id=%s, cwd=%s",
        session_window_key,
        window_name,
        session_id,
        cwd,
    )

    _write_session_map(
        session_window_key, tmux_session_name, session_id, cwd, window_name
    )
