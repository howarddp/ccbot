---
name: jalan-reviews
description: "Extract and display reviews (口コミ) from jalan.net hotel and tourist spot pages. Use when: user shares a jalan.net URL, mentions a hotel/spot name to look up reviews, or asks about hotel/spot reviews on jalan.net. Supports both URL and name search."
---

# Jalan Reviews Skill

Extract and display reviews (クチコミ/口コミ) from jalan.net pages, including hotels (宿/ホテル) and tourist spots (観光スポット).

## Usage

```bash
# Search by hotel/spot name (auto-finds on jalan.net)
{{BIN_DIR}}/jalan-reviews "ロイヤルパークホテル 大阪御堂筋"

# Show reviews by URL (default 10 reviews)
{{BIN_DIR}}/jalan-reviews "https://www.jalan.net/yad319043/kuchikomi/"

# Show reviews for a tourist spot
{{BIN_DIR}}/jalan-reviews "嵐山"

# Limit number of reviews
{{BIN_DIR}}/jalan-reviews "HOTEL nanvan焼津" -n 5

# View page 2 of reviews
{{BIN_DIR}}/jalan-reviews "https://www.jalan.net/yad319043/" --page 2

# Hide hotel replies
{{BIN_DIR}}/jalan-reviews "ロイヤルパークホテル 大阪" --no-reply

# JSON output for programmatic use
{{BIN_DIR}}/jalan-reviews "https://www.jalan.net/yad319043/" --json
```

## Input Formats

Accepts both URLs and hotel/spot names:
- **Name search**: `"ロイヤルパークホテル 大阪"` → auto-searches DuckDuckGo for the jalan.net page
- **URL**: `https://www.jalan.net/yad319043/` → directly fetches the review page
- URLs are auto-normalized (adds `/kuchikomi/` if missing)

## Options

- `-n, --count N` — Maximum reviews to show (default: 10)
- `--page N` — Page number for pagination (default: 1)
- `--no-reply` — Hide hotel/facility replies
- `--json` — Output raw JSON instead of formatted markdown

## Output Includes

**For hotels:**
- Hotel name, overall rating, total review count
- Category ratings (部屋/風呂/料理/接客・サービス/清潔感)
- Individual reviews with: username, demographics, trip type, stay period, plan, room type, meal, price range, scores, review title/body, hotel reply

**For tourist spots:**
- Spot name, overall rating, total review count
- Individual reviews with: visit date, post date, review body

## When to Use

- User shares a jalan.net URL
- User asks to check reviews for a Japanese hotel or tourist spot
- Comparing reviews across multiple properties
- Research for travel planning in Japan
