"""Callback data constants for Telegram inline keyboards.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Constants:
  - CB_HISTORY_*: History pagination
  - CB_DIR_*: Directory browser navigation
  - CB_WIN_*: Window picker (bind existing unbound window)
  - CB_SCREENSHOT_*: Screenshot refresh
  - CB_ASK_*: Interactive UI navigation (arrows, enter, esc)
  - CB_FILE_*: File action prompt (no-caption attachment)
  - CB_KEYS_PREFIX: Screenshot control keys (kb:<key_id>:<window>)
  - CB_LS_*: File browser (/ls command)
"""

# History pagination
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer

# Directory browser
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"

# Window picker (bind existing unbound window)
CB_WIN_BIND = "wb:sel:"  # wb:sel:<index>
CB_WIN_NEW = "wb:new"  # proceed to directory browser
CB_WIN_CANCEL = "wb:cancel"

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Interactive UI (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"  # aq:up:<window>
CB_ASK_DOWN = "aq:down:"  # aq:down:<window>
CB_ASK_LEFT = "aq:left:"  # aq:left:<window>
CB_ASK_RIGHT = "aq:right:"  # aq:right:<window>
CB_ASK_ESC = "aq:esc:"  # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:"  # aq:enter:<window>
CB_ASK_SPACE = "aq:spc:"  # aq:spc:<window>
CB_ASK_TAB = "aq:tab:"  # aq:tab:<window>
CB_ASK_REFRESH = "aq:ref:"  # aq:ref:<window>

# Restart session (freeze recovery)
CB_RESTART_SESSION = "rs:"  # rs:<window_id>

# File action (no-caption attachment prompt)
CB_FILE_READ = "fa:read:"  # fa:read:<file_key>
CB_FILE_DESC = "fa:desc:"  # fa:desc:<file_key>
CB_FILE_CANCEL = "fa:cancel:"  # fa:cancel:<file_key>

# Voice transcript confirmation
CB_VOICE_SEND = "vt:send:"  # vt:send:<voice_key>
CB_VOICE_EDIT = "vt:edit:"  # vt:edit:<voice_key>
CB_VOICE_CANCEL = "vt:cancel:"  # vt:cancel:<voice_key>

# Screenshot control keys
CB_KEYS_PREFIX = "kb:"  # kb:<key_id>:<window>

# Verbosity setting
CB_VERBOSITY = "vb:"  # vb:<level>

# File browser (/ls)
CB_LS_DIR = "ls:d:"  # ls:d:<index>  — enter directory
CB_LS_FILE = "ls:f:"  # ls:f:<index>  — view/download file
CB_LS_UP = "ls:up"  # go up one level
CB_LS_PAGE = "ls:p:"  # ls:p:<page>   — pagination
CB_LS_CLOSE = "ls:close"  # close browser
