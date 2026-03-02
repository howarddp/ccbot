---
name: travel
description: "Look up places (attractions, restaurants, hotels) with reviews from multiple sources, plan travel itineraries with route maps and weather-based suggestions.\nTRIGGER when: user mentions 旅行/旅遊/行程/景點/餐廳/飯店/規劃旅行/plan a trip/travel/itinerary/sightseeing, asks about a place, wants reviews or ratings, asks to plan a trip, find restaurants/hotels, compare reviews, or needs travel recommendations.\nDO NOT TRIGGER when: user asks about local weather only, currency conversion only, or general geography questions."
---

# Travel Planner Skill

Plan multi-day travel itineraries or look up individual places with reviews from multiple sources. This is a workflow skill that orchestrates other skills.

## MANDATORY Rules

- **AskUserQuestion**: MUST use the `AskUserQuestion` tool for all clarification questions. NEVER type questions as plain text.
- **Reviews**: At least 3 web sources per place (`web-search` skill) + MUST call `tripadvisor-reviews` for every destination.
- **YouTube**: MUST search YouTube for every place — see Step 3a below.
- **Route map**: When generating a route map, MUST use `.claude/skills/travel/route_map.html` template. NEVER use Google Static Maps API PNGs as substitute.
- **Multi mode**: For multi-day route maps, MUST use `mode: "multi"` → ONE combined HTML with tab switching.
- **Share link**: MUST use `share-link` skill to host HTML maps and send the URL.
- **Route map is optional**: For Mode 2 (itinerary planning), output the itinerary FIRST, then ask the user if they want an interactive route map. Only generate the map if they say yes.
- **Google Maps link**: Every place mentioned MUST include a Google Maps link (from `google-places` skill response).
- **Source URLs**: Every review/tip MUST include source URL. Unverified info marked `(unverified)`.

## Available Skills Reference

| Skill | Purpose |
|-------|---------|
| `google-places` | Search places, get rating/address/hours/Maps link/reviews/coordinates |
| `google-directions` | Route planning with polylines, waypoint optimization |
| `google-geocoding` | Address ↔ coordinates conversion |
| `google-flights` | Flight search with prices (SerpApi) |
| `weather` | Weather forecast (use `google-geocoding` first for coordinates) |
| `exchange-rate` | Currency conversion for cost estimates |
| `tripadvisor-reviews` | TripAdvisor review count + link |
| `tabelog-reviews` | Japan restaurant reviews (Tabelog) |
| `jalan-reviews` | Japan hotel/spot reviews (Jalan) |
| `web-search` | Web search for blog/forum reviews |
| `web-read` | Extract full article content from URL |
| `youtube-search` | YouTube video search by keyword |
| `youtube-summary` | YouTube video transcript extraction |
| `share-link` | Host HTML files and generate shareable links |

---

## Mode 1: Place Lookup

When the user asks about a **single place** (attraction, restaurant, hotel):

**Step 1 — Search the place** using `google-places` skill. Get rating, address, opening hours, Google Maps link, reviews, coordinates.

**Step 2 — Collect reviews from multiple platforms** (run in parallel):

- Google Places: already from Step 1
- TripAdvisor: always → `tripadvisor-reviews "PLACE_NAME CITY"`
- Tabelog: Japan restaurants → `tabelog-reviews "PLACE_NAME" -n 5`
- Jalan: Japan hotels/spots → `jalan-reviews "PLACE_NAME" -n 5`
- YouTube: see Step 3a below

**Step 3 — Web search reviews (MANDATORY: 3+ sources)**

Run three separate `web-search` calls with different keywords:
1. User's locale language: `"PLACE_NAME [review/recommend keywords in user's language]" --limit 5`
2. English: `"PLACE_NAME CITY review recommended" --limit 5`
3. Different angle: `"PLACE_NAME CITY blog travel experience" --limit 5`

Use `web-read "URL"` when a snippet is insufficient.

**Step 3a — YouTube reviews (MANDATORY)**

1. Search: `yt-search "PLACE_NAME travel vlog" --sort views -n 5`
   - Keyword language matches user's locale (zh-TW → `旅遊 vlog`, ja → `旅行 vlog`, en → `travel vlog`)
2. Filter results: skip videos < 2 min (shorts/ads) or > 30 min (too long)
3. Take the top 2 qualifying videos — run `yt-transcript "URL"` for each
4. Extract from transcript: experience highlights, practical tips, costs, warnings
5. If no subtitles available — skip summary, list video link only

**Step 4 — Format output**

Use the **Review Card Format** (see appendix at bottom) for each place.

---

## Mode 2: Itinerary Planning

When the user asks to **plan a trip**:

### Step 1 — Clarify requirements (MUST use AskUserQuestion)

**Required info** — ask if missing:
- Dates or number of days
- Destination (specific city/area)

**Optional** — use defaults if not specified:
- Interests (default: balanced sightseeing + food)
- Budget (default: moderate)
- Departure city (for flights)
- Number of travelers (default: 1)

**Rules:**
- If user gives enough info (e.g. "3 days in Kyoto"), proceed with defaults
- If critical info missing, use `AskUserQuestion` (max 4 questions)
- Each question: 2-4 options, recommended option first with "(Recommended)", short `header` (max 12 chars)
- Tailor options to context (skip known info, match destination)

**Fallback** (ONLY if `AskUserQuestion` tool does not exist): ask in plain text with numbered options.

### Step 2 — Search flights (if air travel needed)

Use `google-flights` skill. Show top 2-3 options with airline, time, duration, price. Include price insights.

Common airport codes: TPE(Taoyuan), TSA(Songshan), KIX(Kansai), NRT(Narita), HND(Haneda), ICN(Incheon), HKG(Hong Kong), BKK(Bangkok), SIN(Singapore)

### Step 3 — Check weather

Use `google-geocoding` to get destination coordinates, then `weather` skill for daily forecast.

**Weather-based planning:**
- Hot (>30°C): outdoor activities early/late, indoor breaks midday
- Rain >50%: prioritize indoor attractions, move outdoor to other days
- Heavy rain/typhoon: indoor-only, warn user, suggest backup
- Cold (<5°C): suggest onsen, indoor dining, warm attractions
- Extreme heat (>35°C): shorter walks, AC breaks, hydration reminders

### Step 4 — Search candidate places

Use `google-places` skill to search attractions and restaurants (10 each). Get coordinates (`places.location`) for route planning.

### Step 5 — Collect reviews for shortlisted places

Same as Mode 1 Steps 2-3 (including Step 3a YouTube). Use reviews to decide which places to include. At least 3 web sources per recommended place.

**Platform selection by region:**
- Japan: Google + TripAdvisor + YouTube + Tabelog (restaurants) + Jalan (hotels/spots) + web-search x3
- Taiwan: Google + TripAdvisor + YouTube + web-search x3
- Other: Google + TripAdvisor + YouTube + web-search x3

### Step 6 — Arrange places by day

Group places geographically and arrange the visit order:
- Group nearby attractions on the same day (use coordinates from Step 4)
- Account for opening hours
- Include meal timing (breakfast, lunch, dinner)
- For 5+ stops per day: use `google-distance-matrix` to compare travel times and optimize visit order
- For fewer stops: estimate transport time between stops (approximate is OK — no API call needed)

### Step 7 — Format and output itinerary

Output the itinerary to the user FIRST, before any route map generation.

Include these sections:
1. **Weather overview**: per-day weather summary
2. **Flight options** (if applicable): top 2-3 flights with price
3. **Transport pass recommendations**: recommended passes
4. **Daily itinerary**: time-based schedule with per-place review cards (see format below)
5. **Cost estimate**: breakdown (flights, transport, accommodation, meals, attractions) with currency conversion via `exchange-rate` skill

Use the **Review Card Format** (see appendix at bottom) for each place in the daily schedule. Add time prefix and transport between stops:

```
#### 09:00 Place Name ⭐ Google 4.5
[... review card ...]
🚶 Walk 10min → Next stop
```

### Step 8 — Ask about interactive route map

After outputting the itinerary, use `AskUserQuestion` to ask:

```
"Would you like me to generate an interactive route map?"
```

Options:
- "Yes, generate route map (Recommended)" — proceed to Step 9
- "No thanks" — done

### Step 9 — Generate interactive route map (only if user requested)

**MUST use `.claude/skills/travel/route_map.html` template. NEVER use Static Maps API PNGs.**

**9a. Optimize route**: Use `google-directions` skill with `optimizeWaypointOrder: true` for each day. MUST request per-leg polylines (include `routes.legs.polyline.encodedPolyline` in the FieldMask). **CRITICAL: Every leg MUST have a `"polyline"` field with the encoded polyline from Google Directions. Without it, the map renders ugly straight lines instead of real road paths. NEVER skip this step.**

**9b. Generate HTML**: Read the template, inject route data JSON via Python, save to `tmp/`.

Template data schema (single day):
```json
{
  "title": "Day 1 Route",
  "subtitle": "Kyoto Classic Route — 4 stops",
  "places": [
    {"lat": 34.98, "lng": 135.76, "name": "Kyoto Station", "color": "green"},
    {"lat": 34.97, "lng": 135.77, "name": "Fushimi Inari Shrine", "color": "blue"}
  ],
  "legs": [
    {"transport": "Train", "duration": "15min", "distance": "4.5km", "polyline": "ENCODED_POLYLINE"}
  ]
}
```

**9c. Multi-day map (REQUIRED for multi-day trips)**:

Use `mode: "multi"` to combine all days into ONE HTML with tab switching:
```json
{
  "mode": "multi",
  "title": "Kyoto 3D2N",
  "subtitle": "Overview + daily route switching",
  "days": [
    {
      "title": "Day 1 — Higashiyama",
      "tab": "Day 1",
      "places": [{"lat": 34.98, "lng": 135.76, "name": "Kyoto Station", "color": "green"}],
      "legs": [{"transport": "Train", "duration": "15min", "distance": "4.5km", "polyline": "..."}]
    }
  ]
}
```

Template features: tab bar (Overview + per-day), collapsible panel, Leaflet map, RWD (desktop: side panel, mobile: bottom panel).

**9d. Inject and save**:
```python
import json, os
TEMPLATE = ".claude/skills/travel/route_map.html"
OUTPUT = "tmp/trip_route.html"
with open(TEMPLATE) as f:
    html = f.read()
html = html.replace("__ROUTE_DATA_JSON__", json.dumps(data, ensure_ascii=False))
html = html.replace("__TITLE__", data["title"])
os.makedirs("tmp", exist_ok=True)
with open(OUTPUT, "w") as f:
    f.write(html)
```

Then use `share-link` skill to host the HTML and send the URL.

Transport labels: Walk, Train, Bus, Subway, Taxi, Drive.

---

## Appendix: Review Card Format

Both Mode 1 and Mode 2 use this format for each place. Adapt detail level to context (Mode 1 standalone = full detail, Mode 2 itinerary = concise).

```
### Place Name
📍 Address | [Google Maps](link)
⏰ Opening hours | 💰 Price level

**Reviews**
- Google: ⭐ 4.5/5 (1,234 reviews) — "representative review snippet"
- TripAdvisor: ⭐ 4.0/5 (567 reviews) — "representative review snippet"
- Tabelog: ⭐ 3.8/5 (89 reviews) — "representative snippet" (Japan restaurants only)
- Jalan: ⭐ 4.2/5 (234 reviews) — "representative snippet" (Japan hotels/spots only)
- YouTube: 🎬 N related videos — key insights from transcript
  - [Video Title](url) — summary of highlights
- Blog/Web: review highlights from web search
  - [Article Title](url) — summary [Blog]
  - [Thread Title](url) — summary [PTT/Forum]

💡 Tips: practical advice consolidated from all sources
```

**Rules:**
- MUST show every platform that was queried, even if no results (e.g. "TripAdvisor: not found")
- Rating format: ⭐ X.X/5 (N reviews) — keep it concise
- Each platform's snippet should be the most representative or useful comment
- YouTube: summarize transcript insights if available; otherwise list video link + duration
- Blog/Web: include source type tag `[Blog]`, `[PTT]`, `[Forum]`, `[News]`, `[Travel Site]`
- All URLs must be clickable links
- Mark unverified info: `(unverified)`

---

## PDF Export

When generating a PDF:
1. Use route map print mode: append `?print=1` to HTML URL (hides panel, full-width map)
2. Screenshot the print-mode page, or use Google Static Maps API as fallback for the map image
3. Embed static map in PDF + include interactive link below it
