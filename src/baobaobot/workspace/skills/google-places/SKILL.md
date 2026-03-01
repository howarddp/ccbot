---
name: google-places
description: "Search nearby places via Google Places API (New) using curl. Use when: user asks what's nearby, needs to find places by location/type, or wants opening hours and addresses. For place reviews, ratings, or recommendations, use the 'travel' skill instead. Requires GOOGLE_MAPS_API_KEY."
---

# Google Places Skill

Search for places, get ratings, reviews, and opening hours via Google Places API (New) with curl.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Places API (New)" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/places-backend.googleapis.com).

## Load API Key

All commands below assume the key is loaded. Run this first:

```bash
source "{{BIN_DIR}}/_load_env"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Text Search

Search places by text query. The most common operation.

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.priceLevel,places.currentOpeningHours.openNow,places.websiteUri,places.googleMapsUri" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "台北拉麵",
    "languageCode": "zh-TW",
    "maxResultCount": 5
  }' | jq -r '.places[]? | "⭐ \(.rating // "N/A") (\(.userRatingCount // 0) reviews) \(.displayName.text)\n   📍 \(.formattedAddress)\n   🗺️ \(.googleMapsUri // "")\n"'
```

### With location bias (prefer results near a point)

```bash
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.currentOpeningHours.openNow,places.googleMapsUri" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "coffee",
    "locationBias": {
      "circle": {"center": {"latitude": 25.033, "longitude": 121.565}, "radius": 2000.0}
    },
    "languageCode": "zh-TW",
    "maxResultCount": 5
  }'
```

### Filter: only open now

```bash
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.formattedAddress,places.currentOpeningHours.openNow,places.googleMapsUri" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "台北咖啡廳",
    "openNow": true,
    "languageCode": "zh-TW",
    "maxResultCount": 5
  }'
```

## Nearby Search

Search by location + radius + place type. Use when user shares coordinates or asks "what's nearby".

```bash
source "{{BIN_DIR}}/_load_env"

curl -s -X POST "https://places.googleapis.com/v1/places:searchNearby" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress,places.currentOpeningHours.openNow,places.googleMapsUri" \
  -H "Content-Type: application/json" \
  -d '{
    "locationRestriction": {
      "circle": {"center": {"latitude": 25.033, "longitude": 121.565}, "radius": 1000.0}
    },
    "includedTypes": ["restaurant"],
    "languageCode": "zh-TW",
    "maxResultCount": 10
  }' | jq -r '.places[]? | "⭐ \(.rating // "N/A") (\(.userRatingCount // 0) reviews) \(.displayName.text)\n   📍 \(.formattedAddress)\n   🗺️ \(.googleMapsUri // "")\n"'
```

### Common place types

- `restaurant`, `cafe`, `bar`, `bakery`
- `hotel`, `lodging`
- `pharmacy`, `hospital`, `doctor`
- `grocery_store`, `supermarket`, `convenience_store`
- `gas_station`, `parking`, `car_repair`
- `gym`, `park`, `tourist_attraction`, `museum`
- `atm`, `bank`, `post_office`
- `hair_care`, `beauty_salon`, `laundry`

Full list: https://developers.google.com/maps/documentation/places/web-service/place-types

## Place Details

Get full details for a specific place by its ID (from search results).

```bash
source "{{BIN_DIR}}/_load_env"
PLACE_ID="places/ChIJ..."

curl -s "https://places.googleapis.com/v1/$PLACE_ID" \
  -H "X-Goog-Api-Key: $GOOGLE_MAPS_API_KEY" \
  -H "X-Goog-FieldMask: displayName,rating,userRatingCount,formattedAddress,nationalPhoneNumber,websiteUri,googleMapsUri,currentOpeningHours,priceLevel,editorialSummary,reviews" \
  | jq -r '"📍 \(.displayName.text)\n⭐ \(.rating) (\(.userRatingCount) reviews)\n📞 \(.nationalPhoneNumber // "N/A")\n🌐 \(.websiteUri // "N/A")\n📋 \(.editorialSummary.text // "N/A")\n\nReviews:\n" + ([.reviews[]? | "  ⭐\(.rating) \(.authorAttribution.displayName): \(.text.text[:100])"] | join("\n"))'
```

## FieldMask Reference

Control which fields to return (affects billing). Always specify to save costs.

| Field | Description |
|-------|-------------|
| `places.displayName` | Place name |
| `places.formattedAddress` | Full address |
| `places.rating` | Rating (1.0-5.0) |
| `places.userRatingCount` | Number of reviews |
| `places.priceLevel` | Price level (FREE to VERY_EXPENSIVE) |
| `places.currentOpeningHours` | Opening hours + openNow |
| `places.regularOpeningHours` | Regular weekly hours |
| `places.websiteUri` | Official website |
| `places.googleMapsUri` | Google Maps link |
| `places.nationalPhoneNumber` | Phone number |
| `places.editorialSummary` | Short description |
| `places.reviews` | User reviews |
| `places.photos` | Photo references |
| `places.id` | Place ID (for detail queries) |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — loaded from `.env` via `_load_env`
- Enable "Places API (New)" in your Google Cloud project
- Free tier: $200/month credit (typically enough for personal use)
- Text Search: ~$0.032/request, Nearby Search: ~$0.032/request, Place Details: varies by fields
- Always use FieldMask to request only needed fields (reduces cost)
- `languageCode: "zh-TW"` returns results in Traditional Chinese
- `maxResultCount` caps results (1-20)
