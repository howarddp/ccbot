---
name: cron-add
description: 新增排程或提醒。當用戶說「提醒我」「每天」「幾點」等需要定時任務時使用。
---

新增排程任務。

用法：`{{BIN_DIR}}/cron-add "$0" "$1" [--name NAME] [--tz TZ]`

第一個參數是 schedule，第二個參數是 message。

Schedule 格式：
| 格式 | 範例 | 說明 |
|------|------|------|
| `at:<ISO時間>` | `at:2026-02-28T09:00` | 一次性，到時自動刪除 |
| `every:<數字><單位>` | `every:30m`, `every:2h`, `every:1d` | 固定間隔（s/m/h/d） |
| cron 表達式 | `"0 9 * * *"`, `"0 9 * * 1-5"` | 標準 5 欄位 cron |

注意：
- `at:` 類型預設執行後自動刪除（一次性提醒）
- 建立排程後，同時也記到 memory（如待辦事項），確保雙重保障
- 時區預設 UTC，台灣時間用 `--tz Asia/Taipei`
