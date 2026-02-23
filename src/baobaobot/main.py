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


def _kill_existing(config_dir: Path) -> None:
    """If a previous bot instance is running, SIGTERM it (SIGKILL after 5s)."""
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

    claude_cmd = input("Claude command [claude]: ").strip()
    if not claude_cmd:
        claude_cmd = "claude"

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
claude_command = "{claude_cmd}"
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

    # Install hook
    from .hook import _install_hook

    _install_hook()

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


def _launch_in_tmux(config_dir: Path, session_name: str = "baobaobot") -> None:
    """Create a tmux session and re-launch baobaobot inside it."""
    window_name = "__main__"
    target = f"{session_name}:{window_name}"

    if not shutil.which("tmux"):
        print("Error: tmux is not installed.")
        sys.exit(1)

    # Kill any existing bot instance via pidfile
    _kill_existing(config_dir)

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

    # Send command into the __main__ window with marker env var
    full_cmd = f"_BAOBAOBOT_TMUX=1 {baobaobot_cmd}"
    subprocess.run(
        ["tmux", "send-keys", "-t", target, full_cmd, "Enter"],
        check=True,
    )

    print(f"baobaobot started in tmux session '{session_name}'.")
    print(f"  View logs:  tmux attach -t {session_name}")
    print("  Detach:     Ctrl-B, D")


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main

        hook_main()
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

    # Inside tmux: kill any existing instance, write our PID
    _kill_existing(config_dir)
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

    from .bot import create_bot

    if len(agent_contexts) == 1:
        # Single agent: blocking run_polling
        logger.info("Starting Telegram bot...")
        application = create_bot(agent_contexts[0])
        application.run_polling(allowed_updates=["message", "callback_query"])
    else:
        # Multiple agents: run concurrently with asyncio
        import asyncio
        import signal

        logger.info("Starting %d Telegram bots...", len(agent_contexts))

        _INIT_MAX_RETRIES = 10
        _INIT_RETRY_DELAY = 5  # seconds

        async def _init_with_retry(app, post_init_fn) -> None:  # type: ignore[no-untyped-def]
            """Initialize, post_init, start, and begin polling with retry on network errors."""
            from telegram.error import TimedOut, NetworkError

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
                        await app.shutdown()
                    except Exception:
                        pass
                    if attempt == _INIT_MAX_RETRIES:
                        raise
                    await asyncio.sleep(_INIT_RETRY_DELAY)

        async def _run_multi() -> None:
            apps = []
            for ctx in agent_contexts:
                app = create_bot(ctx)
                apps.append(app)

            # Initialize and start all applications
            # NOTE: post_init is only called by run_polling(), not by
            # initialize()/start() directly. We must call it manually.
            from .bot import post_init

            for app in apps:
                await _init_with_retry(app, post_init)

            logger.info("All %d bots started, waiting...", len(apps))

            # Wait until interrupted via signal
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)

            await stop_event.wait()

            # Shutdown all applications
            from .bot import post_shutdown

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

        try:
            asyncio.run(_run_multi())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
