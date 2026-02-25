---
name: google-air-quality
description: "Get current air quality index and pollutant data for any location via Google Air Quality API using curl. Use when: user asks about air quality, pollution levels, AQI, or whether it's safe to exercise outdoors. Requires GOOGLE_MAPS_API_KEY."
---

# Google Air Quality Skill

Get current air quality conditions, AQI index, and pollutant details for any location via Google Air Quality API with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Air Quality API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/airquality.googleapis.com).

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Current Air Quality (basic)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s -X POST "https://airquality.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "location": {"latitude": 25.0339, "longitude": 121.5645},
    "languageCode": "zh-TW"
  }' | jq -r '.indexes[]? | "🌬️ \(.displayName): \(.aqiDisplay) — \(.category)\n💨 主要汙染物: \(.dominantPollutant)"'
```

## With Detailed Pollutant Data

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s -X POST "https://airquality.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "location": {"latitude": 25.0339, "longitude": 121.5645},
    "extraComputations": ["POLLUTANT_CONCENTRATION", "POLLUTANT_ADDITIONAL_INFO", "HEALTH_RECOMMENDATIONS"],
    "languageCode": "zh-TW"
  }' | jq -r '
    "🌬️ AQI: \(.indexes[0].aqiDisplay) — \(.indexes[0].category)\n💨 主要汙染物: \(.indexes[0].dominantPollutant)\n" +
    "📊 各汙染物濃度:\n" +
    ([.pollutants[]? | "  \(.displayName): \(.concentration.value) \(.concentration.units)"] | join("\n")) +
    "\n\n💡 健康建議:\n" +
    ([.healthRecommendations | to_entries[]? | "  \(.key): \(.value)"] | join("\n"))'
```

## By Address (combine with geocoding)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

# Step 1: Geocode address
COORDS=$(curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0].geometry.location | "\(.lat),\(.lng)"')

LAT="${COORDS%%,*}"
LNG="${COORDS##*,}"

# Step 2: Get air quality
curl -s -X POST "https://airquality.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"location\": {\"latitude\": $LAT, \"longitude\": $LNG},
    \"languageCode\": \"zh-TW\"
  }" | jq -r '.indexes[]? | "🌬️ \(.displayName): \(.aqiDisplay) — \(.category)"'
```

## Compare Air Quality Across Locations

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

for loc in "25.0339,121.5645:台北" "25.1327,121.7402:基隆" "24.1478,120.6736:台中"; do
  COORDS="${loc%%:*}"
  NAME="${loc##*:}"
  LAT="${COORDS%%,*}"
  LNG="${COORDS##*,}"
  RESULT=$(curl -s -X POST "https://airquality.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"location\": {\"latitude\": $LAT, \"longitude\": $LNG}, \"languageCode\": \"zh-TW\"}")
  AQI=$(echo "$RESULT" | jq -r '.indexes[0].aqiDisplay // "N/A"')
  CAT=$(echo "$RESULT" | jq -r '.indexes[0].category // "N/A"')
  echo "🌬️ $NAME: AQI $AQI — $CAT"
done
```

## Air Quality History (hourly)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s -X POST "https://airquality.googleapis.com/v1/history:lookup?key=$GOOGLE_MAPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "location": {"latitude": 25.0339, "longitude": 121.5645},
    "hours": 8,
    "languageCode": "zh-TW"
  }' | jq -r '.hoursInfo[]? | "\(.dateTime): AQI \(.indexes[0].aqiDisplay) — \(.indexes[0].category)"'
```

## AQI Scale Reference

| AQI Range | Category | Advice |
|-----------|----------|--------|
| 0-50 | 良好 | 正常戶外活動 |
| 51-100 | 普通 | 敏感族群注意 |
| 101-150 | 對敏感族群不健康 | 減少長時間戶外活動 |
| 151-200 | 不健康 | 避免戶外活動 |
| 201-300 | 非常不健康 | 留在室內 |
| 300+ | 危險 | 避免所有戶外活動 |

Note: Google 使用 Universal AQI (UAQI, 0-100 scale where higher is better) 而非台灣環保署 AQI。UAQI 數字越高越好。

## Common Pollutants

| Code | Name | Source |
|------|------|--------|
| `pm25` | PM2.5 細懸浮微粒 | 交通、工業 |
| `pm10` | PM10 懸浮微粒 | 揚塵、建築 |
| `o3` | 臭氧 | 光化反應 |
| `no2` | 二氧化氮 | 交通排放 |
| `so2` | 二氧化硫 | 工業 |
| `co` | 一氧化碳 | 燃燒 |

## Extra Computations

| Value | Description |
|-------|-------------|
| `POLLUTANT_CONCENTRATION` | 各汙染物濃度數據 |
| `POLLUTANT_ADDITIONAL_INFO` | 汙染物來源和影響說明 |
| `HEALTH_RECOMMENDATIONS` | 健康建議（依族群分類） |
| `LOCAL_AQI` | 當地標準 AQI（如有） |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Enable "Air Quality API" in Google Cloud Console
- `languageCode: "zh-TW"` returns categories in Traditional Chinese
- Combine with google-geocoding to convert addresses to coordinates
- Combine with weather skill for complete outdoor activity advice
- Free tier: $200/month credit; Air Quality: ~$0.005/request
- `extraComputations` adds detail but increases response size
- History supports up to 720 hours (30 days) lookback
