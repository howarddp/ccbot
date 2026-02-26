---
name: google-directions
description: "Get driving/transit/walking directions between locations via Google Routes API using curl. Use when: user asks for route planning, travel time, how to get somewhere, or multi-stop itineraries. Requires GOOGLE_MAPS_API_KEY."
---

# Google Directions Skill

Get route directions, travel time, and distance between locations via Google Routes API (New) with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Routes API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/routes.googleapis.com).

## Load API Key

```bash
source "{{BIN_DIR}}/_load_env"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Basic Directions (driving)

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.description,routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "九份老街"},
    "travelMode": "DRIVE",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "🚗 \(.description // "route")\n⏱️ \(.localizedValues.duration.text)\n📏 \(.localizedValues.distance.text)"'
```

## Transit Directions (public transport)

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.localizedValues,routes.legs.steps.transitDetails,routes.legs.steps.travelMode,routes.legs.steps.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北101"},
    "destination": {"address": "淡水老街"},
    "travelMode": "TRANSIT",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "⏱️ \(.localizedValues.duration.text) | 📏 \(.localizedValues.distance.text)\n\nSteps:" + ([.legs[0].steps[] | if .travelMode == "TRANSIT" then "  🚇 \(.transitDetails.transitLine.name // .transitDetails.transitLine.nameShort // "transit") → \(.transitDetails.headsign // "") (\(.localizedValues.staticDuration.text // ""))" else "  🚶 \(.travelMode) (\(.localizedValues.staticDuration.text // ""))" end] | join("\n"))'
```

## Walking Directions

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "西門町"},
    "destination": {"address": "中正紀念堂"},
    "travelMode": "WALK",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "🚶 \(.localizedValues.duration.text) | 📏 \(.localizedValues.distance.text)"'
```

## Bicycling Directions

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "大安森林公園"},
    "destination": {"address": "台北動物園"},
    "travelMode": "BICYCLE",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "🚲 \(.localizedValues.duration.text) | 📏 \(.localizedValues.distance.text)"'
```

## Multi-stop Route (waypoints)

Route through multiple stops in order:

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.legs.localizedValues,routes.legs.startAddress,routes.legs.endAddress,routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "九份老街"},
    "intermediates": [
      {"address": "台北101"},
      {"address": "象山步道"}
    ],
    "travelMode": "DRIVE",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "🚗 Total: \(.localizedValues.duration.text) | \(.localizedValues.distance.text)\n\n" + ([.legs[] | "  📍 \(.startAddress // "start") → \(.endAddress // "end")\n     ⏱️ \(.localizedValues.duration.text) 📏 \(.localizedValues.distance.text)"] | join("\n"))'
```

### Optimized waypoint order

Set `optimizeWaypointOrder: true` to let Google find the fastest order:

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.optimizedIntermediateWaypointIndex,routes.legs.localizedValues,routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "台北車站"},
    "intermediates": [
      {"address": "台北101"},
      {"address": "西門町"},
      {"address": "中正紀念堂"},
      {"address": "士林夜市"}
    ],
    "travelMode": "DRIVE",
    "optimizeWaypointOrder": true,
    "languageCode": "zh-TW"
  }' | jq -r '"Optimized order: \(.routes[0].optimizedIntermediateWaypointIndex)\nTotal: \(.routes[0].localizedValues.duration.text) | \(.routes[0].localizedValues.distance.text)\n\n" + ([.routes[0].legs[] | "  ⏱️ \(.localizedValues.duration.text) 📏 \(.localizedValues.distance.text)"] | join("\n"))'
```

## Alternative Routes

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.description,routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "桃園機場"},
    "travelMode": "DRIVE",
    "computeAlternativeRoutes": true,
    "languageCode": "zh-TW"
  }' | jq -r '[.routes[] | "🚗 \(.description // "route")  ⏱️ \(.localizedValues.duration.text)  📏 \(.localizedValues.distance.text)"] | join("\n")'
```

## Avoid Tolls / Highways / Ferries

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "桃園機場"},
    "travelMode": "DRIVE",
    "routeModifiers": {
      "avoidTolls": true,
      "avoidHighways": true
    },
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "⏱️ \(.localizedValues.duration.text) | 📏 \(.localizedValues.distance.text) (no tolls/highways)"'
```

## Departure Time (for traffic estimates)

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.duration,routes.staticDuration,routes.localizedValues" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"address": "台北車站"},
    "destination": {"address": "桃園機場"},
    "travelMode": "DRIVE",
    "departureTime": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "routingPreference": "TRAFFIC_AWARE",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "⏱️ \(.localizedValues.duration.text) (with traffic) | \(.localizedValues.staticDuration.text // "N/A") (no traffic)"'
```

## Using Coordinates (lat/lng)

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: routes.localizedValues,routes.description" \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"location": {"latLng": {"latitude": 25.0478, "longitude": 121.5170}}},
    "destination": {"location": {"latLng": {"latitude": 25.0340, "longitude": 121.5645}}},
    "travelMode": "DRIVE",
    "languageCode": "zh-TW"
  }' | jq -r '.routes[0] | "🚗 \(.description // "route")\n⏱️ \(.localizedValues.duration.text) | 📏 \(.localizedValues.distance.text)"'
```

## Travel Modes

| Mode | Parameter | Notes |
|------|-----------|-------|
| Driving | `"travelMode": "DRIVE"` | Default. Supports `departureTime` + `TRAFFIC_AWARE` |
| Transit | `"travelMode": "TRANSIT"` | Public transport. Returns step-by-step transit details |
| Walking | `"travelMode": "WALK"` | Pedestrian paths |
| Bicycling | `"travelMode": "BICYCLE"` | Bike-friendly routes |
| Two-wheeler | `"travelMode": "TWO_WHEELER"` | Motorcycle/scooter routes |

## Route Modifiers

| Option | Description |
|--------|-------------|
| `"avoidTolls": true` | Avoid toll roads |
| `"avoidHighways": true` | Avoid highways |
| `"avoidFerries": true` | Avoid ferries |
| `"avoidIndoor": true` | Avoid indoor routes |

## FieldMask Reference

| Field | Description |
|-------|-------------|
| `routes.duration` | Total duration (raw seconds, e.g. "854s") |
| `routes.distanceMeters` | Total distance in meters |
| `routes.localizedValues` | Human-readable duration/distance (e.g. "14 分鐘", "6.6 公里") |
| `routes.description` | Route summary (road names) |
| `routes.legs` | Per-segment info (for multi-stop routes) |
| `routes.legs.steps` | Step-by-step navigation |
| `routes.legs.steps.transitDetails` | Transit line info (for TRANSIT mode) |
| `routes.optimizedIntermediateWaypointIndex` | Optimal waypoint order |
| `routes.staticDuration` | Duration without traffic |

## Notes

- Uses **Routes API** (New) — POST with JSON body + FieldMask (same style as Places API New)
- Requires `GOOGLE_MAPS_API_KEY` — same key as google-places and google-geocoding
- Enable "Routes API" in Google Cloud Console
- `languageCode: "zh-TW"` returns Chinese route descriptions and localized values
- `localizedValues` gives human-readable durations/distances (recommended over raw values)
- Origins/destinations can be addresses, coordinates, or Place IDs (`{"placeId": "ChIJ..."}`)
- Free tier: $200/month credit; Routes: ~$0.005-0.01/request
- `computeAlternativeRoutes: true` returns up to 3 route options
