# Agents

## User Identification

- Messages are formatted as `[Username|user_id] content`, e.g. `[Alice|7012345678] fix that bug`
- User profiles are stored in `{{USERS_DIR}}/`, with filenames like `<user_id>.md`
- Read the corresponding profile file when you need to understand user preferences
- When replying to a specific user, use `@[user_id]` format to mention them, e.g. `@[7012345678] your task is done`
- The bot automatically converts `@[user_id]` to Telegram mentions, and users will receive push notifications

## Language & Locale

System locale: `{{LOCALE}}`

- Use the system locale as the default language for all responses, memory entries, and cron messages
- Each user's profile has a `Language` field (locale code like `zh-TW`, `en-US`, `ja-JP`) that overrides the system locale for that user
- When writing to memory, use the language that matches the user's locale (or system locale if unset)
- Locale codes follow BCP 47 format: `{language}-{REGION}` (e.g. `zh-TW`, `en-US`, `ja-JP`)

## Session Rituals

### At Session Start
- Read the user's profile from `{{USERS_DIR}}/` (filename is `<user_id>.md`) to determine their preferred language, timezone, and other preferences
- Run `{{BIN_DIR}}/memory-list` to see recent daily memories and available tags
- Browse the relevant experience/ topic files listed in the Memory Context section below
- Use `{{BIN_DIR}}/memory-search <query>` to find specific past information when needed

### During a Session
- Use `{{BIN_DIR}}/memory-save "content"` to record important information (preferences, decisions, TODOs)
- Use `{{BIN_DIR}}/memory-save -e <topic> "content"` to record long-term knowledge to a specific topic
- Use `{{BIN_DIR}}/memory-save /path/to/file "description"` to save file attachments to memory

## Memory Management

### Daily Memory (memory/daily/YYYY-MM/YYYY-MM-DD.md)

Daily memory files are organized by month and use Obsidian-compatible YAML frontmatter:

```yaml
---
date: 2026-02-15
tags: []
---
```

Content guidelines:
- Use ## headings to categorize (conversation summary, decisions, TODOs, observations)
- Keep it concise, 1-2 lines per memory entry
- Tag which user the information belongs to, e.g. `- [Alice] requested login bug fix`
- Add tags to the frontmatter as appropriate: `#decision`, `#preference`, `#todo`, `#bug`, `#learning`, `#project`
- Daily memories are permanent — they are never deleted

### Auto Summaries (memory/summaries/)

Summary files use Obsidian-compatible YAML frontmatter:

```yaml
---
date: 2026-02-15
period: "14:00"
tags: []
---
```

- System-managed directory; hourly cron job handles summary creation automatically
- When creating summaries, always include the frontmatter block above
- `date`: the date being summarized (YYYY-MM-DD)
- `period`: the hour being summarized (HH:MM, 24h format)
- `tags`: relevant tags extracted from the summarized content

### Memory Consolidation
- A weekly system job reviews daily memories older than 21 days
- Important content is consolidated into `memory/experience/` topic files
- Daily files are preserved permanently as a complete record
- Consolidation extracts key insights into experience files without deleting originals

### Long-term Memory (memory/experience/)

Topic-based long-term memory files. Each file covers a single topic:
- Filename: use the system locale language for topic names (e.g. for `zh-TW`: `使用者偏好.md`; for `en-US`: `user-preferences.md`)
- English topic names use kebab-case (e.g. `user-preferences`); non-ASCII names use natural language directly
- New files are auto-created with YAML frontmatter (topic, tags, created, updated) via `memory-save -e`
- The `updated` field is automatically bumped on each append
- You decide when to create, update, or remove topic files
- Periodically clean up outdated information
- Files are Obsidian-compatible Markdown (wikilinks, tags OK)

### Tags

Use these tags in frontmatter or inline to categorize memories:
- `#decision` — architectural/design decisions made
- `#preference` — user preferences and settings
- `#todo` — tasks to follow up on
- `#bug` — bugs encountered and their fixes
- `#learning` — lessons learned, tips, patterns
- `#project` — project-specific context and status

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
| `memory/daily/` | Daily memories organized by month (memory/daily/YYYY-MM/YYYY-MM-DD.md) |
| `memory/experience/` | Long-term topic memories (one topic per file, locale language naming) |
| `memory/summaries/` | Auto summaries (generated hourly by system, don't delete manually) |
| `memory/attachments/` | File attachments organized by date |

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

## Memory Save

Use the `/memory-save` skill for all memory writes:

**Text memories:**
```
{{BIN_DIR}}/memory-save "important decision or observation" --user Alice
{{BIN_DIR}}/memory-save -e 使用者偏好 "long-term knowledge" --user Alice
```

**File attachments:**
```
{{BIN_DIR}}/memory-save /path/to/file "description" --user Alice
{{BIN_DIR}}/memory-save -e 專案筆記 /path/to/file "description"
```

- Auto-detects mode: existing file path → attachment, otherwise → text
- `-e TOPIC` saves to `memory/experience/<topic>.md` instead of daily (use locale language for topic names)
- Images (`.jpg/.png/.gif/.webp`) use `![description](path)` format
- Attachments are stored in `memory/attachments/YYYY-MM-DD/`

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
