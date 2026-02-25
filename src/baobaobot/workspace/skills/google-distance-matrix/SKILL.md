---
name: google-distance-matrix
description: "Compare travel distances and times from one origin to multiple destinations via Google Distance Matrix API using curl. Use when: user wants to know which place is closest, compare travel times, or plan multi-destination trips. Requires GOOGLE_MAPS_API_KEY."
---

# Google Distance Matrix Skill

Compare distances and travel times from one or more origins to multiple destinations via Distance Matrix API with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Distance Matrix API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/distance-matrix-backend.googleapis.com). Note: If you have "Routes API" enabled, Distance Matrix is included.

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## One Origin → Multiple Destinations

The most common use case: "which of these places is closest?"

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101|西門町|中正紀念堂"))')&mode=driving&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '"From: \(.origin_addresses[0])\n" + ([range(.rows[0].elements | length)] | map("  → \(.destination_addresses[.]) : \(.rows[0].elements[.].distance.text), \(.rows[0].elements[.].duration.text)") | join("\n"))' 2>/dev/null || \
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101|西門町|中正紀念堂"))')&mode=driving&language=zh-TW&key=$GOOGLE_MAPS_API_KEY"
```

### Using coordinates

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=25.0478,121.5170&destinations=25.0339,121.5645%7C25.0422,121.5079%7C25.0324,121.5198&mode=driving&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements[] | "\(.distance.text) — \(.duration.text)"'
```

Separate multiple destinations with `%7C` (URL-encoded `|`).

## Travel Modes

### Driving (default)

```bash
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=台北車站&destinations=桃園機場&mode=driving&language=zh-TW&key=$GOOGLE_MAPS_API_KEY"
```

### Transit (public transport)

```bash
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&mode=transit&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements[0] | "🚇 \(.distance.text) — \(.duration.text)"'
```

### Walking

```bash
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("西門町"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("中正紀念堂"))')&mode=walking&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements[0] | "🚶 \(.distance.text) — \(.duration.text)"'
```

### Bicycling

```bash
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("大安森林公園"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北動物園"))')&mode=bicycling&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements[0] | "🚲 \(.distance.text) — \(.duration.text)"'
```

## Multiple Origins → Multiple Destinations (Matrix)

Full N×M comparison:

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站|台北101"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("桃園機場|台中車站"))')&mode=driving&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq '.'
```

## With Departure Time (traffic estimates)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北車站"))')&destinations=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("桃園機場"))')&mode=driving&departure_time=now&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements[0] | "📏 \(.distance.text)\n⏱️ \(.duration.text)\n🚗 With traffic: \(.duration_in_traffic.text // "N/A")"'
```

## Avoid Options

```bash
# Avoid tolls
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=台北車站&destinations=桃園機場&mode=driving&avoid=tolls&language=zh-TW&key=$GOOGLE_MAPS_API_KEY"

# Avoid highways
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=台北車站&destinations=桃園機場&mode=driving&avoid=highways&language=zh-TW&key=$GOOGLE_MAPS_API_KEY"

# Multiple: tolls|highways|ferries
curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=台北車站&destinations=桃園機場&mode=driving&avoid=tolls%7Chighways&language=zh-TW&key=$GOOGLE_MAPS_API_KEY"
```

## Combining with Places Search

After a google-places search, compare which result is closest:

```bash
# 1. Get place coordinates from Places API results
# 2. Use Distance Matrix to compare
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/distancematrix/json?origins=25.0339,121.5645&destinations=PLACE1_LAT,PLACE1_LNG%7CPLACE2_LAT,PLACE2_LNG%7CPLACE3_LAT,PLACE3_LNG&mode=walking&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.rows[0].elements | to_entries | sort_by(.value.duration.value) | .[] | "#\(.key+1): \(.value.distance.text) — \(.value.duration.text)"'
```

## Parameters Reference

| Parameter | Description | Values |
|-----------|-------------|--------|
| `origins` | Starting point(s) | Address, lat/lng, or Place ID |
| `destinations` | End point(s) | Address, lat/lng, or Place ID (separate with `\|`) |
| `mode` | Travel mode | `driving`, `walking`, `bicycling`, `transit` |
| `avoid` | Route restrictions | `tolls`, `highways`, `ferries` (separate with `\|`) |
| `departure_time` | For traffic data | `now` or Unix timestamp |
| `language` | Response language | `zh-TW` |
| `units` | Distance units | `metric` (default), `imperial` |

## Response Fields

| Field | Description |
|-------|-------------|
| `origin_addresses` | Resolved origin address(es) |
| `destination_addresses` | Resolved destination address(es) |
| `rows[].elements[].distance.text` | Human-readable distance (e.g., "5.2 公里") |
| `rows[].elements[].distance.value` | Distance in meters |
| `rows[].elements[].duration.text` | Human-readable duration (e.g., "14 分鐘") |
| `rows[].elements[].duration.value` | Duration in seconds |
| `rows[].elements[].duration_in_traffic` | Duration with traffic (requires `departure_time`) |
| `rows[].elements[].status` | `OK`, `NOT_FOUND`, `ZERO_RESULTS` |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Enable "Distance Matrix API" (or "Routes API" which includes it) in Google Cloud Console
- `language=zh-TW` returns addresses and text in Traditional Chinese
- URL-encode Chinese addresses with `python3 -c 'import urllib.parse; print(urllib.parse.quote("..."))'`
- Separate multiple origins/destinations with `|` (URL-encoded as `%7C`)
- Max 25 origins × 25 destinations per request (625 elements)
- `departure_time=now` enables real-time traffic for driving mode
- Free tier: $200/month credit; Distance Matrix: ~$0.005/element
- Use `jq` sort to rank destinations by distance or duration
