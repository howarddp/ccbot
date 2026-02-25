---
name: google-place-photos
description: "Download photos of places, restaurants, and attractions via Google Places API (New) using curl. Use when: user wants to see photos of a restaurant, hotel, or attraction, or wants to preview a place before visiting. Requires GOOGLE_MAPS_API_KEY."
---

# Google Place Photos Skill

Download place photos from Google Places API (New) with curl. Images are saved to `tmp/` and sent via `[SEND_FILE:]`.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Uses the same "Places API (New)" already enabled for google-places skill.

## Load API Key

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"
```

## Step 1: Get Photo References

Photos are returned as part of Places search results. Include `places.photos` in the FieldMask:

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"

curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GMAP_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.photos" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "台北101",
    "languageCode": "zh-TW",
    "maxResultCount": 1
  }' | jq -r '.places[0] | "Name: \(.displayName.text)\nPhotos available: \(.photos | length)\n\nFirst photo: \(.photos[0].name)"'
```

## Step 2: Download Photo

Use the photo `name` from Step 1 to download the image:

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"
PHOTO_NAME="places/ChIJH56c2rarQjQRphD9gvC8BhI/photos/PHOTO_REFERENCE_HERE"

curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxHeightPx=600&key=$GMAP_KEY" \
  -o tmp/place_photo.jpg
```

Then reply with `[SEND_FILE:tmp/place_photo.jpg]` to send it to the user.

## Complete Example: Search + Download Photo

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"

# Step 1: Search and get photo reference
PHOTO_NAME=$(curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GMAP_KEY" \
  -H "X-Goog-FieldMask: places.displayName,places.photos" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "鼎泰豐 信義",
    "languageCode": "zh-TW",
    "maxResultCount": 1
  }' | jq -r '.places[0].photos[0].name')

# Step 2: Download photo
curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxHeightPx=600&key=$GMAP_KEY" \
  -o tmp/place_photo.jpg
```

## Download Multiple Photos

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"

# Get up to 3 photo references
PHOTOS=$(curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "X-Goog-Api-Key: $GMAP_KEY" \
  -H "X-Goog-FieldMask: places.displayName,places.photos" \
  -H "Content-Type: application/json" \
  -d '{
    "textQuery": "九份老街",
    "languageCode": "zh-TW",
    "maxResultCount": 1
  }' | jq -r '.places[0].photos[:3][].name')

# Download each photo
i=1
for photo in $PHOTOS; do
  curl -s -L "https://places.googleapis.com/v1/$photo/media?maxHeightPx=600&key=$GMAP_KEY" \
    -o "tmp/place_photo_$i.jpg"
  i=$((i+1))
done
```

Then send with multiple `[SEND_FILE:]` markers.

## Photo from Place Details

If you already have a Place ID from a previous search:

```bash
GMAP_KEY="${GOOGLE_MAPS_API_KEY:-${GOOGLE_PLACES_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}}"
PLACE_ID="places/ChIJH56c2rarQjQRphD9gvC8BhI"

# Get photos for a known place
PHOTO_NAME=$(curl -s "https://places.googleapis.com/v1/$PLACE_ID" \
  -H "X-Goog-Api-Key: $GMAP_KEY" \
  -H "X-Goog-FieldMask: photos" \
  | jq -r '.photos[0].name')

curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxHeightPx=600&key=$GMAP_KEY" \
  -o tmp/place_photo.jpg
```

## Size Options

Control the output image size with these parameters:

```bash
# By max height (preserves aspect ratio)
curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxHeightPx=400&key=$GMAP_KEY" -o tmp/photo.jpg

# By max width (preserves aspect ratio)
curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxWidthPx=800&key=$GMAP_KEY" -o tmp/photo.jpg

# Both (fits within bounds)
curl -s -L "https://places.googleapis.com/v1/$PHOTO_NAME/media?maxHeightPx=600&maxWidthPx=800&key=$GMAP_KEY" -o tmp/photo.jpg
```

## Parameters Reference

| Parameter | Description | Example |
|-----------|-------------|---------|
| `maxHeightPx` | Maximum height in pixels (1-4800) | `600` |
| `maxWidthPx` | Maximum width in pixels (1-4800) | `800` |
| `key` | API key | `$GMAP_KEY` |

At least one of `maxHeightPx` or `maxWidthPx` is required.

## Photo Metadata

Each photo object from the search results includes:

| Field | Description |
|-------|-------------|
| `name` | Photo resource name (used to download) |
| `widthPx` | Original width |
| `heightPx` | Original height |
| `authorAttributions` | Photographer info |

## Notes

- Uses the same "Places API (New)" as google-places skill — no additional API to enable
- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Must use `-L` flag with curl to follow redirects
- Up to 10 photos per place are returned in search results
- Save images to `tmp/` and send with `[SEND_FILE:tmp/filename.jpg]`
- Free tier: $200/month credit (shared); Place Photos: ~$0.007/request
- Combine with google-places to show search results with photos
- Photo quality depends on user-submitted content — some places have more/better photos than others
