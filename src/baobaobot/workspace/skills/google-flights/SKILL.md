---
name: google-flights
description: "Search flights, compare prices, and find the best deals via SerpApi Google Flights. Use when: user asks about flight prices, wants to book flights, compare airlines, or plan air travel. Requires SERPAPI_API_KEY."
---

# Google Flights Search Skill

Search flights, compare prices, and find best deals using SerpApi's Google Flights API.

## Setup

```bash
source "{{BIN_DIR}}/_load_env"
[ -z "$SERPAPI_API_KEY" ] && echo "❌ SERPAPI_API_KEY not set. Get a free key at https://serpapi.com/" && exit 1
```

## Search One-Way Flights

```bash
source "{{BIN_DIR}}/_load_env"

# Example: Taipei (TPE) → Tokyo Narita (NRT), one-way
curl -s "https://serpapi.com/search.json?engine=google_flights&departure_id=TPE&arrival_id=NRT&outbound_date=2026-03-15&type=2&currency=TWD&hl=zh-TW&gl=tw&api_key=$SERPAPI_API_KEY" \
  | jq '{
    best_flights: [.best_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      flight_numbers: [.flights[].flight_number] | join(", "),
      departure: .flights[0].departure_airport.time,
      arrival: .flights[-1].arrival_airport.time,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1),
      price: .price,
      carbon_kg: ((.carbon_emissions.this_flight // 0) / 1000 | round)
    }],
    other_flights: [.other_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      flight_numbers: [.flights[].flight_number] | join(", "),
      departure: .flights[0].departure_airport.time,
      arrival: .flights[-1].arrival_airport.time,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1),
      price: .price
    }] | .[0:5],
    price_insights: .price_insights
  }'
```

## Search Round-Trip Flights

```bash
source "{{BIN_DIR}}/_load_env"

# Example: Taipei (TPE) ↔ Osaka (KIX), round-trip
curl -s "https://serpapi.com/search.json?engine=google_flights&departure_id=TPE&arrival_id=KIX&outbound_date=2026-03-15&return_date=2026-03-20&type=1&currency=TWD&hl=zh-TW&gl=tw&api_key=$SERPAPI_API_KEY" \
  | jq '{
    best_flights: [.best_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      flight_numbers: [.flights[].flight_number] | join(", "),
      departure: .flights[0].departure_airport.time,
      arrival: .flights[-1].arrival_airport.time,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1),
      price: .price
    }],
    other_flights: [.other_flights[]? | {
      airlines: [.flights[].airline] | join(" → "),
      price: .price,
      duration_min: .total_duration,
      stops: ((.flights | length) - 1)
    }] | .[0:5],
    price_insights: .price_insights
  }'
```

## Filter Options

Add these query parameters to narrow results:

```
&stops=1          # Nonstop only (0=any, 1=nonstop, 2=≤1 stop, 3=≤2 stops)
&travel_class=1   # 1=Economy, 2=Premium Economy, 3=Business, 4=First
&sort_by=2        # 1=Best, 2=Price, 3=Departure, 4=Arrival, 5=Duration
&max_price=15000  # Maximum price in currency units
&adults=2         # Number of adult passengers (default 1)
&bags=1           # Number of carry-on bags
&include_airlines=BR,CI   # Only show specific airlines (IATA codes)
&exclude_airlines=MM,IT   # Exclude specific airlines
```

## Common Airport Codes

| Code | Airport |
|------|---------|
| `TPE` | 台北桃園 |
| `TSA` | 台北松山 |
| `KHH` | 高雄 |
| `RMQ` | 台中 |
| `NRT` | 東京成田 |
| `HND` | 東京羽田 |
| `KIX` | 大阪關西 |
| `ICN` | 首爾仁川 |
| `HKG` | 香港 |
| `SIN` | 新加坡 |
| `BKK` | 曼谷 |
| `CTS` | 札幌新千歲 |
| `OKA` | 沖繩那霸 |
| `FUK` | 福岡 |
| `PVG` | 上海浦東 |
| `LAX` | 洛杉磯 |
| `SFO` | 舊金山 |

## Common Airline Codes

| Code | Airline |
|------|---------|
| `BR` | 長榮航空 EVA Air |
| `CI` | 中華航空 China Airlines |
| `IT` | 台灣虎航 Tigerair Taiwan |
| `MM` | 樂桃航空 Peach |
| `JL` | 日本航空 JAL |
| `NH` | 全日空 ANA |
| `CX` | 國泰航空 Cathay Pacific |
| `SQ` | 新加坡航空 Singapore Airlines |
| `OZ` | 韓亞航空 Asiana |
| `KE` | 大韓航空 Korean Air |
| `TG` | 泰國航空 Thai Airways |
| `TR` | 酷航 Scoot |

## Price Insights

The API returns price intelligence when available:

```json
{
  "lowest_price": 4500,
  "price_level": "low",
  "typical_price_range": [5000, 8000],
  "price_history": [[timestamp, price], ...]
}
```

- `price_level`: "low", "typical", or "high" relative to historical data
- `typical_price_range`: [min, max] of usual prices for this route


## Tips

- Use `currency=TWD` and `gl=tw` for Taiwan pricing
- Round-trip is `type=1`, one-way is `type=2`
- Compare prices by adding `&sort_by=2` (sort by price)
- Free tier: 250 searches/month (cached searches don't count)
- For multi-city trips, use `type=3` with `multi_city_json` parameter
- Combine with `exchange-rate` skill for currency conversion
- Combine with `weather` skill to check destination weather
