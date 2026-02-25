---
name: google-timezone
description: "Get timezone information for any location via Google Time Zone API using curl. Use when: user asks about the timezone of a place, needs to convert times between locations, or wants to know local time at coordinates. Requires GOOGLE_MAPS_API_KEY."
---

# Google Time Zone Skill

Get timezone name, UTC offset, and DST offset for any location via Google Time Zone API with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Time Zone API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/timezone-backend.googleapis.com).

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
```

## Get Timezone by Coordinates

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/timezone/json?location=25.0339,121.5645&timestamp=$(date +%s)&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '"🕐 \(.timeZoneId)\n📍 \(.timeZoneName)\n⏱️ UTC offset: \(.rawOffset / 3600)h\n☀️ DST offset: \(.dstOffset / 3600)h"'
```

## Get Timezone by Address

Combine with google-geocoding to look up by address:

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

# Step 1: Geocode the address
COORDS=$(curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("東京タワー"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0].geometry.location | "\(.lat),\(.lng)"')

# Step 2: Get timezone
curl -s "https://maps.googleapis.com/maps/api/timezone/json?location=$COORDS&timestamp=$(date +%s)&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '"🕐 \(.timeZoneId)\n📍 \(.timeZoneName)\n⏱️ UTC offset: \(.rawOffset / 3600)h\n☀️ DST offset: \(.dstOffset / 3600)h"'
```

## Get Local Time at a Location

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

RESULT=$(curl -s "https://maps.googleapis.com/maps/api/timezone/json?location=40.7128,-74.0060&timestamp=$(date +%s)&key=$GOOGLE_MAPS_API_KEY")
TOTAL_OFFSET=$(echo "$RESULT" | jq '.rawOffset + .dstOffset')
TZ_ID=$(echo "$RESULT" | jq -r '.timeZoneId')
LOCAL_TIME=$(TZ="$TZ_ID" date '+%Y-%m-%d %H:%M:%S')
echo "🕐 $TZ_ID — $LOCAL_TIME"
```

## Compare Timezones Between Locations

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
TIMESTAMP=$(date +%s)

for loc in "25.0339,121.5645:台北" "35.6762,139.6503:東京" "40.7128,-74.0060:紐約" "51.5074,-0.1278:倫敦"; do
  COORDS="${loc%%:*}"
  NAME="${loc##*:}"
  RESULT=$(curl -s "https://maps.googleapis.com/maps/api/timezone/json?location=$COORDS&timestamp=$TIMESTAMP&key=$GOOGLE_MAPS_API_KEY")
  TZ_ID=$(echo "$RESULT" | jq -r '.timeZoneId')
  LOCAL_TIME=$(TZ="$TZ_ID" date '+%H:%M')
  echo "🕐 $NAME ($TZ_ID): $LOCAL_TIME"
done
```

## Check DST Status at a Specific Date

Use a specific timestamp to check DST at a future/past date:

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

# Check timezone in July (summer, DST active in northern hemisphere)
JULY_TS=$(date -j -f "%Y-%m-%d" "2026-07-15" "+%s" 2>/dev/null || date -d "2026-07-15" "+%s")

curl -s "https://maps.googleapis.com/maps/api/timezone/json?location=40.7128,-74.0060&timestamp=$JULY_TS&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '"🕐 \(.timeZoneId)\n📍 \(.timeZoneName)\n⏱️ UTC offset: \(.rawOffset / 3600)h\n☀️ DST offset: \(.dstOffset / 3600)h\n📅 Total: UTC\((.rawOffset + .dstOffset) / 3600)h"'
```

## Parameters Reference

| Parameter | Description | Example |
|-----------|-------------|---------|
| `location` | Coordinates (lat,lng) | `25.0339,121.5645` |
| `timestamp` | Unix timestamp (determines DST) | `$(date +%s)` |
| `language` | Response language (optional) | `zh-TW` |

## Response Fields

| Field | Description |
|-------|-------------|
| `timeZoneId` | IANA timezone ID (e.g., `Asia/Taipei`) |
| `timeZoneName` | Human-readable name (e.g., `Taiwan Standard Time`) |
| `rawOffset` | UTC offset in seconds (e.g., 28800 = +8h) |
| `dstOffset` | DST offset in seconds (0 if no DST) |
| `status` | `OK`, `INVALID_REQUEST`, `OVER_DAILY_LIMIT`, `ZERO_RESULTS` |

## Common Timezone IDs

| Location | Timezone ID | UTC Offset |
|----------|-------------|------------|
| Taiwan | `Asia/Taipei` | +8 |
| Japan | `Asia/Tokyo` | +9 |
| US East | `America/New_York` | -5 (-4 DST) |
| US West | `America/Los_Angeles` | -8 (-7 DST) |
| UK | `Europe/London` | 0 (+1 DST) |
| Hong Kong | `Asia/Hong_Kong` | +8 |
| Singapore | `Asia/Singapore` | +8 |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Enable "Time Zone API" in Google Cloud Console
- `timestamp` is required — it determines whether DST is active
- Use `$(date +%s)` for current time, or a specific Unix timestamp for future/past queries
- Combine with google-geocoding to convert addresses to coordinates first
- Free tier: $200/month credit; Time Zone: ~$0.005/request
- Useful for scheduling across timezones and displaying local times
