"""Application entry point — CLI dispatcher and bot bootstrap.

Handles four execution modes:
  1. `baobaobot hook` — delegates to hook.hook_main() for Claude Code hook processing.
  2. `baobaobot init` — initializes workspace directory with default templates.
  3. `baobaobot setup` — interactive first-time setup (create .env, init workspace,
     install hook).
  4. Default — configures logging, initializes workspace + tmux session, and starts
     the Telegram bot polling loop via bot.create_bot().

By default, the bot auto-launches inside a tmux session so it survives terminal
closure. Use `--foreground` / `-f` to run in the current terminal instead.
"""

import json
import logging
import os
import shutil
import subprocess
import sys


def _setup() -> None:
    """Interactive first-time setup."""
    from .utils import baobaobot_dir

    config_dir = baobaobot_dir()
    env_file = config_dir / ".env"

    if env_file.exists():
        resp = input(f"{env_file} already exists. Overwrite? [y/N] ")
        if resp.lower() != "y":
            print("Setup cancelled.")
            return

    print("=== BaoBaoClaude Setup ===\n")

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
    for uid in users.split(","):
        uid = uid.strip()
        if uid and not uid.isdigit():
            print(f"Error: '{uid}' is not a valid numeric user ID.")
            sys.exit(1)

    claude_cmd = input("Claude command [claude]: ").strip()
    if not claude_cmd:
        claude_cmd = "claude"

    # Write .env
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN={token}\n"
        f"ALLOWED_USERS={users}\n"
        f"CLAUDE_COMMAND={claude_cmd}\n"
    )
    print(f"\nConfig written to {env_file}")

    # Set env so Config can load without re-reading the file
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["ALLOWED_USERS"] = users
    os.environ["CLAUDE_COMMAND"] = claude_cmd

    # Init workspace
    from .config import Config

    cfg = Config()
    from .workspace.manager import WorkspaceManager

    wm = WorkspaceManager(cfg.workspace_dir)
    wm.init()
    print(f"Workspace initialized at {cfg.workspace_dir}")

    # Install hook
    from .hook import _install_hook

    _install_hook()

    print("\nSetup complete! Run 'baobaobot' to start the bot.")


def _launch_in_tmux() -> None:
    """Create a tmux session and re-launch baobaobot inside it."""
    session_name = os.getenv("TMUX_SESSION_NAME", "baobaobot")
    window_name = "__main__"
    target = f"{session_name}:{window_name}"

    if not shutil.which("tmux"):
        print("Error: tmux is not installed.")
        print("Install tmux, or run with --foreground to skip tmux.")
        sys.exit(1)

    # Check if session already exists
    session_exists = (
        subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        ).returncode
        == 0
    )

    if session_exists:
        # Check if __main__ window already has baobaobot running
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
                print(
                    f"baobaobot is already running in tmux session '{session_name}'."
                )
                print(f"  View logs:  tmux attach -t {session_name}")
                print("  Restart:    scripts/restart.sh")
                return
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

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        _setup()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        # Standalone workspace initialization
        from .config import config
        from .workspace.manager import WorkspaceManager

        wm = WorkspaceManager(config.workspace_dir)
        wm.init()
        print(f"Workspace initialized at {config.workspace_dir}")

        # Suggest hook install if not yet configured
        from .hook import _is_hook_installed, _CLAUDE_SETTINGS_FILE

        try:
            settings: dict = {}
            if _CLAUDE_SETTINGS_FILE.exists():
                settings = json.loads(_CLAUDE_SETTINGS_FILE.read_text())
            if not _is_hook_installed(settings):
                print(
                    "\nHint: Run 'baobaobot hook --install' to set up "
                    "Claude Code session tracking."
                )
        except (OSError, json.JSONDecodeError):
            pass  # Non-critical hint — don't fail init
        return

    # Check if we should auto-launch inside tmux
    foreground = "--foreground" in sys.argv or "-f" in sys.argv
    inside_tmux = os.environ.get("_BAOBAOBOT_TMUX") == "1" or "TMUX" in os.environ

    if not foreground and not inside_tmux:
        _launch_in_tmux()
        return

    # Strip --foreground / -f from argv so they don't confuse anything downstream
    sys.argv = [a for a in sys.argv if a not in ("--foreground", "-f")]

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    # Import config before enabling DEBUG — avoid leaking debug logs on config errors
    try:
        from .config import config
    except ValueError as e:
        print(f"Error: {e}\n")
        print("Run 'baobaobot setup' for interactive first-time configuration.")
        sys.exit(1)

    logging.getLogger("baobaobot").setLevel(logging.DEBUG)
    logger = logging.getLogger(__name__)

    # Initialize workspace on startup
    from .workspace.manager import WorkspaceManager

    wm = WorkspaceManager(config.workspace_dir)
    wm.init()
    logger.info("Workspace ready at %s", config.workspace_dir)

    from .tmux_manager import tmux_manager

    logger.info("Allowed users: %s", config.allowed_users)
    logger.info("Claude projects path: %s", config.claude_projects_path)

    # Ensure tmux session exists
    session = tmux_manager.get_or_create_session()
    logger.info("Tmux session '%s' ready", session.session_name)

    logger.info("Starting Telegram bot...")
    from .bot import create_bot

    application = create_bot()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
