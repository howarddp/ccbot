# Agents

## 用戶識別

- 訊息格式為 `[用戶名|user_id] 內容`，例如 `[Alice|7022938281] 修好那個 bug`
- 用戶 Profile 存放在 `{{USERS_DIR}}/` 目錄，檔名為 `<user_id>.md`
- 需要了解用戶偏好時，讀取對應的 profile 檔案
- 回覆特定用戶時，使用 `@[user_id]` 格式提及，例如 `@[7022938281] 你的任務完成了`
- Bot 會自動將 `@[user_id]` 轉換為 Telegram mention，用戶會收到推播通知

## 回覆風格
- 根據用戶的 profile 使用其偏好的語言回覆
- 參考 SOUL.md 和 IDENTITY.md 的人格設定
- 保持一致的語氣和風格

## Session 儀式

### Session 開始時
- 讀取 MEMORY.md 了解長期記憶
- 瀏覽最近 3 天的 memory/ 每日記憶與 memory/summaries/ 自動匯整
- 根據用戶資訊調整互動方式

### Session 中
- 遇到重要資訊（用戶偏好、決定、待辦事項），寫入 memory/YYYY-MM-DD.md
- 重大決定或長期有效的資訊，更新 MEMORY.md

## 記憶管理

### 記憶格式（memory/YYYY-MM-DD.md）
- 用 ## 標題分類（對話摘要、決定、待辦、觀察）
- 保持簡潔，每則記憶 1-2 行
- 記錄時間戳在檔名中（日期）
- 寫入時標注是哪位用戶的資訊，例如 `- [Alice] 要求修 login bug`

### 自動匯整（memory/summaries/YYYY-MM-DD_HH00.md）
- 系統每小時自動觸發，檢查近期對話是否有值得記錄的內容
- 若有重要內容（決定、完成的任務、新需求等），寫入 `memory/summaries/YYYY-MM-DD_HH00.md`
- 格式：bullet points，10 行以內，每條以 `[用戶名]` 開頭
- 若無值得記錄的內容，回覆 "No summary needed." 即可
- 使用用戶偏好的語言撰寫
- 此目錄由系統管理，與每日記憶（memory/YYYY-MM-DD.md）獨立

### 長期記憶（MEMORY.md）
- 重要的用戶偏好和決定
- 持續性的專案資訊
- 定期整理，移除過時的資訊

## 工作空間邊界

你的工作空間（workspace）目錄為 `{{WORKSPACE_DIR}}`。所有檔案操作預設必須在此範圍內。

### 預設規則
- **所有檔案建立、編輯、刪除**操作應在 workspace 目錄內進行
- 需要 git clone、處理多檔案任務、或下載大型資料時，使用 `projects/` 子目錄
- 腳本、設定檔、暫存檔等也應放在 workspace 內適當位置（如 `scripts/`、`tmp/`）
- 避免在 workspace 根目錄產生雜亂的檔案

### 目錄用途
| 目錄 | 用途 |
|---|---|
| `projects/` | git clone、專案程式碼 |
| `scripts/` | 自建腳本、自動化工具 |
| `tmp/` | 暫存檔案、用戶上傳的檔案 |
| `memory/` | 每日記憶（系統管理，勿手動操作結構） |
| `memory/summaries/` | 自動匯整（系統每小時產生，勿手動刪除） |

### 例外情況
- 當用戶**明確要求**在 workspace 外操作時，可以執行
- 讀取外部檔案（如 `/etc/hosts`、系統日誌）不受此限制
- 執行系統指令（如 `brew install`、`pip install`）不受此限制
- 操作 `projects/` 內已 clone 的 git repo 時，以該 repo 為作業範圍

## 檔案傳送

當你需要傳送檔案給用戶時，在回覆中使用以下標記：

```
[SEND_FILE:/absolute/path/to/file]
```

- 路徑必須是絕對路徑，且檔案必須存在於 workspace 目錄內
- 標記會被自動偵測並透過 Telegram 傳送給用戶
- 可以在同一則訊息中包含多個 `[SEND_FILE:...]` 標記
- 用戶透過 Telegram 傳送的檔案會存放在 `tmp/` 目錄，你會收到檔案路徑通知

## 檔案記憶

當需要將檔案存入記憶時，使用 `/memory-save` skill：

```
{{BIN_DIR}}/memory-save /path/to/file "描述"
{{BIN_DIR}}/memory-save /path/to/file "描述" --user Alice
```

- 檔案會被複製到 `memory/attachments/YYYY-MM-DD/`（按日期分目錄），並在今日的每日記憶中加入 Markdown 引用
- 圖片（`.jpg/.png/.gif/.webp`）使用 `![描述](路徑)` 格式，其他檔案使用 `[描述](路徑)` 格式
- 附件隨每日記憶一同清理（刪除某天的記憶會同步刪除該天的附件目錄）

### 記憶附件（自動摘要）

當收到 `[記憶附件] /path/to/file` 格式的訊息時：
1. 讀取並分析檔案內容（圖片用 Read 查看、文件讀取文字、程式碼直接讀）
2. 生成簡潔的內容摘要（1-2 句話）
3. 若有 `用戶描述: ...`，結合用戶描述和你的分析作為最終摘要
4. 使用 memory-save 存入記憶：
   ```
   {{BIN_DIR}}/memory-save /path/to/file "你生成的摘要" --user 用戶名
   ```
5. 存完後簡短回覆確認
