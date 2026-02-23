# BaoBaoBot

通過 Telegram 遠程控制 Claude Code 會話 — 監控、互動、管理運行在 tmux 中的 AI 編程會話，並賦予 Claude 持久的人格與記憶。


## 為什麼是 BaoBaoBot？

Claude Code 運行在終端裡。當你離開電腦 — 通勤路上、躺在沙發上、或者只是不在工位 — 會話仍在繼續，但你失去了查看和控制的能力。

BaoBaoBot 讓你**通過 Telegram 無縫接管同一個會話**。核心設計思路是：它操作的是 **tmux**，而不是 Claude Code SDK。你的 Claude Code 進程始終在 tmux 視窗裡運行，BaoBaoBot 只是讀取它的輸出並向它發送按鍵。這意味著：

- **從電腦無縫切換到手機** — Claude 正在執行重構？走開就是了，繼續在 Telegram 上監控和回覆。
- **隨時切換回電腦** — tmux 會話從未中斷，直接 `tmux attach` 就能回到終端，完整的滾動歷史和上下文都在。
- **並行運行多個會話** — 每個 Telegram 話題對應一個獨立的 tmux 視窗，一個聊天組裡就能管理多個專案。

市面上其他 Claude Code Telegram Bot 通常封裝 Claude Code SDK 來創建獨立的 API 會話，這些會話是隔離的 — 你無法在終端裡恢復它們。BaoBaoBot 採取了不同的方式：它只是 tmux 之上的一個薄控制層，終端始終是數據源，你永遠不會失去切換回去的能力。

## 功能特性

### 遠程會話控制

- **基於話題的會話** — 每個 Telegram 話題 1:1 映射到一個 tmux 視窗和 Claude 會話
- **即時通知** — 接收助手回覆、思考過程、工具調用/結果、本地命令輸出的 Telegram 訊息
- **互動式 UI** — 通過內聯鍵盤操作 AskUserQuestion、ExitPlanMode 和權限提示
- **發送訊息** — 通過 tmux 按鍵將文字轉發給 Claude Code
- **斜槓命令轉發** — 任何 `/command` 直接發送給 Claude Code（如 `/clear`、`/compact`、`/cost`）
- **創建新會話** — 在話題中發送第一條訊息即自動創建工作空間和 Claude 會話
- **關閉會話** — 關閉話題自動終止關聯的 tmux 視窗
- **訊息歷史** — 分頁瀏覽對話歷史（預設顯示最新）
- **Hook 會話追蹤** — 通過 `SessionStart` hook 自動關聯 tmux 視窗與 Claude 會話
- **持久化狀態** — 話題綁定和讀取偏移量在重啟後保持
- **訊息消音** — Claude 輸出以 `[NO_NOTIFY]` 開頭的內容不會發送到 Telegram

### 人格與記憶系統

- **靈魂定義（AGENTSOUL.md）** — 定義 Claude 的名稱、角色、Emoji、性格特質與溝通風格
- **用戶檔案** — 每位用戶獨立檔案（`shared/users/<user_id>.md`），記錄偏好與個人資訊
- **每日記憶** — `memory/daily/YYYY-MM/YYYY-MM-DD.md`，自動累積的日常記憶
- **長期記憶** — `memory/experience/<topic>.md`，經過整理的主題記憶
- **記憶摘要** — `memory/summaries/`，自動生成的小時摘要
- **SQLite 全文搜尋** — FTS5 索引提供快速記憶搜尋
- **記憶合併** — 每週自動將舊的每日記憶和摘要合併到長期記憶
- **工作空間管理** — 自動組裝 CLAUDE.md，連結專案目錄

### 排程系統（Cron）

- **定時任務** — 支援 cron 表達式、`every:30m` 間隔語法、`at:ISO-datetime` 一次性任務
- **系統任務** — 自動建立每小時摘要和每週記憶合併任務
- **`/clear` 自動摘要** — 清除會話前自動觸發摘要保存
- **指數退避** — 連續失敗時自動延長重試間隔

### 語音訊息

- **語音轉文字** — 可選安裝 `faster-whisper`，收到語音訊息自動轉錄
- **預覽確認** — 轉錄後顯示預覽，可發送、編輯或取消

### 檔案處理

- **接收檔案** — 下載到工作空間 `tmp/` 目錄，可讓 Claude 讀取分析
- **發送檔案** — Claude 輸出 `[SEND_FILE:/path]` 可將檔案上傳到 Telegram（50MB 限制）

### Bash 捕獲

- **`!command` 語法** — 以 `!` 開頭的訊息在背景執行 Shell 命令，自動捕獲輸出

### 訊息詳細度控制

- **三級控制** — quiet（僅最終回覆）、normal（回覆 + 工具摘要）、verbose（全部內容）
- **按話題設定** — 每個話題可獨立設定詳細度

### 多 Agent 支援

- **單 tmux 共享** — 多個 Agent 共用一個 `baobaobot` tmux session
- **獨立配置** — 每個 Agent 有自己的 Bot Token、工作空間和狀態
- **視窗前綴** — 多 Agent 時視窗名稱自動加上 `agent_name/` 前綴

### Skills 系統

Claude Code 可透過工作空間中的 Skills 使用以下功能：

| Skill | 說明 |
|---|---|
| `memory-search` | 搜尋記憶（SQLite FTS5） |
| `memory-list` | 列出近期每日記憶 |
| `memory-save` | 主動保存記憶 |
| `cron-add` | 新增排程任務 |
| `cron-list` | 列出排程任務 |
| `cron-remove` | 移除排程任務 |
| `weather` | 查詢天氣 |
| `google-places` | Google 地點搜尋 |
| `google-directions` | Google 路線規劃 |
| `google-geocoding` | Google 地理編碼 |

## 前置要求

- **tmux** — 需要安裝並在 PATH 中可用
- **Claude Code** — CLI 工具（`claude`）需要已安裝

## 安裝

### 方式一：從 GitHub 安裝（推薦）

```bash
# 使用 uv（推薦）
uv tool install git+https://github.com/howarddp/BaoBaoBot.git

# 或使用 pipx
pipx install git+https://github.com/howarddp/BaoBaoBot.git
```

### 方式二：從源碼安裝

```bash
git clone https://github.com/howarddp/BaoBaoBot.git
cd BaoBaoBot
uv sync
```

### 可選：語音轉錄

```bash
uv pip install faster-whisper>=1.0.0
```

## 配置

**1. 創建 Telegram Bot 並啟用話題模式：**

1. 與 [@BotFather](https://t.me/BotFather) 對話創建新 Bot 並獲取 Token
2. 打開 @BotFather 的個人頁面，點擊 **Open App** 啟動小程式
3. 選擇你的 Bot，進入 **Settings** > **Bot Settings**
4. 啟用 **Threaded Mode**（話題模式）

**2. 配置環境變數：**

首次運行 `baobaobot` 時會自動引導你完成設置（輸入 Bot Token、用戶 ID、Claude 命令等），並自動建立 `.env`、初始化工作空間、安裝 Hook。

設置完成後，配置保存在 `~/.baobaobot/settings.toml`（設定）和 `~/.baobaobot/.env`（密鑰）。所有可調設定及預設值都會列在 `settings.toml` 中，可直接編輯。

要添加更多 Agent：

```bash
baobaobot add-agent
```

**配置範例（settings.toml）：**

```toml
[global]
allowed_users = [123456789]
claude_command = "claude"
locale = "zh-TW"
recent_memory_days = 7
monitor_poll_interval = 2.0
# whisper_model = "small"
# cron_default_tz = "Asia/Taipei"

[[agents]]
name = "baobao"
bot_token_env = "BAOBAO_BOT_TOKEN"
mode = "forum"   # 或 "group"
# allowed_users = [...]  # 覆蓋 global 設定
```

**環境變數：**

| 變數 | 預設值 | 說明 |
|---|---|---|
| `BAOBAOBOT_DIR` | `~/.baobaobot` | 配置/狀態目錄 |

> 如果在 VPS 上運行且沒有互動終端來批准權限，可在 `settings.toml` 中設定：
> ```toml
> [global]
> claude_command = "IS_SANDBOX=1 claude --dangerously-skip-permissions"
> ```

## Hook 設置

> 如果已通過首次 `baobaobot` 設置完成，Hook 已自動安裝，可跳過此段。

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
# 啟動 Bot（首次運行自動引導設置，自動在 tmux session 中運行）
baobaobot

# 從源碼安裝的
uv run baobaobot

# 在前台運行（不自動建立 tmux，用於除錯）
baobaobot --foreground
baobaobot -f
```

啟動時，`baobaobot` 會自動建立 tmux session `baobaobot` 並在其中運行，這樣關閉終端後 Bot 仍會持續運行。使用 `--foreground` / `-f` 可在當前終端直接運行。

### 命令

**CLI 命令：**

| 命令 | 說明 |
|---|---|
| `baobaobot` | 啟動 Telegram Bot（首次運行自動設置，自動在 tmux 中運行） |
| `baobaobot add-agent` | 互動式添加新 Agent 到 settings.toml |
| `baobaobot hook --install` | 安裝 Claude Code SessionStart Hook |
| `baobaobot --foreground` | 在前台啟動（不建立 tmux） |

**Bot 命令：**

| 命令 | 說明 |
|---|---|
| `/history` | 當前話題的訊息歷史（分頁瀏覽） |
| `/screenshot` | 截取終端畫面（附帶控制按鍵） |
| `/esc` | 發送 Escape 鍵中斷 Claude |
| `/forcekill` | 強制終止並重啟 Claude 進程 |
| `/agentsoul` | 查看/編輯靈魂定義（AGENTSOUL.md） |
| `/profile` | 查看/編輯用戶檔案 |
| `/memory` | 查看/搜尋記憶 |
| `/forget` | 刪除記憶 |
| `/workspace` | 顯示當前工作空間路徑 |
| `/rebuild` | 重新組裝 CLAUDE.md |
| `/cron` | 管理排程任務（add/remove/enable/disable/run/status） |
| `/verbosity` | 設定訊息詳細度（quiet/normal/verbose） |

**Claude Code 命令（通過 tmux 轉發）：**

| 命令 | 說明 |
|---|---|
| `/clear` | 清除對話歷史（自動觸發摘要） |
| `/compact` | 壓縮對話上下文 |
| `/cost` | 顯示 Token/費用統計 |
| `/help` | 顯示 Claude Code 幫助 |

其他未識別的 `/command` 也會原樣轉發給 Claude Code（如 `/review`、`/doctor`、`/init`）。

### 話題工作流

**1 話題 = 1 視窗 = 1 會話。** Bot 支援 Telegram 論壇（話題）模式和普通群組模式。

**創建新會話：**

1. 在 Telegram 群組中創建新話題（話題名稱會自動成為 tmux 視窗名稱）
2. 在話題中發送任意訊息
3. 自動創建工作空間目錄、組裝 CLAUDE.md、啟動 tmux 視窗中的 `claude`
4. 待處理的訊息自動轉發

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
3. 自動創建工作空間並啟動 Claude

### 方式二：手動創建

```bash
tmux attach -t baobaobot
tmux new-window -n myproject -c ~/Code/myproject
# 在新視窗中啟動 Claude Code
claude
```

視窗必須在 `baobaobot` tmux 會話中。Claude 啟動時 Hook 會自動將其註冊到 `session_map.json`。

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
- **無 LLM 調用** — 所有智慧在 Claude Code 中，BaoBaoBot 只負責檔案管理與 Telegram UI
- **雙層記憶** — experience/（長期、策展）+ daily/（每日、自動）+ summaries/（小時摘要），SQLite FTS5 索引提供快速搜尋
- **記憶合併** — 每週自動將舊的每日記憶和摘要合併到長期記憶
- **自動組裝 CLAUDE.md** — 從 AGENTS.md + AGENTSOUL.md + 記憶上下文自動組合

## 數據存儲

| 路徑 | 說明 |
|---|---|
| `$BAOBAOBOT_DIR/settings.toml` | Agent 配置（名稱、Token、模式、全域設定） |
| `$BAOBAOBOT_DIR/.env` | Bot Token 等密鑰 |
| `$BAOBAOBOT_DIR/shared/AGENTSOUL.md` | Agent 靈魂定義 |
| `$BAOBAOBOT_DIR/shared/AGENTS.md` | 工作指令（系統同步） |
| `$BAOBAOBOT_DIR/shared/users/<id>.md` | 用戶檔案 |
| `$BAOBAOBOT_DIR/agents/<name>/state.json` | 話題綁定、視窗狀態、顯示名稱 |
| `$BAOBAOBOT_DIR/agents/<name>/session_map.json` | Hook 生成的視窗→會話映射 |
| `$BAOBAOBOT_DIR/agents/<name>/monitor_state.json` | 每會話的監控位元組偏移量 |
| `~/.claude/projects/` | Claude Code 會話數據（唯讀） |

## 工作空間目錄

```
~/.baobaobot/                        # 根目錄 (BAOBAOBOT_DIR)
├── .env                             # Bot 密鑰
├── settings.toml                    # Agent 配置
├── shared/                          # 跨工作空間共享檔案
│   ├── AGENTSOUL.md                 # 靈魂定義（名稱、角色、Emoji、性格）
│   ├── AGENTS.md                    # 工作指令（系統同步）
│   ├── bin/                         # 記憶/排程工具
│   │   ├── memory-search            # SQLite 記憶搜尋
│   │   ├── memory-list              # 列出近期每日記憶
│   │   ├── memory-save              # 保存記憶
│   │   ├── cron-add                 # 新增排程任務
│   │   ├── cron-list                # 列出排程任務
│   │   └── cron-remove              # 移除排程任務
│   └── users/                       # 用戶檔案
│       └── <user_id>.md
└── agents/<name>/                   # 每個 Agent 的數據
    ├── state.json                   # 話題綁定、視窗狀態
    ├── session_map.json             # 視窗→會話映射（hook 寫入）
    ├── monitor_state.json           # 輪詢進度（位元組偏移量）
    └── workspace_<topic>/           # 每個話題的工作空間
        ├── CLAUDE.md                # 自動組裝（勿手動編輯）
        ├── memory/                  # 記憶目錄
        │   ├── daily/               # 每日記憶 (YYYY-MM/YYYY-MM-DD.md)
        │   ├── experience/          # 長期主題記憶
        │   ├── summaries/           # 自動小時摘要
        │   └── attachments/         # 附件
        ├── memory.db                # SQLite FTS5 記憶索引
        ├── cron/jobs.json           # 排程任務
        ├── projects/                # 符號連結的專案目錄
        ├── tmp/                     # 下載的檔案
        └── .claude/skills/          # Claude Code Skills
```

## 檔案結構

```
src/baobaobot/
├── main.py                  # CLI 調度器（hook / add-agent / bot 啟動 + 自動 tmux）
├── settings.py              # TOML 多 Agent 配置（AgentConfig + load_settings）
├── bot.py                   # Telegram Bot 設置、命令處理、話題路由
├── agent_context.py         # AgentContext 資料類
├── router.py                # Router ABC（論壇/群組路由）
├── routers/
│   ├── forum.py             # ForumRouter（話題模式）
│   └── group.py             # GroupRouter（群組模式）
├── session.py               # 會話管理、狀態持久化、訊息歷史
├── session_monitor.py       # JSONL 檔案監控（輪詢 + 變更偵測）
├── monitor_state.py         # 監控狀態持久化（位元組偏移量）
├── tmux_manager.py          # tmux 視窗管理（列出、創建、發送按鍵、終止）
├── hook.py                  # Hook 子命令（會話追蹤 + --install）
├── transcript_parser.py     # Claude Code JSONL 對話記錄解析
├── terminal_parser.py       # 終端面板解析（互動式 UI + 狀態行）
├── markdown_v2.py           # Markdown → Telegram MarkdownV2 轉換
├── telegram_sender.py       # 訊息拆分 + 同步 HTTP 發送
├── screenshot.py            # 終端文字 → PNG 圖片（支援 ANSI 顏色）
├── transcribe.py            # 語音轉錄（faster-whisper）
├── locale_utils.py          # 時區 → 語系映射
├── utils.py                 # 通用工具（原子 JSON 寫入、JSONL 輔助函式）
├── workspace/               # 工作空間系統
│   ├── manager.py           # 目錄初始化、專案連結、bin/skills 部署
│   ├── assembler.py         # CLAUDE.md 從源檔案組裝
│   ├── bin/                 # 部署到 shared/bin/ 的腳本
│   ├── skills/              # 部署到 .claude/skills/ 的 SKILL.md
│   └── templates/           # 預設模板（AGENTSOUL/AGENTS/USER.md）
├── persona/                 # 人格系統
│   ├── agentsoul.py         # AGENTSOUL.md 讀寫解析
│   └── profile.py           # 多用戶檔案（shared/users/）
├── memory/                  # 記憶系統
│   ├── db.py                # SQLite FTS5 索引（schema v4）
│   ├── manager.py           # MemoryManager（列出、搜尋、清理）
│   ├── daily.py             # 每日記憶檔案操作
│   ├── search.py            # 純文字搜尋（降級方案）
│   └── utils.py             # 前置資料解析、標籤處理
├── cron/                    # 排程系統
│   ├── service.py           # CronService（asyncio 計時器迴圈）
│   ├── store.py             # JSON 持久化任務儲存
│   ├── schedule.py          # 計算下次執行時間
│   ├── parse.py             # 排程字串解析
│   └── types.py             # CronJob、CronSchedule 資料類
└── handlers/                # Telegram 處理器
    ├── callback_data.py     # 回呼數據常量
    ├── message_queue.py     # 每用戶訊息佇列 + worker
    ├── message_sender.py    # safe_reply / safe_edit / safe_send
    ├── history.py           # 訊息歷史分頁
    ├── interactive_ui.py    # 互動式 UI（AskUser、ExitPlan、權限）
    ├── status_polling.py    # 終端狀態行輪詢
    ├── response_builder.py  # 回應訊息建構
    ├── directory_browser.py # 目錄瀏覽器 UI
    ├── cleanup.py           # 話題關閉/刪除清理
    ├── persona_handler.py   # /agentsoul 命令
    ├── profile_handler.py   # /profile 命令
    ├── memory_handler.py    # /memory、/forget 命令
    ├── cron_handler.py      # /cron 命令
    └── verbosity_handler.py # /verbosity 命令
```
