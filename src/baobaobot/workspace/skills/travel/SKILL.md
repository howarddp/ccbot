---
name: travel
description: "Look up places (attractions, restaurants, hotels) with reviews from multiple sources, plan travel itineraries with route maps and weather-based suggestions. Use when: user asks about a place, wants reviews or ratings, asks to plan a trip, find restaurants/hotels, compare reviews, or needs travel recommendations."
---

# Travel Planner Skill

Plan multi-day travel itineraries or look up individual places with reviews from multiple sources. This is a workflow skill that orchestrates other skills.

## Two Modes of Operation

### Mode 1: Place Lookup

When the user asks about a **single place** (attraction, restaurant, hotel):

**Step 1 — Search the place**

```bash
source "{{BIN_DIR}}/_load_env"

# Search via Google Places API (get rating, address, opening hours, Google Maps link)
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.currentOpeningHours.openNow,places.regularOpeningHours,places.websiteUri,places.googleMapsUri,places.editorialSummary,places.priceLevel,places.reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "PLACE_NAME",
    "languageCode": "zh-TW",
    "maxResultCount": 1
  }'
```

**Step 2 — Collect reviews from multiple platforms**

Run these in parallel based on region:

| Platform | When to use | Command |
|----------|-------------|---------|
| Google Places | Always | Already included in Step 1 (`places.reviews` field) |
| TripAdvisor | Always | `{{BIN_DIR}}/tripadvisor-reviews "PLACE_NAME CITY"` |
| Tabelog | Japan restaurants | `{{BIN_DIR}}/tabelog-reviews "PLACE_NAME" -n 5` |
| Jalan | Japan hotels/spots | `{{BIN_DIR}}/jalan-reviews "PLACE_NAME" -n 5` |

**Step 3 — Web search for additional reviews (MANDATORY: at least 3 sources)**

Run **three separate web searches** with different keywords to maximize source diversity:

```bash
# Search 1: User's language (zh-TW)
{{BIN_DIR}}/web-search "PLACE_NAME 評價 推薦 心得" --region tw-tzh --limit 5

# Search 2: English reviews
{{BIN_DIR}}/web-search "PLACE_NAME CITY review recommended" --limit 5

# Search 3: Japanese reviews (for Japan destinations)
{{BIN_DIR}}/web-search "PLACE_NAME 口コミ おすすめ" --region jp-jp --limit 5
# OR for non-Japan destinations, search a different angle:
{{BIN_DIR}}/web-search "PLACE_NAME CITY blog travel experience" --limit 5
```

For each web search result used:
- **MUST** include the source URL
- **MUST** note if the information cannot be verified: add `（未經驗證）`
- Prefer established sources (travel blogs, forums, news) over anonymous posts
- Use `{{BIN_DIR}}/web-read "URL"` to get full article content when a snippet is insufficient

**Step 4 — Format output**

```
📍 PLACE_NAME (English Name)
📍 Address
⭐ Google: X.X/5 (N reviews)
🗺️ Google Maps link

📊 Multi-platform Ratings:
- Google: X.X/5 (N reviews)
- TripAdvisor: N reviews → link
- Tabelog: X.XX/5 (N reviews) → link     ← Japan restaurants only
- Jalan: X.X (N reviews) → link          ← Japan hotels/spots only

🕐 Opening Hours: ...
💰 Price Level: ...
🌐 Website: ...

📝 Review Summary (from N sources):
1. [Source Name] summary — URL
2. [Source Name] summary — URL
3. [Source Name] summary — URL （未經驗證）
```

---

### Mode 2: Itinerary Planning

When the user asks to **plan a trip** (e.g., "plan 3 days in Kyoto"):

**Step 1 — Clarify requirements (use AskUserQuestion when possible)**

Check what the user has provided. The following are **required** — if missing, **ask the user before proceeding**:
- Dates or number of days
- Destination (specific city/area)

The following are **optional** — use reasonable defaults if not specified:
- Interests/preferences (default: balanced sightseeing + food)
- Budget level (default: moderate)
- Departure city (for flight search)
- Number of travelers (default: 1)

**When to ask vs. proceed:**
- If the user gives enough info (e.g., "3天京都行程"), proceed directly with sensible defaults
- If critical info is missing (e.g., "幫我規劃日本旅行" — which city? how many days?), ask first
- **Maximum 4 questions** — only ask what's truly missing, skip what can be inferred

**How to ask — use `AskUserQuestion` tool (preferred):**

If the `AskUserQuestion` tool is available, use it to present questions with suggested options. This creates interactive buttons in the chat, saving users from typing. Each question MUST have 2-4 suggested options.

Rules:
- Maximum 4 questions per `AskUserQuestion` call (tool limit)
- Every option should have a short `label` and helpful `description`
- Put the most common/recommended option first with "(推薦)" in the label
- The user can always select "Other" to type a custom answer
- Add a `header` (max 12 chars) as breadcrumb context for each question

Example `AskUserQuestion` usage for travel planning:
```
questions: [
  {
    "question": "想去日本哪個城市/地區？",
    "header": "目的地",
    "options": [
      {"label": "京都 (推薦)", "description": "寺廟、古都、抹茶"},
      {"label": "東京", "description": "購物、美食、都市"},
      {"label": "大阪", "description": "美食、環球影城、活力"},
      {"label": "北海道", "description": "自然、海鮮、薰衣草"}
    ],
    "multiSelect": false
  },
  {
    "question": "預計去幾天？",
    "header": "天數",
    "options": [
      {"label": "3天2夜 (推薦)", "description": "週末+1天，最常見短旅"},
      {"label": "5天4夜", "description": "可以深度玩一個城市"},
      {"label": "7天6夜", "description": "可跨城市或深度遊"}
    ],
    "multiSelect": false
  },
  {
    "question": "旅行風格偏好？",
    "header": "風格",
    "options": [
      {"label": "寺廟古蹟+美食 (推薦)", "description": "文化巡禮搭配在地美食"},
      {"label": "購物+都市體驗", "description": "逛街、藥妝、潮流"},
      {"label": "自然風景+溫泉", "description": "放鬆、郊外、療癒"},
      {"label": "親子/家庭", "description": "適合帶小孩的景點"}
    ],
    "multiSelect": true
  },
  {
    "question": "每人預算大概多少？",
    "header": "預算",
    "options": [
      {"label": "2~3萬台幣 (推薦)", "description": "中等預算，住商旅"},
      {"label": "1~2萬台幣", "description": "省錢旅行，住青旅/膠囊"},
      {"label": "3~5萬台幣", "description": "舒適旅行，住飯店"},
      {"label": "5萬以上", "description": "豪華旅行，高級飯店"}
    ],
    "multiSelect": false
  }
]
```

Adapt the questions and options based on context:
- If destination is known but days are missing → skip destination question
- If it's a domestic trip → skip flight-related questions
- Tailor options to the destination (e.g., Kyoto → temples; Tokyo → shopping)

**Fallback**: If `AskUserQuestion` is NOT available, ask in plain text with numbered suggested answers:
```
想幫你規劃行程，先確認幾個問題：

1️⃣ 想去哪個城市？
   → 京都 / 東京 / 大阪 / 其他

2️⃣ 預計幾天？
   → 3天2夜 / 5天4夜 / 7天6夜

3️⃣ 旅行風格？
   → 寺廟美食 / 購物都市 / 自然溫泉

4️⃣ 每人預算？
   → 2~3萬 / 3~5萬 / 5萬+
```

**Step 1.5 — Search flights** (if trip involves air travel)

When the destination requires flying (international or domestic long-distance), search flights using SerpApi:

```bash
source "{{BIN_DIR}}/_load_env"

# Round-trip flight search (adjust airport codes, dates, passengers)
curl -s "https://serpapi.com/search.json?engine=google_flights&departure_id=TPE&arrival_id=KIX&outbound_date=2026-03-15&return_date=2026-03-20&type=1&currency=TWD&hl=zh-TW&gl=tw&adults=2&api_key=$SERPAPI_API_KEY" \
  | jq '{
    best_flights: [.best_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      flight_numbers: [.flights[].flight_number] | join(", "),
      departure: .flights[0].departure_airport.time,
      arrival: .flights[-1].arrival_airport.time,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1),
      price: .price
    }],
    other_flights: [.other_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      price: .price,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1)
    }] | .[0:3],
    price_insights: .price_insights
  }'
```

**Flight output format:**
```
✈️ 航班建議（DEPARTURE → DESTINATION）

推薦航班：
1. AIRLINE FLIGHT_NO | HH:MM→HH:MM | 直飛 Xhr Ymin | $PRICE/人
2. AIRLINE FLIGHT_NO | HH:MM→HH:MM | 轉機1次 | $PRICE/人

💡 價格分析：目前票價屬於「low/typical/high」水準
   一般價格範圍：$MIN~$MAX
```

Common airport codes: TPE(桃園), TSA(松山), KIX(關西), NRT(成田), HND(羽田), ICN(仁川), HKG(香港), BKK(曼谷), SIN(新加坡)

**Step 2 — Check weather**

```bash
source "{{BIN_DIR}}/_load_env"

# Geocode the destination
COORDS=$(curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("DESTINATION"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0].geometry.location | "\(.lat) \(.lng)"')
LAT=$(echo "$COORDS" | cut -d' ' -f1)
LNG=$(echo "$COORDS" | cut -d' ' -f2)

# Get daily forecast
curl -s "https://weather.googleapis.com/v1/forecast/days:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=$LAT&location.longitude=$LNG&days=N_DAYS" \
  | jq -r '.forecastDays[] | "📅 \(.displayDate.year)-\(.displayDate.month)-\(.displayDate.day): \(.daytimeForecast.weatherCondition.description.text // .daytimeForecast.weatherCondition.type) | ⬆️\(.maxTemperature.degrees)°C ⬇️\(.minTemperature.degrees)°C | 🌧️\(.daytimeForecast.precipitation.probability.percent // 0)%"'
```

**Step 3 — Weather-based planning rules**

Apply these rules when selecting and scheduling activities:

| Weather | Strategy |
|---------|----------|
| ☀️ Clear/Sunny, Hot (>30°C) | Schedule outdoor activities early morning (before 10am) or late afternoon (after 4pm). Add indoor breaks midday (museums, cafes, shopping). Suggest shaded spots. |
| 🌧️ Rain likely (>50%) | Prioritize indoor attractions (museums, temples with covered areas, shopping streets, food markets). Move outdoor activities to other days if possible. |
| 🌧️ Heavy rain / Typhoon alert | Strongly recommend indoor-only plan. Warn the user. Suggest backup activities. |
| ❄️ Cold (<5°C) | Suggest onsen/hot springs, indoor dining, warm indoor attractions. Note warm clothing needed. |
| ☁️ Cloudy, mild | Ideal for outdoor sightseeing. No special adjustments needed. |
| 🌡️ Extreme heat (>35°C) | Warn about heat. Suggest shorter outdoor walks, more AC breaks, hydration reminders. |

Include a weather summary at the top of each day's plan.

**Step 4 — Search candidate places**

```bash
source "{{BIN_DIR}}/_load_env"

# Search attractions
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.googleMapsUri,places.location,places.editorialSummary" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "DESTINATION popular attractions",
    "languageCode": "zh-TW",
    "maxResultCount": 10
  }'

# Search restaurants
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.googleMapsUri,places.location,places.priceLevel" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "DESTINATION recommended restaurants",
    "languageCode": "zh-TW",
    "maxResultCount": 10
  }'
```

**Step 5 — Collect reviews for shortlisted places**

For each candidate place, run the review collection (same as Mode 1, Step 2-3). Use reviews to decide which places to include:
- Prefer places with high ratings across multiple platforms
- Note any common complaints or tips from reviews
- Include at least 3 web sources with URLs for each recommended place

**Step 6 — Optimize route order**

Use Google Directions API with `optimizeWaypointOrder: true` to find the most efficient route:

```bash
source "{{BIN_DIR}}/_load_env"

# Optimize waypoint order for each day
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.optimizedIntermediateWaypointIndex,routes.legs.localizedValues,routes.localizedValues,routes.polyline.encodedPolyline" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "START_POINT"},
    "destination": {"address": "END_POINT"},
    "intermediates": [
      {"address": "PLACE_1"},
      {"address": "PLACE_2"},
      {"address": "PLACE_3"}
    ],
    "travelMode": "DRIVE",
    "optimizeWaypointOrder": true,
    "languageCode": "zh-TW"
  }'
```

**Step 7 — Generate annotated route maps**

Generate **one map per day** + **one overall trip map**. All maps MUST have place names labeled directly on the image (not just A/B/C markers).

**7a. Get route data with polyline and leg details**

```bash
source "{{BIN_DIR}}/_load_env"

# Get route for each day
ROUTE_JSON=$(curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.polyline.encodedPolyline,routes.legs.localizedValues,routes.legs.startLocation,routes.legs.endLocation" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "DAY_START"},
    "destination": {"address": "DAY_END"},
    "intermediates": [{"address": "STOP_1"}, {"address": "STOP_2"}],
    "travelMode": "TRANSIT",
    "languageCode": "zh-TW"
  }')
```

**7b. Generate map and annotate with Python**

Use this Python script to: (1) download the static map, (2) overlay place names + transport info directly on the image.

```python
import math, json, urllib.parse, urllib.request, os
from PIL import Image, ImageDraw, ImageFont

# ---- CONFIG (fill in for each day) ----
MAP_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
OUTPUT = "tmp/day1_route.png"
DAY_LABEL = "Day 1"

# Places: list of (lat, lng, name, marker_color)
# marker_color: "green" for start, "red" for end, "blue" for intermediate
places = [
    (35.0116, 135.7681, "京都車站", "green"),
    (34.9803, 135.7478, "東寺", "blue"),
    (34.9671, 135.7727, "伏見稻荷大社", "blue"),
    (34.9879, 135.7710, "三十三間堂", "red"),
]

# Leg info between consecutive places (from Directions API)
legs = [
    "🚃 10min / 2.5km",
    "🚃 15min / 4km",
    "🚌 12min / 3km",
]

# Encoded polyline from Directions API
polyline_encoded = "PASTE_ENCODED_POLYLINE_HERE"

# ---- MAP GENERATION ----
IMG_W, IMG_H, SCALE = 600, 400, 2
REAL_W, REAL_H = IMG_W * SCALE, IMG_H * SCALE

# Build static map URL with route line + letter markers
markers_param = ""
for i, (lat, lng, name, color) in enumerate(places):
    label = chr(65 + i)  # A, B, C, ...
    markers_param += f"&markers=color:{color}%7Clabel:{label}%7C{lat},{lng}"

encoded_poly = urllib.parse.quote(polyline_encoded)
url = (f"https://maps.googleapis.com/maps/api/staticmap?"
       f"size={IMG_W}x{IMG_H}&scale={SCALE}"
       f"&path=color:0x4285F4FF%7Cweight:4%7Cenc:{encoded_poly}"
       f"{markers_param}&language=zh-TW&key={MAP_KEY}")

urllib.request.urlretrieve(url, OUTPUT)

# ---- ANNOTATE WITH PLACE NAMES ----
# Calculate map bounds from all place coordinates
lats = [p[0] for p in places]
lngs = [p[1] for p in places]
# Add padding (same as Google's auto-fit)
lat_pad = (max(lats) - min(lats)) * 0.15 + 0.002
lng_pad = (max(lngs) - min(lngs)) * 0.15 + 0.002
min_lat, max_lat = min(lats) - lat_pad, max(lats) + lat_pad
min_lng, max_lng = min(lngs) - lng_pad, max(lngs) + lng_pad
center_lat = (min_lat + max_lat) / 2
center_lng = (min_lng + max_lng) / 2

# Find zoom level that fits all points
def lat_to_y(lat, zoom):
    siny = max(min(math.sin(lat * math.pi / 180), 0.9999), -0.9999)
    return 256 * (2 ** zoom) * (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi))

def lng_to_x(lng, zoom):
    return 256 * (2 ** zoom) * (lng + 180) / 360

# Auto-detect zoom (try from high to low)
for zoom in range(18, 0, -1):
    x_min = lng_to_x(min_lng, zoom)
    x_max = lng_to_x(max_lng, zoom)
    y_min = lat_to_y(max_lat, zoom)  # Note: y is inverted
    y_max = lat_to_y(min_lat, zoom)
    if (x_max - x_min) < REAL_W * 0.9 and (y_max - y_min) < REAL_H * 0.9:
        break

cx = lng_to_x(center_lng, zoom)
cy = lat_to_y(center_lat, zoom)

def to_pixel(lat, lng):
    x = lng_to_x(lng, zoom) - cx + REAL_W / 2
    y = lat_to_y(lat, zoom) - cy + REAL_H / 2
    return int(x), int(y)

# Load image and font
img = Image.open(OUTPUT)
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 22)
    font_small = ImageFont.truetype("/System/Library/Fonts/STHeiti Light.ttc", 16)
except:
    font = ImageFont.load_default()
    font_small = font

# Draw place name labels
for i, (lat, lng, name, color) in enumerate(places):
    px, py = to_pixel(lat, lng)
    label_text = f" {chr(65+i)} {name} "

    # Calculate text box
    bbox = draw.textbbox((0, 0), label_text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Position label: try right of marker, fall back to left if near edge
    lx = px + 20
    if lx + tw > REAL_W - 10:
        lx = px - tw - 20
    ly = py - th // 2 - 15

    # Draw background box with rounded feel
    pad = 3
    draw.rectangle([lx - pad, ly - pad, lx + tw + pad, ly + th + pad],
                    fill=(255, 255, 255, 220), outline=(80, 80, 80))
    draw.text((lx, ly), label_text, fill=(0, 0, 0), font=font)

# Draw transport info between consecutive places
for i, leg_text in enumerate(legs):
    if i + 1 < len(places):
        lat1, lng1 = places[i][0], places[i][1]
        lat2, lng2 = places[i+1][0], places[i+1][1]
        px1, py1 = to_pixel(lat1, lng1)
        px2, py2 = to_pixel(lat2, lng2)
        mx, my = (px1 + px2) // 2, (py1 + py2) // 2

        bbox = draw.textbbox((0, 0), leg_text, font=font_small)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 2
        draw.rectangle([mx - tw//2 - pad, my - th//2 - pad,
                         mx + tw//2 + pad, my + th//2 + pad],
                        fill=(230, 240, 255, 200), outline=(100, 130, 200))
        draw.text((mx - tw//2, my - th//2), leg_text, fill=(30, 60, 140), font=font_small)

# Draw day label in top-left corner
day_text = f" 🗺️ {DAY_LABEL} 路線圖 "
bbox = draw.textbbox((0, 0), day_text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.rectangle([8, 8, 16 + tw, 16 + th], fill=(255, 255, 255, 230), outline=(60, 60, 60))
draw.text((12, 12), day_text, fill=(0, 0, 0), font=font)

img.save(OUTPUT)
print(f"Annotated map saved: {OUTPUT}")
```

**7c. Overall trip map** (all days combined)

Same approach but use different marker colors per day and label with day numbers:
- 🔴 Red markers: Day 1 spots
- 🔵 Blue markers: Day 2 spots
- 🟢 Green markers: Day 3 spots

```python
# For the overall map, use same annotation script but:
# - No route polyline (markers only)
# - Label format: "D1 景點名" / "D2 景點名" etc.
# - Color-code by day
```

Send maps with `[SEND_FILE:tmp/day1_route.png]`.

**IMPORTANT**:
- Always use encoded polyline from Directions API for route lines. NEVER draw straight lines.
- Place names and transport info MUST be rendered directly on the map image.
- If Pillow is not available, fall back to sending the base map + text legend (see below).

**Fallback text legend** (only if Pillow annotation fails):

For daily route maps:
```
🗺️ Day 1 路線圖
A → 京都車站（起點）
  ↓ 🚶 15min / 1.2km
B → 東寺
  ↓ 🚃 10min / 4.5km
C → 伏見稻荷大社
```

For the overall trip map:
```
🗺️ 三日總覽
🔴 Day 1 — 京都車站周邊（東寺、伏見稻荷、三十三間堂）
🔵 Day 2 — 嵐山（天龍寺、竹林、渡月橋）
🟢 Day 3 — 東山（清水寺、祇園、建仁寺）
```

**Step 8 — Format itinerary output**

```
🗺️ DESTINATION N日行程

📊 天氣概覽:
Day 1 (MM/DD): ☀️ 22°C, 降雨 10%
Day 2 (MM/DD): 🌧️ 18°C, 降雨 70% → 建議安排室內行程
Day 3 (MM/DD): ☁️ 20°C, 降雨 20%

✈️ 航班（if applicable — show top 2-3 flights with price）

🎫 交通券建議：推薦 XXX 一日券 ¥X,XXX（含地鐵/公車）

---

📅 Day 1 — AREA_NAME (☀️ 22°C, 降雨 10%)

09:00 🏛 PLACE_1
  ⭐ Google X.X | TripAdvisor NK reviews | Tabelog X.XX
  🗺️ Google Maps link
  💡 Tips from reviews
  ⏱️ Suggested duration: 1.5hr

  ↓ 🚶 15min (0.8km)

10:30 ⛩ PLACE_2
  ⭐ Google X.X
  🗺️ Google Maps link

  ↓ 🚇 20min (Metro Line X)

12:00 🍜 Lunch: RESTAURANT
  ⭐ Google X.X | Tabelog X.XX
  🗺️ Google Maps link
  💰 ¥1,500~2,000
  📝 Reviews: "..." — source URL

...

📝 Day 1 Review Sources:
1. [Blog Name] description — URL
2. [Forum] description — URL
3. [Travel Site] description — URL （未經驗證）

🗺️ Day 1 路線圖:
[SEND_FILE:tmp/day1_route.png]
A → PLACE_1（起點）
  ↓ 🚶 15min / 0.8km
B → PLACE_2
  ↓ 🚇 20min / 5km
C → RESTAURANT（午餐）
  ↓ 🚌 10min / 2km
D → PLACE_3（終點）

---

📅 Day 2 — AREA_NAME (🌧️ 18°C, 降雨 70%)
⚠️ Rain expected — indoor activities prioritized

...

---

🗺️ 三日總覽:
[SEND_FILE:tmp/trip_overview.png]
🔴 Day 1 — AREA（PLACE_1, PLACE_2, PLACE_3）
🔵 Day 2 — AREA（PLACE_4, PLACE_5, PLACE_6）
🟢 Day 3 — AREA（PLACE_7, PLACE_8, PLACE_9）

---

✈️ 航班建議（if applicable）:
推薦航班：
1. 長榮 BR XXX | 08:00→12:00 | 直飛 3hr | $X,XXX/人
2. 虎航 IT XXX | 14:00→18:00 | 直飛 3hr | $X,XXX/人
💡 價格屬「typical」水準，一般範圍 $X,XXX~$X,XXX

---

💰 費用預估（per person）:

| 項目 | 金額 |
|------|------|
| ✈️ 機票（來回） | $X,XXX~$X,XXX |
| 🚃 當地交通 | ¥X,XXX (~$X,XXX) |
| 🏨 住宿 N晚 | ¥X,XXX~¥X,XXX (~$X,XXX~$X,XXX) |
| 🍜 餐飲 N天 | ¥X,XXX~¥X,XXX (~$X,XXX~$X,XXX) |
| 🎫 門票/景點 | ¥X,XXX (~$X,XXX) |
| 🛍️ 購物/其他 | 依個人 |
| **合計（不含購物）** | **$XX,XXX~$XX,XXX** |

💡 匯率參考：1 JPY ≈ X.XX TWD（use exchange-rate skill）
💡 省錢提示：...

---

📝 All Review Sources:
1. source — URL
2. source — URL
...
```

## Review Source Rules

**MANDATORY**: For every place recommended, gather reviews from **at least 3 different web sources** (in addition to API-based platforms like Google, TripAdvisor, Tabelog, Jalan).

### How to search for reviews

Run three web searches with different keywords:

```bash
# 1. Chinese reviews (blogs, forums, PTT)
{{BIN_DIR}}/web-search "PLACE_NAME 評價 推薦 心得" --region tw-tzh --limit 5

# 2. English reviews
{{BIN_DIR}}/web-search "PLACE_NAME CITY review blog" --limit 5

# 3. Region-specific search
# Japan: {{BIN_DIR}}/web-search "PLACE_NAME 口コミ おすすめ" --region jp-jp --limit 5
# Other: {{BIN_DIR}}/web-search "PLACE_NAME CITY travel tips" --limit 5
```

### Source attribution rules

- **Always** include the source URL for every piece of review information
- **Always** name the source type: `[Blog]`, `[PTT]`, `[Forum]`, `[News]`, `[Travel Site]`, `[Social Media]`
- If information cannot be cross-verified with another source, mark it: `（未經驗證）`
- Prefer established sources (known travel blogs, major forums, news sites)
- When quoting reviews, keep them concise (1-2 sentences)

### Platform selection by region

| Region | Platforms to check |
|--------|--------------------|
| Japan | Google + TripAdvisor + Tabelog (restaurants) + Jalan (hotels/spots) + web-search x3 |
| Taiwan | Google + TripAdvisor + web-search x3 (PTT, travel blogs) |
| Other Asia | Google + TripAdvisor + web-search x3 |
| Europe/Americas | Google + TripAdvisor + web-search x3 |

## Google Maps Link Requirement

**Every place mentioned must include a Google Maps link.** Get it from:
- `places.googleMapsUri` field in Google Places API response
- Or construct: `https://www.google.com/maps/place/?q=place_id:PLACE_ID`

## Important Notes

- Use `optimizeWaypointOrder: true` in Directions API to find the best route order
- Always use encoded polyline for route maps (never straight lines)
- Check weather FIRST and adjust the plan accordingly
- Include transit details when using public transport (`travelMode: "TRANSIT"`)
- For multi-day trips, group nearby attractions on the same day to minimize travel time
- Account for opening hours when scheduling (check `regularOpeningHours`)
- Include meal recommendations (breakfast, lunch, dinner) with timing
- Add estimated costs where available (`priceLevel` from Google, payment info from Tabelog)
- **Flights**: For international/long-distance trips, always search flights using SerpApi (google_flights engine). Show top 2-3 options with airline, time, duration, price. Include price_insights if available.
- **Cost estimate**: ALWAYS include a cost breakdown at the end of itineraries. Categories: flights, local transport, accommodation, meals, attractions. Use `exchange-rate` skill for currency conversion to user's local currency (default TWD). Add money-saving tips.
- **Map legends**: Every map MUST be followed by a text legend mapping markers to place names with distances/durations between stops.
