---
name: travel
description: "Look up places (attractions, restaurants, hotels) with reviews from multiple sources, plan travel itineraries with route maps and weather-based suggestions.\nTRIGGER when: user mentions 旅行/旅遊/行程/景點/餐廳/飯店/規劃旅行/plan a trip/travel/itinerary/sightseeing, asks about a place, wants reviews or ratings, asks to plan a trip, find restaurants/hotels, compare reviews, or needs travel recommendations.\nDO NOT TRIGGER when: user asks about local weather only, currency conversion only, or general geography questions."
---

# Travel Planner Skill

Plan multi-day travel itineraries or look up individual places with reviews from multiple sources. This is a workflow skill that orchestrates other skills.

## MANDATORY Rules

- **AskUserQuestion**: MUST use the `AskUserQuestion` tool for all clarification questions. NEVER type questions as plain text.
- **Reviews**: At least 3 web sources per place (`web-search` skill) + MUST call `tripadvisor-reviews` for every destination.
- **Route map**: MUST use `.claude/skills/travel/route_map.html` template. NEVER use Google Static Maps API PNGs as substitute.
- **Multi mode**: For multi-day trips, MUST use `mode: "multi"` → ONE combined HTML with tab switching.
- **Share link**: MUST use `share-link` skill to host HTML maps and send the URL.
- **Google Maps link**: Every place mentioned MUST include a Google Maps link (from `google-places` skill response).
- **Source URLs**: Every review/tip MUST include source URL. Unverified info marked `（未經驗證）`.

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

**Step 3 — Web search reviews (MANDATORY: 3+ sources)**

Run three separate `web-search` calls with different keywords:
1. User's language: `"PLACE_NAME 評價 推薦 心得" --region tw-tzh --limit 5`
2. English: `"PLACE_NAME CITY review recommended" --limit 5`
3. Region-specific or different angle: `"PLACE_NAME CITY blog travel experience" --limit 5`

Use `web-read "URL"` when a snippet is insufficient.

**Step 4 — Format output**

Include: name, address, Google Maps link, multi-platform ratings, opening hours, price level, website, review summary with source URLs.

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
- If user gives enough info (e.g. "3天京都行程"), proceed with defaults
- If critical info missing, use `AskUserQuestion` (max 4 questions)
- Each question: 2-4 options, recommended option first with "(推薦)", short `header` (max 12 chars)
- Tailor options to context (skip known info, match destination)

**Fallback** (ONLY if `AskUserQuestion` tool does not exist): ask in plain text with numbered options.

### Step 2 — Search flights (if air travel needed)

Use `google-flights` skill. Show top 2-3 options with airline, time, duration, price. Include price insights.

Common airport codes: TPE(桃園), TSA(松山), KIX(關西), NRT(成田), HND(羽田), ICN(仁川), HKG(香港), BKK(曼谷), SIN(新加坡)

### Step 3 — Check weather

Use `google-geocoding` to get destination coordinates, then `weather` skill for daily forecast.

**Weather-based planning:**
- ☀️ Hot (>30°C): outdoor activities early/late, indoor breaks midday
- 🌧️ Rain >50%: prioritize indoor attractions, move outdoor to other days
- 🌧️ Heavy rain/typhoon: indoor-only, warn user, suggest backup
- ❄️ Cold (<5°C): suggest onsen, indoor dining, warm attractions
- 🌡️ Extreme heat (>35°C): shorter walks, AC breaks, hydration reminders

### Step 4 — Search candidate places

Use `google-places` skill to search attractions and restaurants (10 each). Get coordinates (`places.location`) for route planning.

### Step 5 — Collect reviews for shortlisted places

Same as Mode 1 Steps 2-3. Use reviews to decide which places to include. At least 3 web sources per recommended place.

**Platform selection by region:**
- Japan: Google + TripAdvisor + Tabelog (restaurants) + Jalan (hotels/spots) + web-search x3
- Taiwan: Google + TripAdvisor + web-search x3
- Other: Google + TripAdvisor + web-search x3

### Step 6 — Optimize route order

Use `google-directions` skill with `optimizeWaypointOrder: true` for each day.
- Group nearby attractions on the same day
- Account for opening hours
- Include meal timing (breakfast, lunch, dinner)

### Step 7 — Generate interactive route map (REQUIRED)

⚠️ **MUST use `.claude/skills/travel/route_map.html` template. NEVER use Static Maps API PNGs.**

**7a. Get route data**: Use `google-directions` skill for each day's stops. MUST request per-leg polylines (include `routes.legs.polyline.encodedPolyline` in the FieldMask). **CRITICAL: Every leg MUST have a `"polyline"` field with the encoded polyline from Google Directions. Without it, the map renders ugly straight lines instead of real road paths. NEVER skip this step.**

**7b. Generate HTML**: Read the template, inject route data JSON via Python, save to `tmp/`.

Template data schema (single day):
```json
{
  "title": "Day 1 路線圖",
  "subtitle": "京都經典路線 — 4 個景點",
  "places": [
    {"lat": 34.98, "lng": 135.76, "name": "京都車站", "color": "green"},
    {"lat": 34.97, "lng": 135.77, "name": "伏見稻荷大社", "color": "blue"}
  ],
  "legs": [
    {"transport": "電車", "duration": "15min", "distance": "4.5km", "polyline": "ENCODED_POLYLINE"}
  ]
}
```

**7c. Multi-day map (REQUIRED for multi-day trips)**:

Use `mode: "multi"` to combine all days into ONE HTML with tab switching:
```json
{
  "mode": "multi",
  "title": "京都 3天2夜",
  "subtitle": "含總覽 + 每日路線切換",
  "days": [
    {
      "title": "Day 1 — 東山區",
      "tab": "Day 1",
      "places": [{"lat": 34.98, "lng": 135.76, "name": "京都車站", "color": "green"}],
      "legs": [{"transport": "電車", "duration": "15min", "distance": "4.5km", "polyline": "..."}]
    }
  ]
}
```

Template features: tab bar (總覽 + per-day), collapsible panel, Leaflet map, RWD (desktop: side panel, mobile: bottom panel).

**7d. Inject and save**:
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

Transport labels: 步行, 電車, 公車, 地鐵, 計程車, 自駕.

### Step 8 — Format itinerary output

Include these sections:
1. **天氣概覽**: per-day weather summary
2. **航班建議** (if applicable): top 2-3 flights with price
3. **交通券建議**: recommended passes
4. **每日行程**: time-based schedule with place ratings, Maps links, transport between stops, review tips
5. **互動路線圖**: share-link URL (multi mode HTML)
6. **費用預估**: breakdown (flights, transport, accommodation, meals, attractions) with currency conversion via `exchange-rate` skill
7. **Review sources**: all URLs with source type labels

**Source attribution**: Always tag source type (`[Blog]`, `[PTT]`, `[Forum]`, `[News]`, `[Travel Site]`). Mark unverified: `（未經驗證）`.

## PDF Export

When generating a PDF:
1. Use route map print mode: append `?print=1` to HTML URL (hides panel, full-width map)
2. Screenshot the print-mode page, or use Google Static Maps API as fallback for the map image
3. Embed static map in PDF + include interactive link below it
