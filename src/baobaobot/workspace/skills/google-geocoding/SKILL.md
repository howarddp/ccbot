---
name: google-geocoding
description: "Convert addresses to coordinates and coordinates to addresses via Google Geocoding API using curl. Use when: user needs lat/lng from an address, wants to know what's at specific coordinates, or needs location data for other Google Maps skills. Requires GOOGLE_MAPS_API_KEY."
---

# Google Geocoding Skill

Convert addresses to coordinates (geocoding) and coordinates to addresses (reverse geocoding) via Google Geocoding API with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (same key as google-places, google-directions).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Geocoding API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com).

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Forward Geocoding (Address → Coordinates)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0] | "📍 \(.formatted_address)\n🌐 \(.geometry.location.lat), \(.geometry.location.lng)"'
```

### Simple version (ASCII-safe addresses)

```bash
curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=Taipei+101&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0] | "📍 \(.formatted_address)\n🌐 \(.geometry.location.lat), \(.geometry.location.lng)"'
```

## Reverse Geocoding (Coordinates → Address)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/geocode/json?latlng=25.0339,121.5645&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0] | "📍 \(.formatted_address)\n🏷️ \([.address_components[] | .long_name] | join(", "))"'
```

## Get Coordinates for Multiple Places

Useful for feeding into google-directions or google-places:

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

for place in "台北車站" "台北101" "西門町"; do
  result=$(curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$place'))")&language=zh-TW&key=$GOOGLE_MAPS_API_KEY")
  echo "$result" | jq -r ".results[0] | \"📍 $place: \(.geometry.location.lat),\(.geometry.location.lng)\""
done
```

## Get Detailed Address Components

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北市信義區市府路1號"))')&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0] | "📍 \(.formatted_address)\n🌐 \(.geometry.location.lat), \(.geometry.location.lng)\n🏷️ Type: \(.types | join(", "))\n\nComponents:" + ([.address_components[] | "  \(.types[0]): \(.long_name)"] | join("\n"))'
```

## Filter by Region

Bias results to a specific country:

```bash
curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=中正路&region=tw&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[:3][] | "📍 \(.formatted_address)\n🌐 \(.geometry.location.lat), \(.geometry.location.lng)\n"'
```

## Output Just Coordinates (for piping to other skills)

```bash
# Get lat,lng only — useful for feeding into google-places locationBias or google-directions
curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0].geometry.location | "\(.lat),\(.lng)"'
```

## Place ID Lookup

Get the Place ID for use with google-places detail queries:

```bash
curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0] | "📍 \(.formatted_address)\n🆔 \(.place_id)"'
```

## Address Component Types

| Type | Description |
|------|-------------|
| `street_number` | Street number |
| `route` | Street name |
| `sublocality` | District / neighborhood |
| `locality` | City |
| `administrative_area_level_1` | State / province / county |
| `administrative_area_level_2` | County / district |
| `country` | Country |
| `postal_code` | Postal code |

## Region Codes (common)

| Code | Region |
|------|--------|
| `tw` | Taiwan |
| `jp` | Japan |
| `us` | United States |
| `hk` | Hong Kong |
| `sg` | Singapore |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as google-places and google-directions
- Enable "Geocoding API" in Google Cloud Console
- `language=zh-TW` returns addresses in Traditional Chinese
- `region=tw` biases results to Taiwan (useful for ambiguous addresses like "中正路")
- URL-encode Chinese/Unicode addresses with `python3 -c 'import urllib.parse; print(urllib.parse.quote("..."))'`
- For ASCII addresses, use `+` to replace spaces (e.g., `Taipei+101`)
- Free tier: $200/month credit; Geocoding: ~$0.005/request
- Output coordinates can be piped into google-places `locationBias` or google-directions `origin`/`destination`
