# BaoBaoClaude

通過 Telegram 遠程控制 Claude Code 會話 — 監控、互動、管理運行在 tmux 中的 AI 編程會話，並賦予 Claude 持久的人格與記憶。

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## 為什麼做 BaoBaoClaude？

Claude Code 運行在終端裡。當你離開電腦 — 通勤路上、躺在沙發上、或者只是不在工位 — 會話仍在繼續，但你失去了查看和控制的能力。

BaoBaoClaude 讓你**通過 Telegram 無縫接管同一個會話**。核心設計思路是：它操作的是 **tmux**，而不是 Claude Code SDK。你的 Claude Code 進程始終在 tmux 視窗裡運行，BaoBaoClaude 只是讀取它的輸出並向它發送按鍵。這意味著：

- **從電腦無縫切換到手機** — Claude 正在執行重構？走開就是了，繼續在 Telegram 上監控和回覆。
- **隨時切換回電腦** — tmux 會話從未中斷，直接 `tmux attach` 就能回到終端，完整的滾動歷史和上下文都在。
- **並行運行多個會話** — 每個 Telegram 話題對應一個獨立的 tmux 視窗，一個聊天組裡就能管理多個專案。

市面上其他 Claude Code Telegram Bot 通常封裝 Claude Code SDK 來創建獨立的 API 會話，這些會話是隔離的 — 你無法在終端裡恢復它們。BaoBaoClaude 採取了不同的方式：它只是 tmux 之上的一個薄控制層，終端始終是數據源，你永遠不會失去切換回去的能力。

## 功能特性

### 遠程會話控制

- **基於話題的會話** — 每個 Telegram 話題 1:1 映射到一個 tmux 視窗和 Claude 會話
- **即時通知** — 接收助手回覆、思考過程、工具調用/結果、本地命令輸出的 Telegram 訊息
- **互動式 UI** — 通過內聯鍵盤操作 AskUserQuestion、ExitPlanMode 和權限提示
- **發送訊息** — 通過 tmux 按鍵將文字轉發給 Claude Code
- **斜槓命令轉發** — 任何 `/command` 直接發送給 Claude Code（如 `/clear`、`/compact`、`/cost`）
- **創建新會話** — 通過目錄瀏覽器從 Telegram 啟動 Claude Code 會話
- **關閉會話** — 關閉話題自動終止關聯的 tmux 視窗
- **訊息歷史** — 分頁瀏覽對話歷史（預設顯示最新）
- **Hook 會話追蹤** — 通過 `SessionStart` hook 自動關聯 tmux 視窗與 Claude 會話
- **持久化狀態** — 話題綁定和讀取偏移量在重啟後保持

### 人格與記憶系統

- **靈魂定義（SOUL.md）** — 定義 Claude 的性格特質與溝通風格
- **身份識別（IDENTITY.md）** — 設定名稱、Emoji、角色描述
- **用戶檔案（USER.md）** — 記錄用戶偏好與個人資訊
- **長期記憶（MEMORY.md）** — 經過整理的持久知識
- **每日記憶（memory/*.md）** — 自動累積的日常記憶，SQLite 索引支援快速搜尋
- **工作空間管理** — 自動組裝 CLAUDE.md，連結專案目錄

## 前置要求

- **tmux** — 需要安裝並在 PATH 中可用
- **Claude Code** — CLI 工具（`claude`）需要已安裝

## 安裝

### 方式一：從 GitHub 安裝（推薦）

```bash
# 使用 uv（推薦）
uv tool install git+https://github.com/howarddp/BaoBaoClaude.git

# 或使用 pipx
pipx install git+https://github.com/howarddp/BaoBaoClaude.git
```

### 方式二：從源碼安裝

```bash
git clone https://github.com/howarddp/BaoBaoClaude.git
cd BaoBaoClaude
uv sync
```

## 配置

**1. 創建 Telegram Bot 並啟用話題模式：**

1. 與 [@BotFather](https://t.me/BotFather) 對話創建新 Bot 並獲取 Token
2. 打開 @BotFather 的個人頁面，點擊 **Open App** 啟動小程式
3. 選擇你的 Bot，進入 **Settings** > **Bot Settings**
4. 啟用 **Threaded Mode**（話題模式）

**2. 配置環境變數：**

使用互動式設置（推薦）：

```bash
baobaobot setup
```

會依序引導你輸入 Bot Token、用戶 ID、Claude 命令，並自動建立 `.env`、初始化工作空間、安裝 Hook。

或手動創建 `~/.baobaobot/.env`：

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

**必填項：**

| 變數 | 說明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 從 @BotFather 獲取的 Bot Token |
| `ALLOWED_USERS` | 逗號分隔的 Telegram 用戶 ID |

**可選項：**

| 變數 | 預設值 | 說明 |
|---|---|---|
| `BAOBAOBOT_DIR` | `~/.baobaobot` | 配置/狀態目錄（`.env` 從此目錄載入） |
| `TMUX_SESSION_NAME` | `baobaobot` | tmux 會話名稱 |
| `CLAUDE_COMMAND` | `claude` | 新視窗中運行的命令 |
| `MONITOR_POLL_INTERVAL` | `2.0` | 輪詢間隔（秒） |
| `WORKSPACE_DIR` | `~/.baobaobot/workspace` | 工作空間目錄 |
| `MEMORY_KEEP_DAYS` | `30` | 每日記憶保留天數 |
| `RECENT_MEMORY_DAYS` | `7` | 納入 CLAUDE.md 的近期記憶天數 |
| `AUTO_ASSEMBLE` | `true` | 會話啟動時自動組裝 CLAUDE.md |

> 如果在 VPS 上運行且沒有互動終端來批准權限，可以考慮：
> ```
> CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

## Hook 設置

> 如果已通過 `baobaobot setup` 完成設置，Hook 已自動安裝，可跳過此段。

手動安裝：

```bash
baobaobot hook --install
```

或手動添加到 `~/.claude/settings.json`：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "baobaobot hook", "timeout": 5 }]
      }
    ]
  }
}
```

Hook 會將視窗-會話映射寫入 `$BAOBAOBOT_DIR/session_map.json`（預設 `~/.baobaobot/`），這樣 Bot 就能自動追蹤每個 tmux 視窗中運行的 Claude 會話 — 即使在 `/clear` 或會話重啟後也能保持關聯。

## 使用方法

```bash
# 首次設置（互動式引導，含初始化 + Hook 安裝）
baobaobot setup

# 啟動 Bot
baobaobot

# 從源碼安裝的
uv run baobaobot
```

### 命令

**CLI 命令：**

| 命令 | 說明 |
|---|---|
| `baobaobot setup` | 互動式首次設置（建 `.env`、初始化工作空間、安裝 Hook） |
| `baobaobot init` | 初始化工作空間目錄 |
| `baobaobot hook --install` | 安裝 Claude Code SessionStart Hook |
| `baobaobot` | 啟動 Telegram Bot |

**Bot 命令：**

| 命令 | 說明 |
|---|---|
| `/start` | 顯示歡迎訊息 |
| `/history` | 當前話題的訊息歷史 |
| `/screenshot` | 截取終端畫面 |
| `/esc` | 發送 Escape 鍵中斷 Claude |
| `/soul` | 查看/編輯靈魂定義 |
| `/identity` | 查看/編輯身份識別 |
| `/profile` | 查看/編輯用戶檔案 |
| `/memory` | 查看記憶 |
| `/forget` | 刪除記憶 |
| `/workspace` | 工作空間管理 |
| `/rebuild` | 重新組裝 CLAUDE.md |

**Claude Code 命令（通過 tmux 轉發）：**

| 命令 | 說明 |
|---|---|
| `/clear` | 清除對話歷史 |
| `/compact` | 壓縮對話上下文 |
| `/cost` | 顯示 Token/費用統計 |
| `/help` | 顯示 Claude Code 幫助 |

其他未識別的 `/command` 也會原樣轉發給 Claude Code（如 `/review`、`/doctor`、`/init`）。

### 話題工作流

**1 話題 = 1 視窗 = 1 會話。** Bot 在 Telegram 論壇（話題）模式下運行。

**創建新會話：**

1. 在 Telegram 群組中創建新話題
2. 在話題中發送任意訊息
3. 彈出目錄瀏覽器 — 選擇專案目錄
4. 自動創建 tmux 視窗，啟動 `claude`，並轉發待處理的訊息

**發送訊息：**

話題綁定會話後，直接在話題中發送文字即可 — 文字會通過 tmux 按鍵轉發給 Claude Code。

**關閉會話：**

在 Telegram 中關閉（或刪除）話題，關聯的 tmux 視窗會自動終止，綁定也會被移除。

### 訊息歷史

使用內聯按鈕導航：

```
📋 [專案名稱] Messages (42 total)

───── 14:32 ─────

👤 修復登入 bug

───── 14:33 ─────

我來排查這個登入 bug...

[◀ Older]    [2/9]    [Newer ▶]
```

### 通知

監控器每 2 秒輪詢會話 JSONL 檔案，並發送以下通知：
- **助手回覆** — Claude 的文字回覆
- **思考過程** — 以可展開引用區塊顯示
- **工具調用/結果** — 帶統計摘要（如 "Read 42 lines"、"Found 5 matches"）
- **本地命令輸出** — 命令的標準輸出（如 `git status`），前綴為 `❯ command_name`

通知發送到綁定了該會話視窗的話題中。

## 在 tmux 中運行 Claude Code

### 方式一：通過 Telegram 創建（推薦）

1. 在 Telegram 群組中創建新話題
2. 發送任意訊息
3. 從瀏覽器中選擇專案目錄

### 方式二：手動創建

```bash
tmux attach -t baobaobot
tmux new-window -n myproject -c ~/Code/myproject
# 在新視窗中啟動 Claude Code
claude
```

視窗必須在 `baobaobot` tmux 會話中（可通過 `TMUX_SESSION_NAME` 配置）。Claude 啟動時 Hook 會自動將其註冊到 `session_map.json`。

## 架構概覽

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Topic ID   │ ───▶ │ Window ID   │ ───▶ │ Session ID  │
│  (Telegram) │      │ (tmux @id)  │      │  (Claude)   │
└─────────────┘      └─────────────┘      └─────────────┘
     thread_bindings      session_map.json
     (state.json)         (由 hook 寫入)
```

**核心設計思路：**
- **話題為中心** — 每個 Telegram 話題綁定一個 tmux 視窗，話題就是會話列表
- **視窗 ID 為中心** — 所有內部狀態以 tmux 視窗 ID（如 `@0`、`@12`）為鍵，而非視窗名稱
- **基於 Hook 的會話追蹤** — Claude Code 的 `SessionStart` Hook 寫入 `session_map.json`；監控器每次輪詢讀取它以自動偵測會話變化
- **無 LLM 調用** — 所有智慧在 Claude Code 中，BaoBaoClaude 只負責檔案管理與 Telegram UI
- **雙層記憶** — MEMORY.md（長期、策展）+ memory/*.md（每日、自動），SQLite 索引提供快速搜尋
- **自動組裝 CLAUDE.md** — 從 SOUL/IDENTITY/USER/AGENTS/MEMORY 檔案自動組合

## 數據存儲

| 路徑 | 說明 |
|---|---|
| `$BAOBAOBOT_DIR/state.json` | 話題綁定、視窗狀態、顯示名稱、每用戶讀取偏移量 |
| `$BAOBAOBOT_DIR/session_map.json` | Hook 生成的 `{tmux_session:window_id: {session_id, cwd, window_name}}` 映射 |
| `$BAOBAOBOT_DIR/monitor_state.json` | 每會話的監控位元組偏移量（防止重複通知） |
| `~/.claude/projects/` | Claude Code 會話數據（唯讀） |

## 工作空間目錄

```
~/.baobaobot/                   # 根目錄 (BAOBAOBOT_DIR)
├── .env                     # Bot 配置
├── state.json               # Bot 狀態（話題綁定、視窗狀態）
├── session_map.json         # Hook 生成的視窗→會話映射
├── monitor_state.json       # 每個 JSONL 檔案的輪詢進度
├── bin/                     # 記憶工具（跨工作空間共享）
│   ├── memory-search        # SQLite 記憶搜尋
│   └── memory-list          # 列出近期每日記憶
└── workspace/               # 預設工作空間 (WORKSPACE_DIR)
    ├── CLAUDE.md            # 自動組裝（人格 + 記憶）
    ├── SOUL.md              # 性格定義
    ├── IDENTITY.md          # 身份識別（名稱、Emoji、角色）
    ├── USER.md              # 用戶檔案
    ├── AGENTS.md            # 工作指令 + 記憶工具使用說明
    ├── MEMORY.md            # 長期記憶
    ├── memory/              # 每日記憶 (YYYY-MM-DD.md)
    ├── memory.db            # SQLite 記憶索引
    └── projects/            # 符號連結的專案目錄
```

## 檔案結構

```
src/baobaobot/
├── __init__.py              # 套件入口
├── main.py                  # CLI 調度器（setup / hook / init / bot 啟動）
├── hook.py                  # Hook 子命令，用於會話追蹤（+ --install）
├── config.py                # 環境變數配置（含工作空間設定）
├── bot.py                   # Telegram Bot 設置、命令處理、話題路由
├── session.py               # 會話管理、狀態持久化、訊息歷史
├── session_monitor.py       # JSONL 檔案監控（輪詢 + 變更偵測）
├── monitor_state.py         # 監控狀態持久化（位元組偏移量）
├── transcript_parser.py     # Claude Code JSONL 對話記錄解析
├── terminal_parser.py       # 終端面板解析（互動式 UI + 狀態行）
├── markdown_v2.py           # Markdown → Telegram MarkdownV2 轉換
├── telegram_sender.py       # 訊息拆分 + 同步 HTTP 發送
├── screenshot.py            # 終端文字 → PNG 圖片（支援 ANSI 顏色）
├── utils.py                 # 通用工具（原子 JSON 寫入、JSONL 輔助函式）
├── tmux_manager.py          # tmux 視窗管理（列出、創建、發送按鍵、終止）
├── fonts/                   # 截圖渲染用字體
├── workspace/               # 工作空間系統
│   ├── manager.py           # 目錄初始化、專案連結、bin/ 腳本安裝
│   ├── assembler.py         # CLAUDE.md 從源檔案組裝
│   ├── bin/                 # 部署到 ~/.baobaobot/bin/ 的腳本
│   │   ├── memory-search    # SQLite 記憶搜尋（供 Claude Code 使用）
│   │   └── memory-list      # 列出近期每日記憶
│   └── templates/           # 預設模板（SOUL/IDENTITY/USER/AGENTS/MEMORY.md）
├── persona/                 # 人格系統
│   ├── soul.py              # SOUL.md 讀寫
│   ├── identity.py          # IDENTITY.md 解析/更新
│   └── profile.py           # USER.md 解析/更新
├── memory/                  # 記憶系統
│   ├── db.py                # SQLite 索引（同步 .md → SQLite、搜尋、統計）
│   ├── manager.py           # MemoryManager（列出、搜尋、清理）
│   ├── daily.py             # 每日記憶檔案操作
│   └── search.py            # 純文字搜尋（降級方案）
└── handlers/
    ├── __init__.py           # Handler 模組匯出
    ├── callback_data.py      # 回呼數據常量（CB_* 前綴）
    ├── directory_browser.py  # 目錄瀏覽器內聯鍵盤 UI
    ├── history.py            # 訊息歷史分頁
    ├── interactive_ui.py     # 互動式 UI 處理（AskUser、ExitPlan、權限）
    ├── message_queue.py      # 每用戶訊息佇列 + worker（合併、限流）
    ├── message_sender.py     # safe_reply / safe_edit / safe_send 輔助函式
    ├── response_builder.py   # 回應訊息建構（格式化 tool_use、思考等）
    ├── status_polling.py     # 終端狀態行輪詢
    ├── persona_handler.py    # /soul、/identity 命令
    ├── profile_handler.py    # /profile 命令
    └── memory_handler.py     # /memory、/forget 命令
```
