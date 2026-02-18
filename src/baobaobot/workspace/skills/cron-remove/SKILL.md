---
name: cron-remove
description: 移除排程或提醒。當用戶說「取消提醒」「不用了」時使用。先用 cron-list 查詢 job_id。
---

移除排程任務。需要先用 `/cron-list` 取得 job_id。

用法：`{{BIN_DIR}}/cron-remove $ARGUMENTS`
