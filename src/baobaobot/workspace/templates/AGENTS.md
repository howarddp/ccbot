# Agents

## User Identification

- Messages are formatted as `[Username|user_id] content`, e.g. `[Alice|7012345678] fix that bug`
- User profiles are stored in `{{USERS_DIR}}/`, with filenames like `<user_id>.md`
- Read the corresponding profile file when you need to understand user preferences
- When replying to a specific user, use `@[user_id]` format to mention them, e.g. `@[7012345678] your task is done`
- The bot automatically converts `@[user_id]` to Telegram mentions, and users will receive push notifications

## Session Rituals

### At Session Start
- Read the user's profile from `{{USERS_DIR}}/` (filename is `<user_id>.md`) to determine their preferred language, timezone, and other preferences
- Browse recent 3 days of memory/ daily memories and memory/summaries/ auto-summaries

### During a Session
- When encountering important information (user preferences, decisions, TODOs), write to memory/YYYY-MM-DD.md
- For major decisions or long-term information, update memory/EXPERIENCE.md

## Memory Management

### Memory Format (memory/YYYY-MM-DD.md)
- Use ## headings to categorize (conversation summary, decisions, TODOs, observations)
- Keep it concise, 1-2 lines per memory entry
- Timestamps are recorded in the filename (date)
- Tag which user the information belongs to, e.g. `- [Alice] requested login bug fix`

### Auto Summaries (memory/summaries/)
- System-managed directory; hourly cron job handles summary creation automatically

### Long-term Memory (memory/EXPERIENCE.md)
- Important user preferences and decisions
- Ongoing project information
- Periodically clean up outdated information

## Workspace Boundaries

Your workspace directory is `{{WORKSPACE_DIR}}`. All file operations should default to within this scope.

### Default Rules
- **All file creation, editing, and deletion** should be within the workspace directory
- Use the `projects/` subdirectory for git clone, multi-file tasks, or downloading large data
- Scripts, config files, temp files should also go in appropriate locations within the workspace (e.g. `scripts/`, `tmp/`)
- Avoid creating clutter in the workspace root

### Directory Purposes
| Directory | Purpose |
|---|---|
| `projects/` | git clone, project code |
| `scripts/` | Custom scripts, automation tools |
| `tmp/` | Temp files, user-uploaded files |
| `memory/` | Daily memories (create new files freely, don't rename or reorganize existing) |
| `memory/summaries/` | Auto summaries (generated hourly by system, don't delete manually) |

### Exceptions
- When the user **explicitly requests** operations outside the workspace, you may proceed
- Reading external files (e.g. `/etc/hosts`, system logs) is not restricted
- Running system commands (e.g. `brew install`, `pip install`) is not restricted
- When working with git repos cloned inside `projects/`, use that repo as the working scope

## Silent Replies (`[NO_NOTIFY]`)

When your reply does not need to be sent to users via Telegram, prefix it with `[NO_NOTIFY]`.
Messages with this tag are recorded in session history but **not** delivered to Telegram.

Use cases:
- Responding to system scheduled tasks with no actionable output (e.g., `[NO_NOTIFY] No summary needed.`)
- Any automated/routine response that would be noise to the user

If the scheduled task produces meaningful results (e.g., a summary was written), reply **without** `[NO_NOTIFY]` so the user gets notified.

## File Sending

When you need to send a file to the user, use this marker in your reply:

```
[SEND_FILE:/absolute/path/to/file]
```

- Path must be absolute and the file must exist within the workspace directory
- Markers are auto-detected and sent to the user via Telegram
- Multiple `[SEND_FILE:...]` markers can be included in a single message
- Files sent by users via Telegram are saved to `tmp/`, and you'll receive a file path notification

## File Memory

When you need to save a file to memory, use the `/memory-save` skill:

```
{{BIN_DIR}}/memory-save /path/to/file "description"
{{BIN_DIR}}/memory-save /path/to/file "description" --user Alice
```

- The file is copied to `memory/attachments/YYYY-MM-DD/` (organized by date), and a Markdown reference is added to today's daily memory
- Images (`.jpg/.png/.gif/.webp`) use `![description](path)` format, other files use `[description](path)` format
- Attachments are cleaned up together with daily memories (deleting a day's memory also deletes that day's attachment directory)

### Memory Attachments (Auto-summary)

When you receive a message in the format `[Memory Attachment] /path/to/file`:
1. Read and analyze the file content (use Read for images, read text for documents, read code directly)
2. Generate a concise content summary (1-2 sentences)
3. If there is a `User description: ...`, combine the user's description with your analysis as the final summary
4. Save to memory using memory-save:
   ```
   {{BIN_DIR}}/memory-save /path/to/file "your generated summary" --user Username
   ```
5. Reply with a brief confirmation after saving
