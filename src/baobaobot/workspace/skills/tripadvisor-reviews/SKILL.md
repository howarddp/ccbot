---
name: tripadvisor-reviews
description: "Find TripAdvisor page, review count, and link for attractions, restaurants, and hotels worldwide. Use when: user asks about TripAdvisor reviews for a place, wants review count comparison, or needs a TripAdvisor link. Note: TripAdvisor blocks scraping, so only URL and review count are available — full reviews require visiting the link."
---

# TripAdvisor Reviews Skill

Find TripAdvisor page URL and review count for places worldwide. Due to TripAdvisor's anti-scraping protection, full review text cannot be extracted — the tool provides the link for users to read reviews directly.

## Usage

```bash
# Search by place name
{{BIN_DIR}}/tripadvisor-reviews "金閣寺 京都"

# English queries often work better
{{BIN_DIR}}/tripadvisor-reviews "Kinkaku-ji Kyoto"

# JSON output
{{BIN_DIR}}/tripadvisor-reviews "Eiffel Tower Paris" --json
```

## Options

- `--json` — Output raw JSON instead of formatted markdown

## Output Includes

- Place name (extracted from URL)
- TripAdvisor page URL (direct link)
- Review count (when available from search snippets)
- Rating is NOT available (TripAdvisor blocks scraping)

## When to Use

- Need a TripAdvisor link for a place
- Want to know how many TripAdvisor reviews a place has
- Comparing popularity across places (review count as proxy)
- Supplement with `google-places` for actual ratings and review text

## Limitations

- TripAdvisor blocks all scraping (403 + JS protection)
- Rating/score cannot be extracted — only review count
- Review count depends on DuckDuckGo search snippet availability
- CJK queries may need retry; English queries are more reliable

## Notes

- Coverage: worldwide
- For actual review content, combine with `google-places` (reviews), `tabelog-reviews` (Japan restaurants), or `jalan-reviews` (Japan hotels/spots)
- For web-based reviews, use `web-search` to find blog posts and forum discussions
