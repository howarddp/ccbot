---
name: google-static-maps
description: "Generate map images via Google Static Maps API using curl. Use when: user wants to see a location on a map, visualize search results, show a route, or needs a map image to send. Requires GOOGLE_MAPS_API_KEY."
---

# Google Static Maps Skill

Generate map images via Google Maps Static API with curl. Images are saved to `tmp/` and sent via `[SEND_FILE:]`.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Maps Static API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/static-maps-backend.googleapis.com).

## Load API Key

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Basic Map (center + zoom)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=15&size=600x400&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

Then reply with `[SEND_FILE:tmp/map.png]` to send it to the user.

### Center by address

```bash
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北101"))')&zoom=15&size=600x400&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

## Map with Markers

### Single marker

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=15&size=600x400&markers=color:red%7Clabel:A%7C25.0339,121.5645&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

### Multiple markers (e.g., show search results on map)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/staticmap?size=600x400&markers=color:red%7Clabel:A%7C25.0339,121.5645&markers=color:blue%7Clabel:B%7C25.0478,121.5170&markers=color:green%7Clabel:C%7C25.0329,121.5654&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

When using multiple markers without `center`/`zoom`, the map auto-fits to show all markers.

### Custom marker colors

Available colors: `black`, `brown`, `green`, `purple`, `yellow`, `blue`, `gray`, `orange`, `red`, `white`

Labels: single uppercase letter (A-Z) or digit (0-9)

## Map with Path (draw a route)

```bash
GOOGLE_MAPS_API_KEY="${GOOGLE_MAPS_API_KEY:-$(cat ~/.config/google-maps/api_key 2>/dev/null)}"

curl -s "https://maps.googleapis.com/maps/api/staticmap?size=600x400&path=color:0x0000ff80%7Cweight:5%7C25.0339,121.5645%7C25.0478,121.5170%7C25.0329,121.5654&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

### Path options

- `color:0xRRGGBBAA` — line color with alpha (hex)
- `weight:N` — line width in pixels
- `fillcolor:0xRRGGBBAA` — fill color for closed paths

## Map Types

```bash
# roadmap (default)
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=14&size=600x400&maptype=roadmap&key=$GOOGLE_MAPS_API_KEY" -o tmp/map.png

# satellite
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=14&size=600x400&maptype=satellite&key=$GOOGLE_MAPS_API_KEY" -o tmp/map.png

# terrain
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=14&size=600x400&maptype=terrain&key=$GOOGLE_MAPS_API_KEY" -o tmp/map.png

# hybrid (satellite + labels)
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=14&size=600x400&maptype=hybrid&key=$GOOGLE_MAPS_API_KEY" -o tmp/map.png
```

## High-Resolution (Retina)

Add `scale=2` for higher DPI (doubles pixel size, good for mobile):

```bash
curl -s "https://maps.googleapis.com/maps/api/staticmap?center=25.0339,121.5645&zoom=15&size=600x400&scale=2&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/map.png
```

## Combining with Other Skills

### Show Places search results on map

After a google-places search, plot results:

```bash
# Assume you got coordinates from Places API results
curl -s "https://maps.googleapis.com/maps/api/staticmap?size=600x400&scale=2&markers=color:red%7Clabel:1%7C25.0339,121.5645&markers=color:red%7Clabel:2%7C25.0350,121.5620&markers=color:red%7Clabel:3%7C25.0310,121.5670&language=zh-TW&key=$GOOGLE_MAPS_API_KEY" \
  -o tmp/places_map.png
```

### Show directions route on map

After a google-directions call, draw the route path through waypoints.

## Parameters Reference

| Parameter | Description | Example |
|-----------|-------------|---------|
| `center` | Map center (lat,lng or address) | `25.0339,121.5645` |
| `zoom` | Zoom level (0=world, 21=building) | `15` |
| `size` | Image size in pixels (max 640x640) | `600x400` |
| `scale` | Resolution multiplier (1 or 2) | `2` |
| `maptype` | Map style | `roadmap`, `satellite`, `terrain`, `hybrid` |
| `markers` | Pin markers on map | `color:red\|label:A\|lat,lng` |
| `path` | Draw lines on map | `color:0x0000ff\|weight:5\|lat,lng\|lat,lng` |
| `language` | Map label language | `zh-TW` |

## Zoom Level Guide

| Zoom | View |
|------|------|
| 1-4 | Country/continent |
| 5-9 | Region/city |
| 10-14 | City/district |
| 15-17 | Streets/buildings |
| 18-21 | Building detail |

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as other google-* skills
- Enable "Maps Static API" in Google Cloud Console
- Max image size: 640x640 pixels (with `scale=2`, actual output is 1280x800)
- `language=zh-TW` shows map labels in Traditional Chinese
- URL-encode Chinese addresses with `python3 -c 'import urllib.parse; print(urllib.parse.quote("..."))'`
- Save images to `tmp/` and send with `[SEND_FILE:tmp/filename.png]`
- Free tier: $200/month credit; Static Maps: ~$0.002/request
- When showing multiple markers without `center`/`zoom`, the map auto-fits to include all markers
