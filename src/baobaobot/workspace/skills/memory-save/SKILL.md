---
name: memory-save
description: 將檔案存入記憶附件。當需要保存圖片、文件、產出物供未來回憶時使用。
---

將檔案複製到 `memory/attachments/` 並在今日的每日記憶中加入 Markdown 引用。

用法：`{{BIN_DIR}}/memory-save /path/to/file "描述"`

指定用戶：`{{BIN_DIR}}/memory-save /path/to/file "描述" --user Alice`

- 圖片（`.jpg/.png/.gif/.webp`）使用 `![描述](路徑)` 格式
- 其他檔案使用 `[描述](路徑)` 格式
- 附件隨每日記憶一同清理
