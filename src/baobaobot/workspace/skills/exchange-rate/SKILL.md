---
name: exchange-rate
description: "Get real-time currency exchange rates and convert amounts between currencies using free API. Use when: user asks about exchange rates, currency conversion, travel budget calculation, or wants to know how much something costs in another currency. No API key required."
---

# Exchange Rate Skill

Get real-time exchange rates and convert currencies via open.er-api.com (free, no API key required).

## Setup

No setup required — the API is completely free and needs no API key.

## Get Exchange Rates from a Base Currency

```bash
curl -s "https://open.er-api.com/v6/latest/TWD" \
  | jq -r '"💱 Base: \(.base_code) (Updated: \(.time_last_update_utc | split(" ") | .[0:4] | join(" ")))\n" + ([.rates | to_entries[] | select(.key == "USD" or .key == "JPY" or .key == "EUR" or .key == "CNY" or .key == "KRW" or .key == "THB" or .key == "GBP" or .key == "HKD" or .key == "SGD" or .key == "AUD") | "  1 TWD = \(.value) \(.key)"] | join("\n"))'
```

## Convert a Specific Amount

```bash
# Convert 10000 JPY to TWD
FROM="JPY"
TO="TWD"
AMOUNT=10000

RATE=$(curl -s "https://open.er-api.com/v6/latest/$FROM" | jq -r ".rates.$TO")
RESULT=$(python3 -c "print(f'{$AMOUNT * $RATE:,.2f}')")
echo "💱 $AMOUNT $FROM = $RESULT $TO"
```

## Common Travel Currency Conversions

### From TWD to other currencies

```bash
AMOUNT=1000
curl -s "https://open.er-api.com/v6/latest/TWD" \
  | jq -r --argjson amt $AMOUNT '[.rates | to_entries[] | select(.key == "USD" or .key == "JPY" or .key == "EUR" or .key == "CNY" or .key == "KRW" or .key == "THB" or .key == "VND" or .key == "HKD" or .key == "SGD") | "  \($amt) TWD = \($amt * .value | . * 100 | round / 100) \(.key)"] | join("\n")'
```

### From other currencies to TWD

```bash
# How much is 1000 JPY in TWD?
FROM="JPY"
AMOUNT=1000

RATE=$(curl -s "https://open.er-api.com/v6/latest/$FROM" | jq -r '.rates.TWD')
RESULT=$(python3 -c "print(f'{$AMOUNT * $RATE:,.2f}')")
echo "💱 $AMOUNT $FROM = $RESULT TWD"
```

## Compare Multiple Currencies

Useful for travel planning:

```bash
curl -s "https://open.er-api.com/v6/latest/TWD" \
  | jq -r '"💱 TWD Exchange Rates\n" + ([.rates | to_entries[] | select(.key == "JPY" or .key == "KRW" or .key == "THB" or .key == "VND" or .key == "USD" or .key == "EUR") | "  1 TWD = \(.value) \(.key)"] | sort | join("\n"))'
```

## Travel Budget Calculator

```bash
# Calculate how much TWD you need for a trip
DEST_CURRENCY="JPY"
BUDGET=50000  # Budget in destination currency

RATE=$(curl -s "https://open.er-api.com/v6/latest/$DEST_CURRENCY" | jq -r '.rates.TWD')
RESULT=$(python3 -c "print(f'{$BUDGET * $RATE:,.0f}')")
echo "💱 旅遊預算: $BUDGET $DEST_CURRENCY ≈ $RESULT TWD"
```

## Supported Currencies (common)

| Code | Currency |
|------|----------|
| `TWD` | 新台幣 |
| `USD` | 美元 |
| `JPY` | 日圓 |
| `EUR` | 歐元 |
| `GBP` | 英鎊 |
| `CNY` | 人民幣 |
| `KRW` | 韓元 |
| `HKD` | 港幣 |
| `SGD` | 新加坡幣 |
| `THB` | 泰銖 |
| `VND` | 越南盾 |
| `MYR` | 馬來西亞令吉 |
| `PHP` | 菲律賓披索 |
| `AUD` | 澳幣 |
| `CAD` | 加幣 |
| `CHF` | 瑞士法郎 |

Full list: 150+ currencies supported.

## Notes

- Free API — no API key, no registration, no cost
- Rates updated daily (sourced from central bank data)
- API endpoint: `https://open.er-api.com/v6/latest/{BASE_CURRENCY}`
- Returns all 150+ currency rates in a single call
- For historical rates, use `https://open.er-api.com/v6/historical/{YYYY-MM-DD}/{BASE}`
- Rates are mid-market rates — actual bank/exchange rates may differ slightly
- No rate limiting for reasonable personal use
