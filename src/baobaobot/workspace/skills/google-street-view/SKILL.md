---
name: google-street-view
description: "Get street-level photos of any location via Google Street View Static API using curl. Use when: user wants to see what a place looks like, check the exterior of a restaurant or building, or preview an address before visiting. Requires GOOGLE_MAPS_API_KEY."
---

# Google Street View Skill

Get street-level panoramic photos of any location via Street View Static API with curl. Images are saved to `tmp/` and sent via `[SEND_FILE:]`.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Street View Static API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/street-view-image-backend.googleapis.com).

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
```

## Basic Street View (by coordinates)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=25.0339,121.5645&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

Then reply with `[SEND_FILE:tmp/streetview.png]` to send it to the user.

## By Address

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

## Control Camera Angle

### Heading (horizontal rotation)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

# heading: 0=North, 90=East, 180=South, 270=West
curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=25.0339,121.5645&heading=180&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

### Pitch (vertical angle)

```bash
# pitch: -90=down, 0=horizon, 90=up
curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=25.0339,121.5645&heading=0&pitch=20&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

### Field of view (zoom)

```bash
# fov: 10=zoomed in, 120=wide angle (default: 90)
curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=25.0339,121.5645&fov=60&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

## High-Resolution

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=25.0339,121.5645&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/streetview.png
```

Max size: 640x640 pixels.

## Check Availability Before Fetching

Not all locations have street view coverage. Use the metadata endpoint to check first:

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/streetview/metadata?location=25.0339,121.5645&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '"Status: \(.status)\nPano ID: \(.pano_id // "N/A")\nDate: \(.date // "N/A")\nLocation: \(.location.lat), \(.location.lng)"'
```

- `status: "OK"` → street view available
- `status: "ZERO_RESULTS"` → no coverage at this location

## Combining with Other Skills

### Show a place from Places search

After google-places returns results with coordinates:

```bash
# Use the place's coordinates to get street view
curl -s "https://maps.googleapis.com/maps/api/streetview?size=600x400&location=PLACE_LAT,PLACE_LNG&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/place_streetview.png
```

### Map + Street View combo

1. Use google-static-maps to show the location on a map
2. Use google-street-view to show what it looks like at street level
3. Send both images to the user

## Parameters Reference

| Parameter | Description | Values |
|-----------|-------------|--------|
| `location` | Location (lat,lng or address) | `25.0339,121.5645` or `台北101` |
| `size` | Image size (max 640x640) | `600x400` |
| `heading` | Camera horizontal direction | `0`-`360` (0=N, 90=E, 180=S, 270=W) |
| `pitch` | Camera vertical angle | `-90` to `90` (0=horizon) |
| `fov` | Field of view (zoom) | `10`-`120` (default `90`) |
| `source` | Image source filter | `default`, `outdoor` |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Enable "Street View Static API" in Google Cloud Console
- Max image size: 640x640 pixels
- URL-encode Chinese addresses with `python3 -c 'import urllib.parse; print(urllib.parse.quote("..."))'`
- Save images to `tmp/` and send with `[SEND_FILE:tmp/filename.png]`
- Use metadata endpoint to check availability before fetching (avoids wasting quota on empty results)
- Not all locations have coverage — rural/remote areas may return `ZERO_RESULTS`
- `source=outdoor` filters to outdoor-only imagery (no indoor business photos)
- Free tier: $200/month credit; Street View: ~$0.007/request
