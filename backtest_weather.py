#!/usr/bin/env python3
"""
Polymarket Weather Backtest
Tests if Open-Meteo historical data can beat Polymarket pricing on weather markets.
Uses CLOB price history to get pre-resolution market prices.
"""

import requests
import re
import time
import json
import pandas as pd
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
BET_SIZE = 100
EDGE_THRESHOLD = 0.05
MAX_EVENTS = 50

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

geocode_cache = {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def parse_event_slug(slug):
    m = re.match(r"highest-temperature-in-(.+)-on-(\w+)-(\d+)-(\d{4})", slug)
    if not m:
        return None
    city = m.group(1).replace("-", " ").title()
    month = MONTH_MAP.get(m.group(2).lower())
    if not month:
        return None
    try:
        dt = datetime(int(m.group(4)), month, int(m.group(3)))
        return city, dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_market_temp(question):
    """Extract target temp and qualifier from market question."""
    # Celsius exact: "be 21°C on"
    m = re.search(r"be (\d+)°C\s+on", question)
    if m:
        return int(m.group(1)), "exact_c"
    # Celsius or below
    m = re.search(r"be (\d+)°C\s+or below", question)
    if m:
        return int(m.group(1)), "lte_c"
    # Celsius or higher
    m = re.search(r"be (\d+)°C\s+or higher", question)
    if m:
        return int(m.group(1)), "gte_c"
    # Fahrenheit range: "be 68-69°F"
    m = re.search(r"be (\d+)-(\d+)°F", question)
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2, "range_f"
    # Fahrenheit or higher/below
    m = re.search(r"be (\d+)°F\s*(or higher|or below)", question)
    if m:
        q = "gte_f" if "higher" in m.group(2) else "lte_f"
        return int(m.group(1)), q
    return None, None


def geocode(city):
    if city in geocode_cache:
        return geocode_cache[city]
    r = requests.get(METEO_GEOCODE, params={"name": city, "count": 1}, timeout=10)
    res = r.json().get("results", [])
    val = (res[0]["latitude"], res[0]["longitude"]) if res else None
    geocode_cache[city] = val
    return val


def get_real_temp(city, date_str):
    coords = geocode(city)
    if not coords:
        return None
    r = requests.get(METEO_ARCHIVE, params={
        "latitude": coords[0], "longitude": coords[1],
        "start_date": date_str, "end_date": date_str,
        "daily": "temperature_2m_max", "timezone": "auto",
    }, timeout=15)
    temps = r.json().get("daily", {}).get("temperature_2m_max", [])
    return round(temps[0], 1) if temps and temps[0] is not None else None


def get_price_before_resolution(token_id):
    """Get the market price ~24h before resolution from CLOB price history."""
    try:
        r = requests.get(f"{CLOB_API}/prices-history",
                         params={"market": token_id, "interval": "all", "fidelity": 60},
                         timeout=15)
        data = r.json()
        history = data.get("history", [])
        if not history:
            return None
        # Take the price at roughly 2/3 through the market's life
        # (before resolution spike, but after meaningful trading)
        # Filter out terminal prices (0 or 1)
        mid_prices = [h for h in history if 0.01 < float(h["p"]) < 0.99]
        if mid_prices:
            # Take the price about 75% through the trading period
            idx = int(len(mid_prices) * 0.75)
            return float(mid_prices[idx]["p"])
        # If no mid-range prices, take the earliest non-zero
        for h in history:
            p = float(h["p"])
            if 0.01 < p < 0.99:
                return p
        return None
    except Exception:
        return None


def resolved_yes(real_temp, target, qualifier):
    """Did the market resolve YES?"""
    if qualifier == "exact_c":
        return abs(real_temp - target) < 0.5
    elif qualifier == "lte_c":
        return real_temp <= target + 0.5
    elif qualifier == "gte_c":
        return real_temp >= target - 0.5
    elif qualifier == "range_f":
        real_f = real_temp * 9 / 5 + 32
        return abs(real_f - target) < 1.5
    elif qualifier == "gte_f":
        real_f = real_temp * 9 / 5 + 32
        return real_f >= target - 0.5
    elif qualifier == "lte_f":
        real_f = real_temp * 9 / 5 + 32
        return real_f <= target + 0.5
    return False


def our_probability(real_temp, target, qualifier):
    """Our estimated probability based on Open-Meteo data."""
    if qualifier == "exact_c":
        diff = abs(real_temp - target)
        if diff < 0.5:
            return 0.90
        elif diff < 1.5:
            return 0.10
        return 0.02
    elif qualifier == "lte_c":
        margin = target - real_temp
        if margin > 1:
            return 0.95
        elif margin > -0.5:
            return 0.60
        return 0.05
    elif qualifier == "gte_c":
        margin = real_temp - target
        if margin > 1:
            return 0.95
        elif margin > -0.5:
            return 0.60
        return 0.05
    elif qualifier in ("range_f", "gte_f", "lte_f"):
        real_f = real_temp * 9 / 5 + 32
        if qualifier == "gte_f":
            margin = real_f - target
            if margin > 2:
                return 0.95
            elif margin > -1:
                return 0.55
            return 0.05
        elif qualifier == "lte_f":
            margin = target - real_f
            if margin > 2:
                return 0.95
            elif margin > -1:
                return 0.55
            return 0.05
        else:
            diff = abs(real_f - target)
            if diff < 1:
                return 0.85
            elif diff < 2.5:
                return 0.15
            return 0.03
    return 0.50


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 70)
    print("🌡️  POLYMARKET WEATHER BACKTEST — Open-Meteo vs Market Pricing")
    print("=" * 70)

    # Step 1: Collect event slugs from known weather trader
    print("\n📥 Collecting weather events...")
    all_events = set()
    for offset in range(0, 2000, 500):
        r = requests.get("https://data-api.polymarket.com/positions",
                         params={"user": "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11",
                                 "sizeThreshold": -1, "limit": 500, "offset": offset}, timeout=30)
        data = r.json()
        if not data:
            break
        for p in data:
            es = p.get("eventSlug", "")
            if "temperature" in es:
                all_events.add(es)
        time.sleep(0.3)

    # Filter past events
    today = datetime.now().strftime("%Y-%m-%d")
    past = []
    for slug in all_events:
        parsed = parse_event_slug(slug)
        if parsed and parsed[1] < today:
            past.append((slug, parsed[0], parsed[1]))
    past.sort(key=lambda x: x[2], reverse=True)
    past = past[:MAX_EVENTS]

    print(f"   Total weather events: {len(all_events)}")
    print(f"   Past resolved: {len(past)} (analyzing top {MAX_EVENTS})")

    # Step 2-4: For each event, get real temp + market prices + compute edge
    results = []
    api_calls = 0

    for i, (slug, city, date_str) in enumerate(past):
        print(f"\n  [{i+1}/{len(past)}] {city} — {date_str}")

        real_temp = get_real_temp(city, date_str)
        if real_temp is None:
            print(f"    ❌ no weather data")
            continue
        print(f"    🌡️  Real: {real_temp}°C")

        event = None
        try:
            r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=15)
            data = r.json()
            event = data[0] if data else None
        except Exception:
            pass
        if not event or not event.get("markets"):
            print(f"    ❌ no event data")
            continue

        markets_analyzed = 0
        for market in event["markets"]:
            question = market.get("question", "")
            target, qualifier = parse_market_temp(question)
            if target is None:
                continue

            tokens_raw = market.get("clobTokenIds", [])
            if isinstance(tokens_raw, str):
                try:
                    tokens = json.loads(tokens_raw)
                except (json.JSONDecodeError, TypeError):
                    tokens = []
            else:
                tokens = tokens_raw
            if not tokens:
                continue

            # Get historical price (YES token = index 0)
            mkt_price = get_price_before_resolution(tokens[0])
            api_calls += 1
            if mkt_price is None:
                continue

            # Skip if market had no real trading
            volume = market.get("volume", 0)
            if volume and float(volume) < 100:
                continue

            our_p = our_probability(real_temp, target, qualifier)
            edge = our_p - mkt_price
            res_yes = resolved_yes(real_temp, target, qualifier)

            if edge > EDGE_THRESHOLD:
                signal = "BUY YES"
                pnl = BET_SIZE * (1 - mkt_price) if res_yes else -BET_SIZE * mkt_price
                correct = res_yes
            elif edge < -EDGE_THRESHOLD:
                signal = "BUY NO"
                mkt_no = 1 - mkt_price
                pnl = BET_SIZE * (1 - mkt_no) if not res_yes else -BET_SIZE * mkt_no
                correct = not res_yes
            else:
                signal = "SKIP"
                pnl = 0
                correct = None

            unit = "°F" if "_f" in qualifier else "°C"
            results.append({
                "City": city,
                "Date": date_str,
                "Target": f"{target}{unit}",
                "Type": qualifier,
                "Real": f"{real_temp}°C",
                "Mkt": round(mkt_price, 3),
                "Ours": round(our_p, 2),
                "Edge": round(edge, 3),
                "Signal": signal,
                "Result": "YES" if res_yes else "NO",
                "Correct": correct,
                "P&L": round(pnl, 2),
            })
            markets_analyzed += 1

        print(f"    📊 {markets_analyzed} markets analyzed")
        time.sleep(0.2)  # rate limit CLOB API

    # ─────────────────────────────────────────
    # STEP 5: Report
    # ─────────────────────────────────────────

    print("\n" + "=" * 70)
    print("📊 BACKTEST RESULTS")
    print("=" * 70)

    df = pd.DataFrame(results)
    if df.empty:
        print("\n❌ No results")
        return

    trades = df[df["Signal"] != "SKIP"].copy()
    all_count = len(df)
    skip_count = len(df[df["Signal"] == "SKIP"])

    print(f"\n  Markets analyzed: {all_count}")
    print(f"  Skipped (no edge): {skip_count}")

    if trades.empty:
        print("  ❌ No trades triggered")
        return

    print(f"\n{'═' * 70}")
    print("TRADES")
    print(f"{'═' * 70}\n")
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 15)
    print(trades[["City", "Date", "Target", "Real", "Mkt", "Ours", "Edge", "Signal", "Result", "P&L"]].to_string(index=False))

    wins = len(trades[trades["Correct"] == True])
    losses = len(trades[trades["Correct"] == False])
    total_trades = len(trades)
    win_rate = wins / total_trades * 100
    total_pnl = trades["P&L"].sum()
    avg_pnl = trades["P&L"].mean()
    best = trades["P&L"].max()
    worst = trades["P&L"].min()
    roi = total_pnl / (total_trades * BET_SIZE) * 100

    yes_trades = trades[trades["Signal"] == "BUY YES"]
    no_trades = trades[trades["Signal"] == "BUY NO"]

    print(f"\n{'═' * 70}")
    print("📈 SUMMARY")
    print(f"{'═' * 70}\n")
    print(f"  Total trades         : {total_trades}")
    print(f"  Wins / Losses        : {wins} / {losses}")
    print(f"  Win rate             : {win_rate:.1f}%")
    print(f"  Total P&L            : ${total_pnl:+,.2f}")
    print(f"  Avg P&L per trade    : ${avg_pnl:+,.2f}")
    print(f"  Best trade           : ${best:+,.2f}")
    print(f"  Worst trade          : ${worst:+,.2f}")
    print(f"  ROI per trade        : {roi:+.1f}%")
    print(f"  Bet size             : ${BET_SIZE}")
    if len(yes_trades):
        print(f"\n  BUY YES trades       : {len(yes_trades)} (win rate {len(yes_trades[yes_trades['Correct']==True])/len(yes_trades)*100:.0f}%)")
    if len(no_trades):
        print(f"  BUY NO trades        : {len(no_trades)} (win rate {len(no_trades[no_trades['Correct']==True])/len(no_trades)*100:.0f}%)")

    print(f"\n  CLOB API calls       : {api_calls}")

    df.to_csv("backtest_results.csv", index=False)
    print(f"\n💾 Results saved to backtest_results.csv")


if __name__ == "__main__":
    main()
