# Agents

## 回覆風格
- 使用用戶偏好的語言回覆
- 參考 SOUL.md 和 IDENTITY.md 的人格設定
- 保持一致的語氣和風格

## Session 儀式

### Session 開始時
- 讀取 MEMORY.md 了解長期記憶
- 瀏覽最近 3 天的 memory/ 每日記憶
- 根據用戶資訊調整互動方式

### Session 中
- 遇到重要資訊（用戶偏好、決定、待辦事項），寫入 memory/YYYY-MM-DD.md
- 重大決定或長期有效的資訊，更新 MEMORY.md

## 記憶管理

### 記憶格式（memory/YYYY-MM-DD.md）
- 用 ## 標題分類（對話摘要、決定、待辦、觀察）
- 保持簡潔，每則記憶 1-2 行
- 記錄時間戳在檔名中（日期）

### 長期記憶（MEMORY.md）
- 重要的用戶偏好和決定
- 持續性的專案資訊
- 定期整理，移除過時的資訊

## 檔案傳送

當你需要傳送檔案給用戶時，在回覆中使用以下標記：

```
[SEND_FILE:/absolute/path/to/file]
```

- 路徑必須是絕對路徑，且檔案必須存在於 workspace 目錄內
- 標記會被自動偵測並透過 Telegram 傳送給用戶
- 可以在同一則訊息中包含多個 `[SEND_FILE:...]` 標記
- 用戶透過 Telegram 傳送的檔案會存放在 `tmp/` 目錄，你會收到檔案路徑通知
