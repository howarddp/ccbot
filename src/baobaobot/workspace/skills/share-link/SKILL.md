---
name: share-link
description: "Generate signed share/upload URLs for files, directories, and uploads via Cloudflare Tunnel. Use when: user asks to share a file, send a download link, send multiple files at once, or request file uploads from others."
---

# Share Link Skill

Generate signed URLs to share files, directories, or upload pages via Cloudflare Tunnel.

## Inline Markers (Preferred)

Use these markers directly in your reply text. The bot auto-detects and replaces them with signed URLs:

```
[SHARE_LINK:/absolute/path/to/file]
[SHARE_LINK:/absolute/path/to/directory/]
[UPLOAD_LINK]
[UPLOAD_LINK:2h]
```

- File link → inline preview for images/PDF/HTML, download for other types
- Directory link → browsable file listing with preview, search, grid/list view, and individual download buttons
- Upload link → mobile-friendly upload page with drag-and-drop

## Sharing Multiple Files

When you need to share **multiple files at once** (files may be in different locations like memory/, tmp/, users/, projects/):

1. Create a temporary directory with date prefix:
   ```bash
   mkdir -p tmp/sharelink-YYYYMMDD-{short-description}/
   ```
2. Copy (not move) files from their various locations:
   ```bash
   cp memory/attachments/2026-02/photo.jpg tmp/sharelink-20260227-mixed/
   cp tmp/report.pdf tmp/sharelink-20260227-mixed/
   cp users/howard/notes.txt tmp/sharelink-20260227-mixed/
   ```
3. Share the directory with a single marker:
   ```
   [SHARE_LINK:{{WORKSPACE_DIR}}/tmp/sharelink-20260227-mixed/]
   ```

**Directory naming**: use `tmp/sharelink-{YYYYMMDD}-{short-description}/` format to avoid collisions and enable easy cleanup.

This is better than multiple `[SEND_FILE]` markers because:
- One clean link instead of multiple uploads flooding the chat
- Browsable page with preview thumbnails, search, and grid/list view
- No Telegram upload size limits (served via HTTP)
- Files from any location can be combined into one page

## CLI Usage

Alternatively, use the CLI tool to generate URLs programmatically:

### Share a File

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" /absolute/path/to/file.pdf
```

### Share a Directory

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" /absolute/path/to/directory/
```

### Generate an Upload Link

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" --upload
```

### Custom TTL

Default TTL is 30 minutes. Override with `--ttl`:

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" /path/to/file --ttl 2h    # 2 hours
"{{BIN_DIR}}/share-link" --upload --ttl 1d          # 1 day
"{{BIN_DIR}}/share-link" /path/to/file --ttl 10m    # 10 minutes
```

Supported units: `s` (seconds), `m` (minutes), `h` (hours), `d` (days).

## Environment

Requires two environment variables (set automatically when the bot is running):
- `SHARE_SECRET` — HMAC signing secret (auto-generated if not in `.env`)
- `SHARE_PUBLIC_URL` — Cloudflare Tunnel public URL (set at bot startup)

## Tips

- File links open inline for HTML, images, and PDFs; other types trigger download
- Upload links accept multiple files at once with progress indication
- All links are time-limited and cryptographically signed
- The share server only serves files within workspace directories
