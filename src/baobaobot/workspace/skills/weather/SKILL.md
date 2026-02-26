---
name: weather
description: "Get current weather, forecasts, and alerts via Google Weather API. Use when: user asks about weather, temperature, forecasts, or weather alerts for any location. Requires GOOGLE_MAPS_API_KEY. Use google-geocoding to convert place names to coordinates first."
---

# Weather Skill

Get current weather conditions, forecasts, historical data, and weather alerts via Google Weather API.

## Setup

Requires `GOOGLE_MAPS_API_KEY` environment variable (shared with google-places, google-directions, google-geocoding).

```bash
export GOOGLE_MAPS_API_KEY="YOUR_API_KEY"
```

Enable "Weather API" in your [Google Cloud Console](https://console.cloud.google.com/apis/library/weather.googleapis.com).

## When to Use

- "What's the weather?"
- "Will it rain today/tomorrow?"
- "Temperature in [city]"
- "Weather forecast for the week"
- Travel planning weather checks
- "Are there any weather alerts?"

## Load API Key

```bash
source "{{BIN_DIR}}/_load_env"
[ -z "$GOOGLE_MAPS_API_KEY" ] && echo "❌ GOOGLE_MAPS_API_KEY not set" && exit 1
```

## Current Conditions

```bash
source "{{BIN_DIR}}/_load_env"

# Taipei (25.0330, 121.5654)
curl -s "https://weather.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=25.0330&location.longitude=121.5654" \
  | jq -r '"🌡️ \(.temperature.degrees)°C (feels like \(.feelsLikeTemperature.degrees)°C)\n💧 Humidity: \(.relativeHumidity)%\n💨 Wind: \(.wind.speed.value) km/h\n☁️ \(.weatherCondition.description.text // .weatherCondition.type)"'
```

## Geocode + Weather (place name → weather)

Use google-geocoding to convert a place name to coordinates, then query weather:

```bash
source "{{BIN_DIR}}/_load_env"

# Step 1: Geocode the place name
COORDS=$(curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("台北"))')&key=$GOOGLE_MAPS_API_KEY" \
  | jq -r '.results[0].geometry.location | "\(.lat) \(.lng)"')
LAT=$(echo "$COORDS" | cut -d' ' -f1)
LNG=$(echo "$COORDS" | cut -d' ' -f2)

# Step 2: Get current weather
curl -s "https://weather.googleapis.com/v1/currentConditions:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=$LAT&location.longitude=$LNG" \
  | jq -r '"🌡️ \(.temperature.degrees)°C (feels like \(.feelsLikeTemperature.degrees)°C)\n💧 Humidity: \(.relativeHumidity)%\n💨 Wind: \(.wind.speed.value) km/h"'
```

## Daily Forecast (up to 10 days)

```bash
source "{{BIN_DIR}}/_load_env"

# 3-day forecast for Taipei
curl -s "https://weather.googleapis.com/v1/forecast/days:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=25.0330&location.longitude=121.5654&days=3" \
  | jq -r '.forecastDays[] | "📅 \(.displayDate.year)-\(.displayDate.month)-\(.displayDate.day): \(.daytimeForecast.weatherCondition.description.text // .daytimeForecast.weatherCondition.type) | ⬆️\(.maxTemperature.degrees)°C ⬇️\(.minTemperature.degrees)°C | 🌧️ \(.daytimeForecast.precipitation.probability.percent // 0)%"'
```

## Hourly Forecast (up to 240 hours)

```bash
source "{{BIN_DIR}}/_load_env"

# Next 12 hours
curl -s "https://weather.googleapis.com/v1/forecast/hours:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=25.0330&location.longitude=121.5654&hours=12" \
  | jq -r '.forecastHours[] | "🕐 \(.interval.startTime): \(.temperature.degrees)°C | 🌧️ \(.precipitation.probability.percent // 0)%"'
```

## Historical Weather (past 24 hours)

```bash
source "{{BIN_DIR}}/_load_env"

curl -s "https://weather.googleapis.com/v1/history/hours:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=25.0330&location.longitude=121.5654&hours=24" \
  | jq -r '.historyHours[] | "🕐 \(.interval.startTime): \(.temperature.degrees)°C | 💧 \(.relativeHumidity)%"'
```

## Weather Alerts

```bash
source "{{BIN_DIR}}/_load_env"

curl -s "https://weather.googleapis.com/v1/publicAlerts:lookup?key=$GOOGLE_MAPS_API_KEY&location.latitude=25.0330&location.longitude=121.5654" \
  | jq -r '.alerts[]? | "⚠️ \(.eventType)\n📋 \(.description.text)\n⏰ \(.interval.startTime) → \(.interval.endTime)\n"'
```

## Notes

- Requires `GOOGLE_MAPS_API_KEY` — same key as google-places, google-directions, google-geocoding
- Enable "Weather API" in Google Cloud Console
- API uses **latitude/longitude** — use google-geocoding to convert place names
- Data refreshed every 15–30 minutes
- Supports metric (default) or imperial (`&unitsSystem=IMPERIAL`)
- Coverage: all countries except Japan, Korea, and restricted territories
- Free tier: $200/month credit
