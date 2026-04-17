#!/usr/bin/env python3
"""
Polymarket Weather — Live Paper Trading Monitor
Scans active weather markets every 10 min, compares with Open-Meteo forecasts,
logs paper trades, tracks resolved markets, and reports P&L.
"""

import requests
import re
import json
import csv
import time
import os
import sys
import base64
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
DATA_API = "https://data-api.polymarket.com"

SCAN_INTERVAL = 600          # 10 minutes
EDGE_THRESHOLD = 0.05
STARTING_BANKROLL = 1000.0
KELLY_FRACTION = 0.25
CSV_FILE = Path("paper_trades.csv")
STATE_FILE = Path("monitor_state.json")

# Known weather trader to discover events
WEATHER_TRADER = "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11"

# GitHub sync
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "hectorm17/polymarket-tracker"

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

geocode_cache = {}

# ─────────────────────────────────────────────
# STATE PERSISTENCE
# ─────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "bankroll": STARTING_BANKROLL,
        "trades": [],          # list of trade dicts
        "seen_markets": [],    # market IDs we already traded
        "start_time": datetime.now().isoformat(),
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_csv(row):
    exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "market_title", "city", "date", "target_temp",
            "forecast_temp", "market_prob", "our_prob", "edge", "signal",
            "stake", "entry_price", "market_url", "status", "result", "pnl",
        ])
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def push_to_github(filename):
    """Push a local file to GitHub so Streamlit Cloud can read it."""
    if not GITHUB_TOKEN:
        return
    try:
        with open(filename, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        r = requests.get(api_url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None
        payload = {
            "message": f"auto: update {filename} {datetime.now().strftime('%H:%M')}",
            "content": content,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            print(f"    📤 {filename} pushed to GitHub")
        else:
            print(f"    ⚠️  GitHub push failed for {filename}: {r.status_code}")
    except Exception as e:
        print(f"    ⚠️  GitHub push error: {e}")


def sync_to_github():
    """Push both data files to GitHub."""
    push_to_github(str(CSV_FILE))
    push_to_github(str(STATE_FILE))



# ─────────────────────────────────────────────
# PARSING
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
    m = re.search(r"be (\d+)°C\s+on", question)
    if m: return int(m.group(1)), "exact_c"
    m = re.search(r"be (\d+)°C\s+or below", question)
    if m: return int(m.group(1)), "lte_c"
    m = re.search(r"be (\d+)°C\s+or higher", question)
    if m: return int(m.group(1)), "gte_c"
    m = re.search(r"be (\d+)-(\d+)°F", question)
    if m: return (int(m.group(1)) + int(m.group(2))) / 2, "range_f"
    m = re.search(r"be (\d+)°F\s*(or higher|or below)", question)
    if m: return int(m.group(1)), ("gte_f" if "higher" in m.group(2) else "lte_f")
    return None, None


# ─────────────────────────────────────────────
# DATA FETCHERS
# ─────────────────────────────────────────────

def geocode(city):
    if city in geocode_cache:
        return geocode_cache[city]
    try:
        r = requests.get(METEO_GEOCODE, params={"name": city, "count": 1}, timeout=10)
        res = r.json().get("results", [])
        val = (res[0]["latitude"], res[0]["longitude"]) if res else None
    except Exception:
        val = None
    geocode_cache[city] = val
    return val


def get_forecast(city, target_date):
    """Get forecasted max temp for a city on a future date."""
    coords = geocode(city)
    if not coords:
        return None
    try:
        r = requests.get(METEO_FORECAST, params={
            "latitude": coords[0], "longitude": coords[1],
            "daily": "temperature_2m_max", "timezone": "auto",
            "forecast_days": 16,
        }, timeout=15)
        data = r.json()
        dates = data.get("daily", {}).get("time", [])
        temps = data.get("daily", {}).get("temperature_2m_max", [])
        for d, t in zip(dates, temps):
            if d == target_date and t is not None:
                return round(t, 1)
    except Exception:
        pass
    return None


def get_real_temp(city, date_str):
    """Get actual temperature for a resolved market."""
    coords = geocode(city)
    if not coords:
        return None
    try:
        r = requests.get(METEO_ARCHIVE, params={
            "latitude": coords[0], "longitude": coords[1],
            "start_date": date_str, "end_date": date_str,
            "daily": "temperature_2m_max", "timezone": "auto",
        }, timeout=15)
        temps = r.json().get("daily", {}).get("temperature_2m_max", [])
        return round(temps[0], 1) if temps and temps[0] is not None else None
    except Exception:
        return None


def discover_weather_events():
    """Find active weather event slugs from a known trader's positions."""
    slugs = set()
    for offset in range(0, 2000, 500):
        try:
            r = requests.get(f"{DATA_API}/positions", params={
                "user": WEATHER_TRADER, "sizeThreshold": -1,
                "limit": 500, "offset": offset,
            }, timeout=30)
            data = r.json()
            if not data:
                break
            for p in data:
                es = p.get("eventSlug", "")
                if "temperature" in es:
                    slugs.add(es)
        except Exception:
            break
        time.sleep(0.3)
    return slugs


def fetch_event(slug):
    try:
        r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=15)
        data = r.json()
        return data[0] if data else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# PROBABILITY
# ─────────────────────────────────────────────

def our_probability(forecast, target, qualifier):
    if qualifier == "exact_c":
        diff = abs(forecast - target)
        if diff < 0.5: return 0.90
        elif diff < 1.5: return 0.10
        return 0.02
    elif qualifier == "lte_c":
        margin = target - forecast
        if margin > 1: return 0.95
        elif margin > -0.5: return 0.60
        return 0.05
    elif qualifier == "gte_c":
        margin = forecast - target
        if margin > 1: return 0.95
        elif margin > -0.5: return 0.60
        return 0.05
    elif qualifier in ("range_f", "gte_f", "lte_f"):
        fc_f = forecast * 9 / 5 + 32
        if qualifier == "gte_f":
            m = fc_f - target
            if m > 2: return 0.95
            elif m > -1: return 0.55
            return 0.05
        elif qualifier == "lte_f":
            m = target - fc_f
            if m > 2: return 0.95
            elif m > -1: return 0.55
            return 0.05
        else:
            diff = abs(fc_f - target)
            if diff < 1: return 0.85
            elif diff < 2.5: return 0.15
            return 0.03
    return 0.50


def resolved_yes(real_temp, target, qualifier):
    if qualifier == "exact_c": return abs(real_temp - target) < 0.5
    elif qualifier == "lte_c": return real_temp <= target + 0.5
    elif qualifier == "gte_c": return real_temp >= target - 0.5
    elif qualifier in ("range_f", "gte_f", "lte_f"):
        rf = real_temp * 9 / 5 + 32
        if qualifier == "gte_f": return rf >= target - 0.5
        if qualifier == "lte_f": return rf <= target + 0.5
        return abs(rf - target) < 1.5
    return False


def kelly_bet(edge, price, bankroll):
    if price <= 0 or price >= 1:
        return 0
    odds = (1 / price) - 1
    if odds <= 0:
        return 0
    f = KELLY_FRACTION * (edge / odds)
    bet = f * bankroll
    return max(0, min(bet, bankroll * 0.25))


# ─────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────

def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")


def print_dashboard(state, new_signals):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bankroll = state["bankroll"]
    trades = state["trades"]
    roi = (bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100

    # Today's trades
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("timestamp", "").startswith(today_str)]
    today_resolved = [t for t in today_trades if t.get("status") == "resolved"]
    today_wins = len([t for t in today_resolved if t.get("pnl", 0) > 0])
    today_wr = (today_wins / len(today_resolved) * 100) if today_resolved else 0

    # All-time stats
    all_resolved = [t for t in trades if t.get("status") == "resolved"]
    all_wins = len([t for t in all_resolved if t.get("pnl", 0) > 0])
    all_wr = (all_wins / len(all_resolved) * 100) if all_resolved else 0
    all_pnl = sum(t.get("pnl", 0) for t in all_resolved)
    pending = [t for t in trades if t.get("status") == "pending"]

    clear_screen()
    print("=" * 65)
    print(f"  🌡️  PAPER TRADING LIVE — {now}")
    print("=" * 65)
    print(f"  Bankroll    : ${bankroll:,.2f} ({roi:+.1f}%)")
    print(f"  All-time    : {len(all_resolved)} resolved | {all_wins}W / {len(all_resolved)-all_wins}L | WR {all_wr:.1f}% | P&L ${all_pnl:+,.2f}")
    print(f"  Today       : {len(today_trades)} trades | {len(today_resolved)} resolved | WR {today_wr:.0f}%")
    print(f"  Pending     : {len(pending)} trades awaiting resolution")
    print(f"  Running     : {(datetime.now() - datetime.fromisoformat(state['start_time'])).days}d {(datetime.now() - datetime.fromisoformat(state['start_time'])).seconds//3600}h")
    print("─" * 65)

    if new_signals:
        for sig in new_signals:
            emoji = "🟢" if sig["signal"] == "BUY YES" else "🔴"
            print(f"\n  {emoji} NEW SIGNAL → {sig['signal']}")
            print(f"  Market   : {sig['title'][:60]}")
            print(f"  Forecast : {sig['forecast']}°C | Target: {sig['target']} | Mkt: {sig['mkt_price']:.1%}")
            print(f"  Edge     : {sig['edge']:+.3f} | Stake: ${sig['stake']:.2f}")
            print(f"  URL      : https://polymarket.com/event/{sig['event_slug']}")
    else:
        print("\n  No new signals this scan.")

    # Show last 5 resolved trades
    if all_resolved:
        print(f"\n{'─' * 65}")
        print("  LAST RESOLVED TRADES:")
        for t in all_resolved[-5:]:
            pnl = t.get("pnl", 0)
            icon = "✅" if pnl > 0 else "❌"
            print(f"    {icon} {t['city']:15s} {t['date']} {t['signal']:8s} → ${pnl:+.2f}")

    # Show pending trades
    if pending:
        print(f"\n{'─' * 65}")
        print(f"  PENDING ({len(pending)}):")
        for t in pending[-8:]:
            print(f"    ⏳ {t['city']:15s} {t['date']} {t['signal']:8s} edge={t['edge']:+.3f} stake=${t['stake']:.0f}")

    print(f"\n{'─' * 65}")
    print(f"  Next scan in {SCAN_INTERVAL // 60} min. Ctrl+C to stop.")
    print(f"  Logs: {CSV_FILE} | State: {STATE_FILE}")


# ─────────────────────────────────────────────
# CORE LOOP
# ─────────────────────────────────────────────

def resolve_pending(state):
    """Check if any pending trades have resolved."""
    today = datetime.now().strftime("%Y-%m-%d")
    updated = False

    for trade in state["trades"]:
        if trade.get("status") != "pending":
            continue
        # Only resolve if the market date is in the past
        if trade["date"] >= today:
            continue

        real_temp = get_real_temp(trade["city"], trade["date"])
        if real_temp is None:
            continue

        target = trade["target_num"]
        qualifier = trade["qualifier"]
        res = resolved_yes(real_temp, target, qualifier)

        signal = trade["signal"]
        stake = trade["stake"]
        entry = trade["entry_price"]

        if signal == "BUY YES":
            pnl = stake * (1 - entry) if res else -stake * entry
            correct = res
        else:  # BUY NO
            entry_no = 1 - entry
            pnl = stake * (1 - entry_no) if not res else -stake * entry_no
            correct = not res

        trade["status"] = "resolved"
        trade["result"] = "YES" if res else "NO"
        trade["real_temp"] = real_temp
        trade["pnl"] = round(pnl, 2)
        trade["correct"] = correct
        state["bankroll"] += pnl

        # Update CSV
        append_csv({
            "timestamp": trade["timestamp"],
            "market_title": trade["title"],
            "city": trade["city"],
            "date": trade["date"],
            "target_temp": trade["target"],
            "forecast_temp": trade["forecast"],
            "market_prob": trade["mkt_price"],
            "our_prob": trade["our_prob"],
            "edge": trade["edge"],
            "signal": trade["signal"],
            "stake": trade["stake"],
            "entry_price": trade["entry_price"],
            "market_url": trade.get("url", ""),
            "status": "resolved",
            "result": trade["result"],
            "pnl": trade["pnl"],
        })
        updated = True
        print(f"  ✅ RESOLVED: {trade['city']} {trade['date']} → {trade['result']} | P&L ${pnl:+.2f}")

    if updated:
        save_state(state)
    return state


def scan_markets(state):
    """Scan active weather markets for signals."""
    # Discover events
    all_slugs = discover_weather_events()
    today = datetime.now().strftime("%Y-%m-%d")

    # Filter future events
    future_events = []
    for slug in all_slugs:
        parsed = parse_event_slug(slug)
        if parsed and parsed[1] >= today:
            future_events.append((slug, parsed[0], parsed[1]))
    future_events.sort(key=lambda x: x[2])

    new_signals = []
    seen = set(state["seen_markets"])

    for slug, city, date_str in future_events:
        # Get forecast
        forecast = get_forecast(city, date_str)
        if forecast is None:
            continue

        # Fetch event markets
        event = fetch_event(slug)
        if not event or not event.get("markets"):
            continue

        for market in event["markets"]:
            question = market.get("question", "")
            target, qualifier = parse_market_temp(question)
            if target is None:
                continue

            # Skip already traded
            market_id = market.get("conditionId", "") + "_" + str(target)
            if market_id in seen:
                continue

            # Get YES price
            prices_raw = market.get("outcomePrices", "")
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw)
                except Exception:
                    continue
            else:
                prices = prices_raw
            if not prices:
                continue

            try:
                mkt_yes = float(prices[0])
            except (ValueError, IndexError):
                continue

            # Skip terminal prices
            if mkt_yes <= 0.01 or mkt_yes >= 0.99:
                continue

            # Calculate edge
            our_p = our_probability(forecast, target, qualifier)
            edge = our_p - mkt_yes

            if edge > EDGE_THRESHOLD:
                signal = "BUY YES"
                entry = min(mkt_yes + 0.02, 0.99)  # slippage
            elif edge < -EDGE_THRESHOLD:
                signal = "BUY NO"
                entry = max(mkt_yes - 0.02, 0.01)
            else:
                continue

            # Kelly sizing
            if signal == "BUY YES":
                stake = kelly_bet(abs(edge), entry, state["bankroll"])
            else:
                stake = kelly_bet(abs(edge), 1 - entry, state["bankroll"])

            if stake < 1:
                continue

            unit = "°F" if "_f" in qualifier else "°C"
            trade = {
                "timestamp": datetime.now().isoformat(),
                "title": question,
                "city": city,
                "date": date_str,
                "target": f"{target}{unit}",
                "target_num": target,
                "qualifier": qualifier,
                "forecast": forecast,
                "mkt_price": round(mkt_yes, 4),
                "our_prob": round(our_p, 2),
                "edge": round(edge, 3),
                "signal": signal,
                "entry_price": round(entry, 4),
                "stake": round(stake, 2),
                "event_slug": slug,
                "url": f"https://polymarket.com/event/{slug}",
                "status": "pending",
                "result": None,
                "pnl": 0,
            }

            state["trades"].append(trade)
            state["seen_markets"].append(market_id)
            new_signals.append({
                "signal": signal,
                "title": question,
                "forecast": forecast,
                "target": f"{target}{unit}",
                "mkt_price": mkt_yes,
                "edge": edge,
                "stake": stake,
                "event_slug": slug,
            })

            # Log to CSV
            append_csv({
                "timestamp": trade["timestamp"],
                "market_title": question,
                "city": city,
                "date": date_str,
                "target_temp": trade["target"],
                "forecast_temp": forecast,
                "market_prob": mkt_yes,
                "our_prob": our_p,
                "edge": edge,
                "signal": signal,
                "stake": stake,
                "entry_price": entry,
                "market_url": trade["url"],
                "status": "pending",
                "result": "",
                "pnl": 0,
            })

        time.sleep(0.3)

    save_state(state)
    return new_signals


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    state = load_state()
    print("🌡️  Starting Polymarket Weather Paper Trading Monitor...")
    print(f"   Bankroll: ${state['bankroll']:,.2f}")
    print(f"   Existing trades: {len(state['trades'])}")
    print(f"   Scan interval: {SCAN_INTERVAL // 60} min\n")

    cycle = 0
    while True:
        try:
            cycle += 1

            # 1. Resolve pending trades
            state = resolve_pending(state)

            # 2. Scan for new signals
            new_signals = scan_markets(state)

            # 3. Display dashboard
            print_dashboard(state, new_signals)

            # 4. Sync to GitHub
            sync_to_github()

            # 5. Wait
            for remaining in range(SCAN_INTERVAL, 0, -1):
                mins, secs = divmod(remaining, 60)
                sys.stdout.write(f"\r  Next scan in {mins:02d}:{secs:02d}  ")
                sys.stdout.flush()
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n🛑 Stopping monitor...")
            save_state(state)

            # Final stats
            resolved = [t for t in state["trades"] if t.get("status") == "resolved"]
            wins = len([t for t in resolved if t.get("pnl", 0) > 0])
            total_pnl = sum(t.get("pnl", 0) for t in resolved)
            wr = (wins / len(resolved) * 100) if resolved else 0

            print(f"\n  Final bankroll: ${state['bankroll']:,.2f}")
            print(f"  Trades: {len(state['trades'])} ({len(resolved)} resolved)")
            print(f"  Win rate: {wr:.1f}%")
            print(f"  P&L: ${total_pnl:+,.2f}")
            if wr > 75 and len(resolved) >= 20:
                print(f"\n  🟢 Win rate > 75% with 20+ trades → READY FOR LIVE TRADING")
            elif len(resolved) < 20:
                print(f"\n  ⏳ Need more resolved trades ({len(resolved)}/20 minimum)")
            else:
                print(f"\n  🔴 Win rate below 75% → keep paper trading")
            print(f"\n  State saved to {STATE_FILE}")
            print(f"  Trades logged in {CSV_FILE}")
            break

        except Exception as e:
            print(f"\n  ⚠️  Error: {e}")
            save_state(state)
            time.sleep(30)


if __name__ == "__main__":
    main()
