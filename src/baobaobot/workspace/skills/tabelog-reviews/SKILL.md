---
name: tabelog-reviews
description: "Extract and display reviews from Tabelog (食べログ) for Japanese restaurants. Use when: user asks about restaurant reviews in Japan, wants detailed food ratings, or is planning dining in Japan. Supports both URL and name search."
---

# Tabelog Reviews Skill

Extract and display reviews from Tabelog (食べログ), Japan's most authoritative restaurant review platform.

## Usage

```bash
# Search by restaurant name
{{BIN_DIR}}/tabelog-reviews "一蘭 京都"

# Search with area for better results
{{BIN_DIR}}/tabelog-reviews "すきやばし次郎 銀座"

# Show reviews by URL
{{BIN_DIR}}/tabelog-reviews "https://tabelog.com/tokyo/A1301/A130101/13001234/"

# Limit number of reviews
{{BIN_DIR}}/tabelog-reviews "蟹道楽 道頓堀" -n 5

# JSON output
{{BIN_DIR}}/tabelog-reviews "一蘭 新宿" --json
```

## Options

- `-n, --count N` — Maximum reviews to show (default: 10)
- `--json` — Output raw JSON instead of formatted markdown

## Output Includes

- Restaurant name, overall rating, total review count
- Category ratings: 料理・味 / サービス / 雰囲気 / CP / 酒・ドリンク
- Individual reviews with: username, rating, title, body, date, visit info
- Tabelog page URL

## Rating Scale

Tabelog uses a stricter rating scale than Google or TripAdvisor:

- **3.00-3.49**: Average
- **3.50-3.69**: Good (推薦)
- **3.70-3.99**: Very good (強力推薦)
- **4.00+**: Exceptional (頂級)

A 3.5 on Tabelog is roughly equivalent to 4.3-4.5 on Google Maps.

## When to Use

- User asks about restaurant reviews in Japan
- Comparing restaurant options in a Japanese city
- Travel planning for dining in Japan
- User shares a Tabelog URL
- Need more detailed/reliable ratings than Google for Japanese restaurants

## Notes

- Coverage: Japan only (restaurants, cafes, bars, bakeries)
- Tabelog is the gold standard for restaurant reviews in Japan
- Reviews are primarily in Japanese
- Combine with `google-places` for Google reviews and `jalan-reviews` for hotel reviews
