"""File browser UI for the /ls command.

Provides a Telegram inline-keyboard file browser that lists both directories
and files within a workspace, with pagination and navigation.

Key components:
  - ITEMS_PER_PAGE: Max items shown per page (8)
  - build_file_browser: Build the file browser UI
  - clear_ls_state: Clear browsing state from user_data
"""

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .callback_data import (
    CB_LS_CLOSE,
    CB_LS_DIR,
    CB_LS_FILE,
    CB_LS_PAGE,
    CB_LS_UP,
)

ITEMS_PER_PAGE = 8

# User state keys for /ls file browser
LS_PATH_KEY = "ls_path"
LS_ROOT_KEY = "ls_root"
LS_ENTRIES_KEY = "ls_entries"


def clear_ls_state(user_data: dict | None) -> None:
    """Clear file browser state keys from user_data."""
    if user_data is not None:
        user_data.pop(LS_PATH_KEY, None)
        user_data.pop(LS_ROOT_KEY, None)
        user_data.pop(LS_ENTRIES_KEY, None)


def _format_size(size_bytes: int) -> str:
    """Format a file size in human-readable form.

    Examples: "0B", "512B", "1.2KB", "3.4MB", "1.0GB"
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    for unit in ("KB", "MB", "GB"):
        size_bytes /= 1024
        if size_bytes < 1024 or unit == "GB":
            if size_bytes == int(size_bytes):
                return f"{int(size_bytes)}{unit}"
            return f"{size_bytes:.1f}{unit}"
    return f"{size_bytes:.1f}GB"  # pragma: no cover


def build_file_browser(
    current_path: str, page: int = 0, root_path: str | None = None
) -> tuple[str, InlineKeyboardMarkup, list[tuple[str, bool, int]]]:
    """Build file browser UI showing directories and files.

    Args:
        current_path: Directory to display.
        page: Pagination page number.
        root_path: The browser cannot navigate above this directory.

    Returns:
        (text, keyboard, entries) where entries is the full sorted list of
        (name, is_dir, size_bytes) tuples.
    """
    path = Path(current_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        path = Path(root_path).resolve() if root_path else Path.cwd()

    # Collect entries
    entries: list[tuple[str, bool, int]] = []
    try:
        for item in path.iterdir():
            is_dir = item.is_dir()
            try:
                size = 0 if is_dir else item.stat().st_size
            except OSError:
                size = 0
            entries.append((item.name, is_dir, size))
    except (PermissionError, OSError):
        pass

    # Sort: dirs first, files second; within each group alphabetical;
    # hidden items (starting with '.') go last within their group.
    def _sort_key(entry: tuple[str, bool, int]) -> tuple[int, int, str]:
        name, is_dir, _size = entry
        hidden = 1 if name.startswith(".") else 0
        dir_order = 0 if is_dir else 1
        return (dir_order, hidden, name.lower())

    entries.sort(key=_sort_key)

    # Pagination
    total_pages = max(1, (len(entries) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    page_entries = entries[start : start + ITEMS_PER_PAGE]

    # Display path relative to root
    if root_path:
        try:
            rel = path.relative_to(Path(root_path).resolve())
            display_path = str(rel) if str(rel) != "." else ""
        except ValueError:
            display_path = str(path)
    else:
        display_path = str(path)

    # Header text
    header = f"📂 {display_path}/" if display_path else "📂 /"
    text_lines = [header, ""]
    for name, is_dir, size in page_entries:
        if is_dir:
            text_lines.append(f"📁 {name}/")
        else:
            text_lines.append(f"📄 {name} ({_format_size(size)})")

    if not entries:
        text_lines.append("_(empty)_")

    text = "\n".join(text_lines)

    # Build keyboard buttons — 2 per row
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(page_entries), 2):
        row: list[InlineKeyboardButton] = []
        for j in range(min(2, len(page_entries) - i)):
            name, is_dir, size = page_entries[i + j]
            idx = start + i + j
            if is_dir:
                label = name[:14] + "…" if len(name) > 15 else name
                row.append(
                    InlineKeyboardButton(
                        f"📁 {label}/", callback_data=f"{CB_LS_DIR}{idx}"
                    )
                )
            else:
                label = name[:10] + "…" if len(name) > 11 else name
                row.append(
                    InlineKeyboardButton(
                        f"📄 {label}", callback_data=f"{CB_LS_FILE}{idx}"
                    )
                )
        buttons.append(row)

    # Pagination nav row
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀", callback_data=f"{CB_LS_PAGE}{page - 1}")
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton("▶", callback_data=f"{CB_LS_PAGE}{page + 1}")
            )
        buttons.append(nav)

    # Bottom action row: .. and close
    action_row: list[InlineKeyboardButton] = []
    at_root = path == path.parent
    if root_path:
        at_root = at_root or path == Path(root_path).resolve()
    if not at_root:
        action_row.append(InlineKeyboardButton("── .. ──", callback_data=CB_LS_UP))
    action_row.append(InlineKeyboardButton("✕ 關閉", callback_data=CB_LS_CLOSE))
    buttons.append(action_row)

    return text, InlineKeyboardMarkup(buttons), entries
