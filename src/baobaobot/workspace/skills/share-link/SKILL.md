---
name: share-link
description: "Generate signed share/upload URLs for files, directories, and uploads via Cloudflare Tunnel. Use when: user asks to share a file, send a download link, or request file uploads from others."
---

# Share Link Skill

Generate signed URLs to share files, directories, or upload pages via Cloudflare Tunnel.

## Share a File

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" /absolute/path/to/file.pdf
```

Returns a public URL like `https://xxx.trycloudflare.com/f/{token}/{path}` that anyone can use to download the file.

## Share a Directory

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" /absolute/path/to/directory/
```

If the directory contains `index.html`, it will be served directly. Otherwise a file listing is shown.

## Generate an Upload Link

```bash
source "{{BIN_DIR}}/_load_env"
"{{BIN_DIR}}/share-link" --upload
```

Returns a public URL with a mobile-friendly upload page. Users can drag-and-drop or tap to select files, add an optional description, and upload. Uploaded files are saved to `tmp/uploads/`.

## Custom TTL

Default TTL is 30 minutes. Override with `--ttl`:

```bash
source "{{BIN_DIR}}/_load_env"
# 2 hours
"{{BIN_DIR}}/share-link" /path/to/file --ttl 2h
# 1 day
"{{BIN_DIR}}/share-link" --upload --ttl 1d
# 10 minutes
"{{BIN_DIR}}/share-link" /path/to/file --ttl 10m
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
