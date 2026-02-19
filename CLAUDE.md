# CLAUDE.md

BaoBaoClaude — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows, with persistent personality (SOUL.md), identity (IDENTITY.md), user profiles (USER.md), and memory (MEMORY.md + daily memories).

All intelligence stays in Claude Code; BaoBaoClaude handles file management, workspace assembly, and Telegram UI.

**Architecture philosophy**: Operates on tmux, not the Claude Code SDK. The Claude Code process stays in a tmux window; BaoBaoClaude reads output and sends keystrokes. Users can seamlessly switch between desktop terminal and Telegram.

Tech stack: Python, python-telegram-bot, tmux, uv, SQLite.

## Common Commands

```bash
# Linting & Formatting (MUST pass before committing)
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type Checking
uv run pyright src/baobaobot/

# Testing
uv run pytest                              # All tests
uv run pytest tests/baobaobot/               # Unit tests only
uv run pytest tests/baobaobot/workspace/     # Workspace tests
uv run pytest tests/baobaobot/persona/       # Persona tests
uv run pytest tests/baobaobot/memory/        # Memory tests
uv run pytest -v -k "test_name"           # Specific test

# Development
baobaobot add-agent                           # Add a new agent to settings.toml
baobaobot hook --install                      # Install Claude Code hook
uv sync                                    # Install dependencies
```

## Project Structure

```
src/baobaobot/
├── main.py                  # CLI entry: hook / add-agent / bot start + auto-tmux launch
├── settings.py              # TOML-based multi-agent config (AgentConfig + load_settings)
├── bot.py                   # Telegram bot (new: /soul, /identity, /profile, /memory, /forget, /workspace, /rebuild)
├── workspace/               # Workspace system
│   ├── manager.py           # Directory init, project linking, bin/ script install
│   ├── assembler.py         # CLAUDE.md assembly from source files
│   ├── bin/                 # Scripts deployed to ~/.baobaobot/bin/
│   │   ├── memory-search    # SQLite memory search (used by Claude Code)
│   │   └── memory-list      # List recent daily memories
│   └── templates/           # Default SOUL.md, IDENTITY.md, USER.md, AGENTS.md, MEMORY.md
├── persona/                 # Persona system
│   ├── soul.py              # SOUL.md read/write
│   ├── identity.py          # IDENTITY.md parse/update (AgentIdentity)
│   └── profile.py           # USER.md parse/update (UserProfile)
├── memory/                  # Memory system
│   ├── db.py                # SQLite index (sync .md → SQLite, search, stats)
│   ├── manager.py           # MemoryManager (list, search via SQLite, cleanup)
│   ├── daily.py             # Daily memory file operations
│   └── search.py            # Legacy plain-text search (fallback)
├── handlers/                # Telegram handlers
│   ├── persona_handler.py   # /soul, /identity commands
│   ├── profile_handler.py   # /profile command
│   └── memory_handler.py    # /memory, /forget commands
└── ...                      # session.py, tmux_manager.py, hook.py, etc.
```

## Directory Layout

```
~/.baobaobot/                   # Root (BAOBAOBOT_DIR)
├── .env                     # Bot configuration
├── state.json               # Bot state (thread bindings, window states)
├── session_map.json         # Hook-generated window→session mapping
├── monitor_state.json       # Poll progress per JSONL file
├── bin/                     # Memory tools (shared across workspaces)
│   ├── memory-search        # SQLite-backed memory search
│   └── memory-list          # List recent daily memories
└── workspace/               # Default workspace (WORKSPACE_DIR)
    ├── CLAUDE.md            # Auto-assembled (persona + memory)
    ├── SOUL.md              # Personality definition
    ├── IDENTITY.md          # Agent identity (name, emoji, role)
    ├── USER.md              # User profile
    ├── AGENTS.md            # Work instructions + memory tool usage
    ├── MEMORY.md            # Long-term memory
    ├── memory/              # Daily memories (YYYY-MM-DD.md)
    ├── memory.db            # SQLite index of memory files
    └── projects/            # Symlinked project directories
```

## Core Design Constraints

- **No LLM calls in Python** — all intelligence in Claude Code, BaoBaoClaude manages files only
- **1 Topic = 1 Window = 1 Session** — all routing keyed by tmux window ID
- **CLAUDE.md assembly** — auto-composed from SOUL/IDENTITY/USER/AGENTS/MEMORY files
- **Two-layer memory** — MEMORY.md (long-term, curated) + memory/*.md (daily, auto)
- **SQLite memory index** — .md files are source of truth; SQLite provides fast search via lazy sync
- **Skill-based memory access** — Claude Code uses `~/.baobaobot/bin/memory-search` to query memories
