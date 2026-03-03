"""Application entry point — CLI dispatcher and bot bootstrap.

Handles three execution modes:
  1. `baobaobot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. `baobaobot add-agent` — interactive prompt to add a new agent to settings.toml.
  3. Default — configures logging, initializes shared files + tmux session, and starts
     the Telegram bot polling loop via bot.create_bot(). Auto-triggers first-time setup
     if settings.toml is missing.

The bot always auto-launches inside a tmux session so it survives terminal closure.
"""

import atexit
import logging
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

PIDFILE_NAME = "baobaobot.pid"

_is_restart = False
_stop_with_tmux_kill = False


def _read_pid(config_dir: Path) -> int | None:
    """Read pidfile and return PID or None."""
    pid_path = config_dir / PIDFILE_NAME
    try:
        return int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_pid(config_dir: Path) -> None:
    """Write current PID to pidfile."""
    (config_dir / PIDFILE_NAME).write_text(str(os.getpid()) + "\n")


def _remove_pid(config_dir: Path) -> None:
    """Remove pidfile."""
    try:
        (config_dir / PIDFILE_NAME).unlink()
    except FileNotFoundError:
        pass


def _kill_tmux_session(session_name: str = "baobaobot") -> None:
    """Kill the tmux session if it exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    if result.returncode == 0:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
        )
        print(f"Killed tmux session '{session_name}'.")


def _stop() -> None:
    """Send SIGUSR1 to the running bot to trigger graceful stop with summaries."""
    from .utils import baobaobot_dir

    config_dir = baobaobot_dir()
    pid = _read_pid(config_dir)

    if pid is None or not _is_pid_alive(pid):
        print("baobaobot is not running.")
        _remove_pid(config_dir)
        _kill_tmux_session()
        return

    print(f"Sending stop signal to baobaobot (PID {pid})...")
    os.kill(pid, signal.SIGUSR1)

    # Poll for process exit — 6 minute timeout (summaries can take a while)
    timeout = 360  # seconds
    interval = 0.5
    elapsed = 0.0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        if not _is_pid_alive(pid):
            print("baobaobot stopped successfully.")
            _kill_tmux_session()  # safety net
            return

    # Timeout — force kill
    print(f"Timeout after {timeout}s. Force killing PID {pid}...")
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.5)
    _remove_pid(config_dir)
    _kill_tmux_session()
    print("baobaobot force killed.")


def _send_restart_notifications(
    agent_configs: list,  # list[AgentConfig]
    message: str,
) -> None:
    """Send a notification message to each agent's primary chat via Telegram API.

    Uses httpx for synchronous HTTP calls — no async needed at the pre-startup stage.
    Reads each agent's state.json to find notification targets:
    - Forum mode: first thread_binding → group_chat_ids for the chat_id + thread_id
    - Group mode: first group_binding → chat_id
    """
    import json

    import httpx

    for cfg in agent_configs:
        state_file = cfg.state_file
        if not state_file.is_file():
            continue
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            continue

        token = cfg.bot_token
        targets: list[tuple[int, int | None]] = []  # (chat_id, thread_id | None)

        if cfg.mode == "group":
            gb = state.get("group_bindings", {})
            if gb:
                first_chat_id = int(next(iter(gb)))
                targets.append((first_chat_id, None))
        else:
            # Forum mode: first thread binding → resolve group_chat_id
            tb = state.get("thread_bindings", {})
            gci = state.get("group_chat_ids", {})
            for uid, bindings in tb.items():
                for tid in bindings:
                    key = f"{uid}:{tid}"
                    chat_id = gci.get(key)
                    if chat_id:
                        targets.append((int(chat_id), int(tid)))
                        break
                if targets:
                    break

        for chat_id, thread_id in targets:
            params: dict[str, object] = {"chat_id": chat_id, "text": message}
            if thread_id is not None:
                params["message_thread_id"] = thread_id
            try:
                httpx.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=params,
                    timeout=10,
                )
            except Exception:
                pass


def _kill_existing(config_dir: Path, *, pid: int | None = None) -> None:
    """If a previous bot instance is running, SIGTERM it (SIGKILL after 5s).

    Args:
        pid: Pre-read PID to avoid re-reading the pidfile. If None, reads it.
    """
    if pid is None:
        pid = _read_pid(config_dir)
    if pid is None or not _is_pid_alive(pid):
        return
    if pid == os.getpid():
        return
    print(f"Stopping existing baobaobot (PID {pid})...")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):  # 5 seconds, check every 0.1s
        time.sleep(0.1)
        if not _is_pid_alive(pid):
            print("Stopped.")
            return
    print(f"Force killing PID {pid}...")
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)


def _tz_to_locale() -> str:
    """Auto-detect locale from OS timezone. Falls back to en-US."""
    from .locale_utils import TZ_LOCALE_MAP

    try:
        import subprocess as _sp

        result = _sp.run(
            ["readlink", "/etc/localtime"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "zoneinfo/" in result.stdout:
            tz = result.stdout.strip().split("zoneinfo/")[-1]
            if tz in TZ_LOCALE_MAP:
                return TZ_LOCALE_MAP[tz]
    except Exception:
        pass
    return "en-US"


def _setup() -> None:
    """Interactive first-time setup — produces .env (secrets) + settings.toml."""
    from pathlib import Path

    from .utils import baobaobot_dir

    default_dir = Path.home() / ".baobaobot"
    config_dir = baobaobot_dir()
    toml_path = config_dir / "settings.toml"

    if toml_path.exists():
        resp = input(f"{toml_path} already exists. Overwrite? [y/N] ")
        if resp.lower() != "y":
            print("Setup cancelled.")
            return

    print("=== BaoBaoClaude Setup ===\n")

    # Allow custom BAOBAOBOT_DIR (always show ~/.baobaobot as default)
    custom_dir = input(f"Config directory [{default_dir}]: ").strip()
    if custom_dir:
        config_dir = Path(os.path.expanduser(custom_dir))
    else:
        config_dir = default_dir
    toml_path = config_dir / "settings.toml"
    env_file = config_dir / ".env"

    # Always persist chosen directory so baobaobot_dir() resolves it on next run
    from .utils import save_dir_pointer

    save_dir_pointer(config_dir)
    print(f"Config directory: {config_dir}")

    # Agent name
    agent_name = input("Agent name [baobao]: ").strip() or "baobao"
    token_env_var = f"{agent_name.upper()}_BOT_TOKEN"

    token = input("Telegram Bot Token (from @BotFather): ").strip()
    if not token:
        print("Error: Token is required.")
        sys.exit(1)

    users = input(
        "Allowed Telegram User IDs (comma-separated, from @userinfobot): "
    ).strip()
    if not users:
        print("Error: At least one user ID is required.")
        sys.exit(1)

    # Validate user IDs are numeric
    user_ids: list[int] = []
    for uid in users.split(","):
        uid = uid.strip()
        if uid and not uid.isdigit():
            print(f"Error: '{uid}' is not a valid numeric user ID.")
            sys.exit(1)
        if uid:
            user_ids.append(int(uid))

    # Mode selection
    mode_resp = input("Bot mode (forum/group) [forum]: ").strip().lower()
    mode = mode_resp if mode_resp in ("forum", "group") else "forum"

    # Agent type selection
    agent_type_input = input("Agent type (claude/gemini) [claude]: ").strip().lower()
    agent_type = (
        agent_type_input if agent_type_input in ("claude", "gemini") else "claude"
    )

    default_cmd = "gemini" if agent_type == "gemini" else "claude"
    claude_cmd = input(f"CLI command [{default_cmd}]: ").strip()
    if not claude_cmd:
        claude_cmd = default_cmd

    # Locale auto-detect from OS timezone
    detected_locale = _tz_to_locale()
    locale_input = input(f"Locale [{detected_locale}]: ").strip()
    locale = locale_input if locale_input else detected_locale

    # Optional: voice transcription
    whisper_model = ""
    voice_resp = input(
        "Enable voice transcription? (requires faster-whisper + model download) [y/N] "
    ).strip()
    enable_voice = voice_resp.lower() in ("y", "yes")
    if enable_voice:
        whisper_model = input("Whisper model size [small]: ").strip() or "small"

    # Write .env (secrets only)
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"{token_env_var}={token}\n")
    print(f"\nSecrets written to {env_file}")

    # Write settings.toml — list all settings with defaults so users know what's available
    users_toml = ", ".join(str(u) for u in user_ids)
    whisper_line = (
        f'whisper_model = "{whisper_model}"'
        if whisper_model
        else '# whisper_model = "small"'
    )
    toml_content = f"""\
# BaoBaoClaude settings
# Agent-level settings override [global]; unset keys fall back to [global] then defaults.

[global]
allowed_users = [{users_toml}]
cli_command = "{claude_cmd}"
locale = "{locale}"

# Memory
recent_memory_days = 7         # default days shown in /memory command

# Monitoring
monitor_poll_interval = 2.0    # seconds between session polling cycles

# Voice transcription (requires faster-whisper)
{whisper_line}

# Cron
# cron_default_tz = "Asia/Taipei"  # default timezone for cron jobs

# Each [[agents]] entry creates one bot instance.
# Per-agent keys override [global] values.
[[agents]]
name = "{agent_name}"
agent_type = "{agent_type}"
bot_token_env = "{token_env_var}"
mode = "{mode}"
"""
    toml_path.write_text(toml_content)
    print(f"Settings written to {toml_path}")

    # Set env so load_settings can resolve the token
    os.environ[token_env_var] = token

    # Init shared files
    from .settings import load_settings

    agents = load_settings(config_dir=config_dir)
    cfg = agents[0]

    from .workspace.manager import WorkspaceManager

    shared_dir = cfg.shared_dir
    shared_dir.mkdir(parents=True, exist_ok=True)
    wm = WorkspaceManager(shared_dir, shared_dir)
    wm.init_shared()
    print(f"Shared files initialized at {shared_dir}")

    # Install hooks for all supported backends
    from .hook import install_all_hooks

    install_all_hooks()

    # Install voice transcription dependency
    if enable_voice:
        print("\nInstalling faster-whisper...")
        uv = shutil.which("uv")
        if uv:
            cmd = [uv, "pip", "install", "faster-whisper>=1.0.0"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "faster-whisper>=1.0.0"]
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("Voice transcription enabled.")
        else:
            print(
                "Warning: faster-whisper installation failed. "
                "Voice transcription will be disabled.\n"
                "You can install it later: uv pip install faster-whisper"
            )

    print("\nSetup complete! Run 'baobaobot' to start the bot.")


def _add_agent() -> None:
    """Interactive prompt to add a new agent to an existing settings.toml."""
    import tomllib

    from .utils import baobaobot_dir

    config_dir = baobaobot_dir()
    toml_path = config_dir / "settings.toml"
    env_file = config_dir / ".env"

    if not toml_path.is_file():
        print(f"No settings.toml found at {toml_path}.")
        print("Run 'baobaobot' first to complete initial setup.")
        sys.exit(1)

    # Load existing settings to check for name conflicts
    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)
    existing_names = {a.get("name", "") for a in raw.get("agents", [])}

    print("=== Add New Agent ===\n")

    # Agent name
    agent_name = input("Agent name: ").strip()
    if not agent_name:
        print("Error: Agent name is required.")
        sys.exit(1)
    if agent_name in existing_names:
        print(f"Error: Agent '{agent_name}' already exists in settings.toml.")
        sys.exit(1)

    token_env_var = f"{agent_name.upper()}_BOT_TOKEN"

    token = input("Telegram Bot Token (from @BotFather): ").strip()
    if not token:
        print("Error: Token is required.")
        sys.exit(1)

    users = input(
        "Allowed Telegram User IDs (comma-separated, from @userinfobot): "
    ).strip()
    if not users:
        print("Error: At least one user ID is required.")
        sys.exit(1)

    # Validate user IDs are numeric
    user_ids: list[int] = []
    for uid in users.split(","):
        uid = uid.strip()
        if uid and not uid.isdigit():
            print(f"Error: '{uid}' is not a valid numeric user ID.")
            sys.exit(1)
        if uid:
            user_ids.append(int(uid))

    # Mode selection
    mode_resp = input("Bot mode (forum/group) [forum]: ").strip().lower()
    mode = mode_resp if mode_resp in ("forum", "group") else "forum"

    # Append [[agents]] block to settings.toml
    users_toml = ", ".join(str(u) for u in user_ids)
    agent_block = f"""\
[[agents]]
name = "{agent_name}"
bot_token_env = "{token_env_var}"
allowed_users = [{users_toml}]
mode = "{mode}"
"""
    with open(toml_path, "a") as f:
        # Ensure previous content ends with newline before appending
        if toml_path.stat().st_size > 0:
            with open(toml_path, "rb") as rf:
                rf.seek(-1, 2)
                if rf.read(1) != b"\n":
                    f.write("\n")
        f.write(agent_block)
    print(f"Agent '{agent_name}' added to {toml_path}")

    # Append token to .env
    with open(env_file, "a") as f:
        # Ensure previous content ends with newline before appending
        if env_file.exists() and env_file.stat().st_size > 0:
            with open(env_file, "rb") as rf:
                rf.seek(-1, 2)
                if rf.read(1) != b"\n":
                    f.write("\n")
        f.write(f"{token_env_var}={token}\n")
    print(f"Token written to {env_file}")

    print("\nDone! Restart baobaobot to activate the new agent.")


def _check_optional_deps(configs: list) -> None:
    """Auto-install missing optional dependencies at startup."""
    logger = logging.getLogger(__name__)
    needs_whisper = any(getattr(c, "whisper_model", "") for c in configs)
    if needs_whisper:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            logger.info("Installing faster-whisper...")
            uv = shutil.which("uv")
            if uv:
                cmd = [uv, "pip", "install", "faster-whisper>=1.0.0"]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "faster-whisper>=1.0.0"]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                logger.info("faster-whisper installed successfully")
            else:
                logger.warning(
                    "faster-whisper installation failed — voice transcription disabled. "
                    "Fix: uv pip install faster-whisper"
                )


def _launch_in_tmux(config_dir: Path, session_name: str = "baobaobot") -> None:
    """Create a tmux session and re-launch baobaobot inside it."""
    window_name = "__main__"
    target = f"{session_name}:{window_name}"

    if not shutil.which("tmux"):
        print("Error: tmux is not installed.")
        sys.exit(1)

    # Check if an existing instance is running → send restart notification before kill
    is_restart = False
    existing_pid = _read_pid(config_dir)
    if (
        existing_pid is not None
        and _is_pid_alive(existing_pid)
        and existing_pid != os.getpid()
    ):
        is_restart = True
        from .settings import load_settings

        try:
            agent_configs = load_settings(config_dir=config_dir)
            _send_restart_notifications(agent_configs, "⏳ Preparing to restart...")
        except Exception as e:
            print(f"Warning: could not send restart notification: {e}")

    _kill_existing(config_dir, pid=existing_pid)

    # Check if session already exists
    session_exists = (
        subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        ).returncode
        == 0
    )

    if session_exists:
        # Fallback: if pane still has a running process (e.g. old instance
        # started before pidfile was introduced), send C-c to stop it.
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-t",
                target,
                "-F",
                "#{pane_current_command}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            pane_cmd = result.stdout.strip()
            if pane_cmd not in ("bash", "zsh", "sh", "fish", ""):
                print(f"Stopping process in tmux pane '{target}'...")
                subprocess.run(
                    ["tmux", "send-keys", "-t", target, "C-c", ""],
                    capture_output=True,
                )
                # Wait for process to exit
                for _ in range(50):  # 5 seconds
                    time.sleep(0.1)
                    check = subprocess.run(
                        [
                            "tmux",
                            "list-panes",
                            "-t",
                            target,
                            "-F",
                            "#{pane_current_command}",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if check.returncode == 0 and check.stdout.strip() in (
                        "bash",
                        "zsh",
                        "sh",
                        "fish",
                        "",
                    ):
                        break
                time.sleep(0.5)
    else:
        # Create new session with __main__ window
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-n", window_name],
            check=True,
        )

    # Find the baobaobot command to re-exec
    baobaobot_cmd = shutil.which("baobaobot") or sys.argv[0]

    # Send command into the __main__ window with marker env vars
    restart_env = " _BAOBAOBOT_RESTART=1" if is_restart else ""
    full_cmd = f"_BAOBAOBOT_TMUX=1{restart_env} {baobaobot_cmd}"
    subprocess.run(
        ["tmux", "send-keys", "-t", target, full_cmd, "Enter"],
        check=True,
    )

    print(f"baobaobot started in tmux session '{session_name}'.")
    print(f"  View logs:  tmux attach -t {session_name}")
    print("  Detach:     Ctrl-B, D")


async def _safe_trigger_summary(
    scheduler: object,  # SystemScheduler
    ws_name: str,
    logger: logging.Logger,
) -> bool:
    """Wrap scheduler.trigger_summary with exception handling."""
    try:
        result = await scheduler.trigger_summary(ws_name)  # type: ignore[union-attr]
        if result:
            logger.info("Summary completed for workspace '%s'", ws_name)
        else:
            logger.info("No new content for workspace '%s', skipping summary", ws_name)
        return result
    except Exception as e:
        logger.error("Summary failed for workspace '%s': %s", ws_name, e)
        return False


async def _handle_stop_signal(
    apps: list,  # list[Application]
    agent_contexts: list,  # list[AgentContext]
    stop_event: object,  # asyncio.Event
) -> None:
    """Handle SIGUSR1: run final summaries for all workspaces, then trigger shutdown."""
    import asyncio

    logger = logging.getLogger(__name__)
    logger.warning("Received stop signal, running final summaries...")
    print("Received stop signal, running final summaries...")

    # Send shutdown notification to each agent's primary chat
    for app, ctx in zip(apps, agent_contexts):
        sm = ctx.session_manager
        cfg = ctx.config
        chat_id: int | None = None
        thread_id: int | None = None

        if cfg.mode == "group":
            for cid in sm.group_bindings:
                chat_id = cid
                break
        else:
            for uid, bindings in sm.thread_bindings.items():
                for tid in bindings:
                    cid_val = sm.group_chat_ids.get(f"{uid}:{tid}")
                    if cid_val:
                        chat_id = cid_val
                        thread_id = tid
                        break
                if chat_id:
                    break

        if chat_id is not None:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ Shutting down for restart...",
                    message_thread_id=thread_id,
                )
            except Exception as e:
                logger.warning("Failed to send shutdown notification: %s", e)

    # Collect summary tasks
    tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
    for ctx in agent_contexts:
        scheduler = ctx.system_scheduler
        if scheduler is None:
            continue
        for ws_dir in ctx.config.iter_workspace_dirs():
            ws_name = ws_dir.name.removeprefix("workspace_")
            task = asyncio.create_task(
                _safe_trigger_summary(scheduler, ws_name, logger),
                name=f"summary:{ctx.config.name}:{ws_name}",
            )
            tasks.append(task)

    if tasks:
        logger.info("Running %d summary tasks...", len(tasks))
        done, pending = await asyncio.wait(tasks, timeout=300)  # 5 min timeout
        for t in pending:
            logger.warning("Cancelling timed-out summary task: %s", t.get_name())
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=5)
        completed = sum(1 for t in done if not t.cancelled() and t.result())
        logger.info("Summaries complete: %d/%d ran", completed, len(tasks))
    else:
        logger.info("No summary tasks to run.")

    global _stop_with_tmux_kill
    _stop_with_tmux_kill = True
    stop_event.set()  # type: ignore[union-attr]


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main

        hook_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        _stop()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "add-agent":
        _add_agent()
        return

    # Run first-time setup BEFORE launching into tmux,
    # so the user can see and interact with the setup prompts.
    from .utils import baobaobot_dir

    config_dir = baobaobot_dir()
    toml_path = config_dir / "settings.toml"

    if not toml_path.is_file():
        print("No settings.toml found. Running first-time setup...\n")
        _setup()
        # Re-resolve config_dir (setup may have changed it via dir pointer)
        config_dir = baobaobot_dir()
        toml_path = config_dir / "settings.toml"
        if not toml_path.is_file():
            print("Setup did not produce settings.toml. Exiting.")
            sys.exit(1)

    # Auto-launch inside tmux unless already there
    inside_tmux = os.environ.get("_BAOBAOBOT_TMUX") == "1"

    if not inside_tmux:
        _launch_in_tmux(config_dir, session_name="baobaobot")
        return

    # Inside tmux: the outer _launch_in_tmux already killed any existing instance
    # and sent the "preparing to restart" notification. Pick up the restart flag
    # from the env var it set so post_init can send "restart complete".
    global _is_restart
    _is_restart = os.environ.pop("_BAOBAOBOT_RESTART", "") == "1"

    _kill_existing(config_dir)  # safety net (normally a no-op)
    _write_pid(config_dir)
    atexit.register(_remove_pid, config_dir)

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    from .agent_context import AgentContext, create_agent_context
    from .settings import load_settings

    try:
        agent_configs = load_settings(config_dir=config_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}\n")
        print("Check your settings.toml configuration.")
        sys.exit(1)

    logging.getLogger("baobaobot").setLevel(logging.DEBUG)
    logger = logging.getLogger(__name__)

    _check_optional_deps(agent_configs)

    agent_contexts: list[AgentContext] = []

    for cfg in agent_configs:
        # Ensure agent directory exists
        cfg.agent_dir.mkdir(parents=True, exist_ok=True)

        # Initialize shared files on startup (persona + bin scripts)
        from .workspace.manager import WorkspaceManager

        wm = WorkspaceManager(cfg.shared_dir, cfg.shared_dir)
        wm.init_shared()
        logger.info("Shared files ready at %s", cfg.shared_dir)

        ctx = create_agent_context(cfg)
        agent_contexts.append(ctx)
        logger.info(
            "Agent '%s': allowed_users=%s, tmux_session=%s",
            cfg.name,
            cfg.allowed_users,
            cfg.tmux_session_name,
        )

    # Ensure tmux sessions exist for all agents
    for ctx in agent_contexts:
        session = ctx.tmux_manager.get_or_create_session()
        logger.info("Tmux session '%s' ready", session.session_name)

    import asyncio

    from .bot import create_bot, post_init, post_shutdown

    _INIT_MAX_RETRIES = 10
    _INIT_RETRY_DELAY = 5  # seconds

    async def _init_with_retry(app, post_init_fn) -> None:  # type: ignore[no-untyped-def]
        """Initialize, post_init, start, and begin polling with retry on network errors."""
        from telegram.error import NetworkError, TimedOut

        for attempt in range(1, _INIT_MAX_RETRIES + 1):
            try:
                await app.initialize()
                await post_init_fn(app)
                await app.start()
                updater = app.updater
                assert updater is not None
                await updater.start_polling(
                    allowed_updates=["message", "callback_query"]
                )
                return
            except (TimedOut, NetworkError, OSError) as e:
                logger.warning(
                    "Bot init attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt,
                    _INIT_MAX_RETRIES,
                    e,
                    _INIT_RETRY_DELAY,
                )
                # Shut down partially initialized app before retrying
                try:
                    if app.running:
                        await app.stop()
                    await app.shutdown()
                except Exception:
                    pass
                if attempt == _INIT_MAX_RETRIES:
                    raise
                await asyncio.sleep(_INIT_RETRY_DELAY)

    async def _run_bot() -> None:
        apps = []
        for ctx in agent_contexts:
            app = create_bot(ctx)
            app.bot_data["_is_restart"] = _is_restart
            apps.append(app)

        for app in apps:
            await _init_with_retry(app, post_init)

        logger.info("All %d bot(s) started, waiting...", len(apps))

        # Wait until interrupted via signal
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        loop.add_signal_handler(
            signal.SIGUSR1,
            lambda: asyncio.ensure_future(
                _handle_stop_signal(apps, agent_contexts, stop_event)
            ),
        )

        await stop_event.wait()

        # Shutdown all applications
        for i, app in enumerate(apps):
            try:
                await post_shutdown(app)
                updater = app.updater
                if updater:
                    await updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.error("Error shutting down app %d: %s", i, e)

        if _stop_with_tmux_kill:
            _kill_tmux_session()

    logger.info("Starting %d Telegram bot(s)...", len(agent_contexts))

    try:
        asyncio.run(_run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
