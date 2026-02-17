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

## 記憶工具

搜尋過去的記憶時，使用以下工具（不要自己 grep memory 目錄）：

- **搜尋記憶**：`~/.baobaobot/bin/memory-search "關鍵字"`
- **搜尋最近 N 天**：`~/.baobaobot/bin/memory-search "關鍵字" --days 7`
- **列出近期記憶**：`~/.baobaobot/bin/memory-list`
- **列出更多天**：`~/.baobaobot/bin/memory-list --days 30`

寫入記憶時，直接寫 memory/YYYY-MM-DD.md 檔案（不需要用工具）。

## 記憶管理

### 記憶格式（memory/YYYY-MM-DD.md）
- 用 ## 標題分類（對話摘要、決定、待辦、觀察）
- 保持簡潔，每則記憶 1-2 行
- 記錄時間戳在檔名中（日期）

### 長期記憶（MEMORY.md）
- 重要的用戶偏好和決定
- 持續性的專案資訊
- 定期整理，移除過時的資訊
