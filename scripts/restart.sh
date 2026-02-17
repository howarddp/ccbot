#!/usr/bin/env bash
set -euo pipefail

TMUX_SESSION="baobaobot"
TMUX_WINDOW="__main__"
TARGET="${TMUX_SESSION}:${TMUX_WINDOW}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAX_WAIT=10  # seconds to wait for process to exit

# Check if tmux session and window exist
if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "Error: tmux session '$TMUX_SESSION' does not exist"
    exit 1
fi

if ! tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -qx "$TMUX_WINDOW"; then
    echo "Error: window '$TMUX_WINDOW' not found in session '$TMUX_SESSION'"
    exit 1
fi

# Check if baobaobot is running by looking at the pane's current command
# Works on both macOS and Linux (no pstree dependency)
is_baobaobot_running() {
    local pane_cmd
    pane_cmd=$(tmux list-panes -t "$TARGET" -F '#{pane_current_command}' 2>/dev/null)
    # If the pane is running python (baobaobot), it's active
    # Shell commands (bash/zsh/sh/fish) mean baobaobot is not running
    case "$pane_cmd" in
        bash|zsh|sh|fish|"") return 1 ;;
        *) return 0 ;;
    esac
}

# Stop existing process if running
if is_baobaobot_running; then
    echo "Found running baobaobot process, sending Ctrl-C..."
    tmux send-keys -t "$TARGET" C-c

    # Wait for process to exit
    waited=0
    while is_baobaobot_running && [ "$waited" -lt "$MAX_WAIT" ]; do
        sleep 1
        waited=$((waited + 1))
        echo "  Waiting for process to exit... (${waited}s/${MAX_WAIT}s)"
    done

    if is_baobaobot_running; then
        echo "Process did not exit after ${MAX_WAIT}s, force killing pane process..."
        PANE_PID=$(tmux list-panes -t "$TARGET" -F '#{pane_pid}')
        # Kill all child processes of the pane shell
        pkill -P "$PANE_PID" 2>/dev/null || true
        sleep 2
        if is_baobaobot_running; then
            pkill -9 -P "$PANE_PID" 2>/dev/null || true
            sleep 1
        fi
    fi

    echo "Process stopped."
else
    echo "No baobaobot process running in $TARGET"
fi

# Brief pause to let the shell settle
sleep 1

# Start baobaobot
echo "Starting baobaobot in $TARGET..."
tmux send-keys -t "$TARGET" "cd ${PROJECT_DIR} && uv run baobaobot" Enter

# Verify startup and show logs
sleep 5
if is_baobaobot_running; then
    echo "baobaobot restarted successfully. Recent logs:"
    echo "----------------------------------------"
    tmux capture-pane -t "$TARGET" -p | tail -20
    echo "----------------------------------------"
else
    echo "Warning: baobaobot may not have started. Pane output:"
    echo "----------------------------------------"
    tmux capture-pane -t "$TARGET" -p | tail -30
    echo "----------------------------------------"
    exit 1
fi
