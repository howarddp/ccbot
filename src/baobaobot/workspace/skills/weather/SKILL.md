---
name: weather
description: "Get current weather and forecasts via wttr.in. Use when: user asks about weather, temperature, or forecasts for any location. NOT for: historical weather data or severe weather alerts. No API key needed."
---

# Weather Skill

Get current weather conditions and forecasts.

## When to Use

- "What's the weather?"
- "Will it rain today/tomorrow?"
- "Temperature in [city]"
- "Weather forecast for the week"
- Travel planning weather checks

## Commands

### Current Weather

```bash
# One-line summary
curl -s "https://wttr.in/Taipei?format=3"

# Detailed current conditions
curl -s "https://wttr.in/Taipei?0"

# Specific city
curl -s "https://wttr.in/New+York?format=3"
```

### Forecasts

```bash
# 3-day forecast
curl -s "https://wttr.in/Taipei"

# Week forecast
curl -s "https://wttr.in/Taipei?format=v2"

# Specific day (0=today, 1=tomorrow, 2=day after)
curl -s "https://wttr.in/Taipei?1"
```

### Format Options

```bash
# One-liner with details
curl -s "https://wttr.in/Taipei?format=%l:+%c+%t+(feels+like+%f),+%w+wind,+%h+humidity"

# JSON output
curl -s "https://wttr.in/Taipei?format=j1"

# PNG image
curl -s "https://wttr.in/Taipei.png" -o weather.png
```

### Format Codes

- `%c` — Weather condition emoji
- `%t` — Temperature
- `%f` — "Feels like"
- `%w` — Wind
- `%h` — Humidity
- `%p` — Precipitation
- `%l` — Location

## Quick Responses

**"What's the weather?"**

```bash
curl -s "https://wttr.in/Taipei?format=%l:+%c+%t+(feels+like+%f),+%w+wind,+%h+humidity"
```

**"Will it rain?"**

```bash
curl -s "https://wttr.in/Taipei?format=%l:+%c+%p"
```

**"Weekend forecast"**

```bash
curl -s "https://wttr.in/Taipei?format=v2"
```

## Notes

- No API key needed (uses wttr.in)
- Rate limited; don't spam requests
- Works for most global cities
- Supports airport codes: `curl -s "https://wttr.in/TPE"`
- Always use `https://` prefix
