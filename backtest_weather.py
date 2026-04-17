#!/usr/bin/env python3
"""
Polymarket Weather Backtest v2
Open-Meteo vs Polymarket pricing with liquidity, slippage & Kelly sizing analysis.
"""

import requests
import re
import time
import json
import pandas as pd
import numpy as np
from datetime import datetime

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
SLIPPAGE_CENTS = 0.02        # 2¢ spread/slippage added to entry price
MIN_LIQUIDITY_USD = 50       # minimum volume to consider liquid
STARTING_BANKROLL = 1000

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
geocode_cache = {}

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
    try:
        r = requests.get(f"{CLOB_API}/prices-history",
                         params={"market": token_id, "interval": "all", "fidelity": 60}, timeout=15)
        history = r.json().get("history", [])
        if not history:
            return None
        mid = [h for h in history if 0.01 < float(h["p"]) < 0.99]
        if mid:
            idx = int(len(mid) * 0.75)
            return float(mid[idx]["p"])
        for h in history:
            p = float(h["p"])
            if 0.01 < p < 0.99:
                return p
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────
# PROBABILITY & RESOLUTION
# ─────────────────────────────────────────────

def resolved_yes(real_temp, target, qualifier):
    if qualifier == "exact_c":
        return abs(real_temp - target) < 0.5
    elif qualifier == "lte_c":
        return real_temp <= target + 0.5
    elif qualifier == "gte_c":
        return real_temp >= target - 0.5
    elif qualifier in ("range_f", "gte_f", "lte_f"):
        real_f = real_temp * 9 / 5 + 32
        if qualifier == "gte_f": return real_f >= target - 0.5
        if qualifier == "lte_f": return real_f <= target + 0.5
        return abs(real_f - target) < 1.5
    return False


def our_probability(real_temp, target, qualifier):
    if qualifier == "exact_c":
        diff = abs(real_temp - target)
        if diff < 0.5: return 0.90
        elif diff < 1.5: return 0.10
        return 0.02
    elif qualifier == "lte_c":
        margin = target - real_temp
        if margin > 1: return 0.95
        elif margin > -0.5: return 0.60
        return 0.05
    elif qualifier == "gte_c":
        margin = real_temp - target
        if margin > 1: return 0.95
        elif margin > -0.5: return 0.60
        return 0.05
    elif qualifier in ("range_f", "gte_f", "lte_f"):
        real_f = real_temp * 9 / 5 + 32
        if qualifier == "gte_f":
            m = real_f - target
            if m > 2: return 0.95
            elif m > -1: return 0.55
            return 0.05
        elif qualifier == "lte_f":
            m = target - real_f
            if m > 2: return 0.95
            elif m > -1: return 0.55
            return 0.05
        else:
            diff = abs(real_f - target)
            if diff < 1: return 0.85
            elif diff < 2.5: return 0.15
            return 0.03
    return 0.50


# ─────────────────────────────────────────────
# P&L CALCULATION
# ─────────────────────────────────────────────

def calc_pnl(signal, entry_price, res_yes, bet_size):
    """Calculate P&L for a trade."""
    if signal == "BUY YES":
        return bet_size * (1 - entry_price) if res_yes else -bet_size * entry_price
    elif signal == "BUY NO":
        entry_no = 1 - entry_price
        return bet_size * (1 - entry_no) if not res_yes else -bet_size * entry_no
    return 0


def kelly_bet(edge, odds, bankroll, fraction):
    """Kelly criterion bet size. odds = payout ratio (e.g. 1/price - 1)."""
    if odds <= 0:
        return 0
    kelly_f = edge / odds
    bet = fraction * kelly_f * bankroll
    return max(0, min(bet, bankroll * 0.25))  # cap at 25% of bankroll


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 70)
    print("🌡️  POLYMARKET WEATHER BACKTEST v2")
    print("   Open-Meteo vs Market Pricing — Liquidity, Slippage & Kelly")
    print("=" * 70)

    # ── Step 1: Collect events ──
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

    today = datetime.now().strftime("%Y-%m-%d")
    past = []
    for slug in all_events:
        parsed = parse_event_slug(slug)
        if parsed and parsed[1] < today:
            past.append((slug, parsed[0], parsed[1]))
    past.sort(key=lambda x: x[2], reverse=True)
    past = past[:MAX_EVENTS]
    print(f"   Total: {len(all_events)} | Past resolved: {len(past)}")

    # ── Step 2-4: Analyze each event ──
    results = []

    for i, (slug, city, date_str) in enumerate(past):
        print(f"\n  [{i+1}/{len(past)}] {city} — {date_str}", end="", flush=True)

        real_temp = get_real_temp(city, date_str)
        if real_temp is None:
            print(" ❌ no weather")
            continue

        try:
            r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=15)
            event = r.json()[0] if r.json() else None
        except Exception:
            event = None
        if not event or not event.get("markets"):
            print(" ❌ no event")
            continue

        n = 0
        for market in event["markets"]:
            question = market.get("question", "")
            target, qualifier = parse_market_temp(question)
            if target is None:
                continue

            tokens_raw = market.get("clobTokenIds", [])
            if isinstance(tokens_raw, str):
                try:
                    tokens = json.loads(tokens_raw)
                except Exception:
                    continue
            else:
                tokens = tokens_raw
            if not tokens:
                continue

            mkt_price = get_price_before_resolution(tokens[0])
            if mkt_price is None:
                continue

            volume = float(market.get("volume", 0) or 0)
            if volume < 100:
                continue

            our_p = our_probability(real_temp, target, qualifier)
            edge = our_p - mkt_price
            res_yes = resolved_yes(real_temp, target, qualifier)

            # Determine signal
            if edge > EDGE_THRESHOLD:
                signal = "BUY YES"
            elif edge < -EDGE_THRESHOLD:
                signal = "BUY NO"
            else:
                signal = "SKIP"

            # ── Liquidity check ──
            is_liquid = volume >= MIN_LIQUIDITY_USD
            # More conservative: for BUY NO at very low prices, check if volume supports it
            if signal == "BUY NO" and mkt_price > 0.90 and volume < 500:
                is_liquid = False
            if signal == "BUY YES" and mkt_price < 0.10 and volume < 500:
                is_liquid = False

            # ── Brut P&L (no slippage) ──
            pnl_brut = calc_pnl(signal, mkt_price, res_yes, BET_SIZE) if signal != "SKIP" else 0

            # ── Slipped entry price ──
            if signal == "BUY YES":
                slipped_price = min(mkt_price + SLIPPAGE_CENTS, 0.99)
            elif signal == "BUY NO":
                # We're buying NO, so the YES price we see drops, meaning NO price increases
                slipped_price = max(mkt_price - SLIPPAGE_CENTS, 0.01)
            else:
                slipped_price = mkt_price
            pnl_slipped = calc_pnl(signal, slipped_price, res_yes, BET_SIZE) if signal != "SKIP" else 0

            # Correct?
            if signal == "BUY YES":
                correct = res_yes
            elif signal == "BUY NO":
                correct = not res_yes
            else:
                correct = None

            unit = "°F" if "_f" in qualifier else "°C"
            results.append({
                "City": city, "Date": date_str,
                "Target": f"{target}{unit}", "Type": qualifier,
                "Real": f"{real_temp}°C",
                "Mkt": round(mkt_price, 3),
                "Slipped": round(slipped_price, 3),
                "Ours": round(our_p, 2),
                "Edge": round(edge, 3),
                "Signal": signal,
                "Result": "YES" if res_yes else "NO",
                "Correct": correct,
                "Volume": round(volume, 0),
                "Liquid": is_liquid,
                "PnL_Brut": round(pnl_brut, 2),
                "PnL_Slip": round(pnl_slipped, 2),
                "PnL_Liq": round(pnl_slipped if is_liquid else 0, 2),
                "token": tokens[0],
            })
            n += 1

        print(f" ✅ {real_temp}°C ({n} mkts)")
        time.sleep(0.2)

    # ═════════════════════════════════════════════
    # REPORT
    # ═════════════════════════════════════════════

    df = pd.DataFrame(results)
    if df.empty:
        print("\n❌ No results")
        return

    trades = df[df["Signal"] != "SKIP"].copy()
    if trades.empty:
        print("\n❌ No trades triggered")
        return

    liquid_trades = trades[trades["Liquid"] == True].copy()
    illiquid_trades = trades[trades["Liquid"] == False].copy()

    def summary(label, subset, pnl_col):
        n = len(subset)
        if n == 0:
            print(f"\n  [{label}] No trades")
            return
        w = len(subset[subset["Correct"] == True])
        l = n - w
        total = subset[pnl_col].sum()
        avg = subset[pnl_col].mean()
        best = subset[pnl_col].max()
        worst = subset[pnl_col].min()
        wr = w / n * 100
        roi = total / (n * BET_SIZE) * 100
        print(f"\n  [{label}]")
        print(f"    Trades: {n} | Wins: {w} | Losses: {l} | Win rate: {wr:.1f}%")
        print(f"    Total P&L: ${total:+,.2f} | Avg: ${avg:+,.2f} | Best: ${best:+,.2f} | Worst: ${worst:+,.2f}")
        print(f"    ROI/trade: {roi:+.1f}%")

    print("\n" + "=" * 70)
    print("📊 TRADE TABLE (top 30)")
    print("=" * 70)
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.width", 220)
    cols = ["City", "Date", "Target", "Real", "Mkt", "Slipped", "Edge", "Signal", "Result", "Volume", "Liquid", "PnL_Brut", "PnL_Slip", "PnL_Liq"]
    print(trades[cols].head(30).to_string(index=False))

    # ── Section 1: Comparative P&L ──
    print("\n" + "═" * 70)
    print("📈 SECTION 1 — P&L COMPARISON")
    print("═" * 70)

    summary("BRUT (no slippage)", trades, "PnL_Brut")
    summary("WITH SLIPPAGE (±2¢)", trades, "PnL_Slip")
    summary("LIQUID ONLY (vol ≥ $50, +slippage)", liquid_trades, "PnL_Liq")

    illiq_count = len(illiquid_trades)
    print(f"\n  Illiquid trades excluded: {illiq_count}")
    if illiq_count:
        print(f"  P&L of illiquid trades (would have been): ${illiquid_trades['PnL_Slip'].sum():+,.2f}")

    # ── Section 2: By signal type ──
    print(f"\n{'═' * 70}")
    print("📊 SECTION 2 — BY SIGNAL TYPE")
    print("═" * 70)

    for sig in ["BUY YES", "BUY NO"]:
        sub = liquid_trades[liquid_trades["Signal"] == sig]
        summary(f"{sig} (liquid only)", sub, "PnL_Liq")

    # ── Section 3: Kelly Sizing ──
    print(f"\n{'═' * 70}")
    print("📊 SECTION 3 — KELLY SIZING SIMULATION")
    print("═" * 70)

    # Sort trades chronologically for Kelly simulation
    sim_trades = liquid_trades.sort_values(["Date", "City"]).reset_index(drop=True)

    scenarios = {
        "Flat $100": None,
        "Kelly 25%": 0.25,
        "Kelly 50%": 0.50,
    }

    kelly_results = {}

    for name, kelly_frac in scenarios.items():
        bankroll = STARTING_BANKROLL
        equity_curve = [bankroll]
        total_pnl = 0
        trade_count = 0

        for _, row in sim_trades.iterrows():
            signal = row["Signal"]
            mkt = row["Slipped"]
            edge_val = abs(row["Edge"])
            res_yes = row["Result"] == "YES"

            if kelly_frac is None:
                # Flat sizing
                bet = min(BET_SIZE, bankroll)
            else:
                # Kelly sizing
                if signal == "BUY YES":
                    odds = (1 / mkt) - 1 if mkt > 0 else 0
                elif signal == "BUY NO":
                    no_price = 1 - mkt
                    odds = (1 / no_price) - 1 if no_price > 0 else 0
                else:
                    odds = 0
                bet = kelly_bet(edge_val, odds, bankroll, kelly_frac)

            if bet < 1:  # skip tiny bets
                equity_curve.append(bankroll)
                continue

            pnl = calc_pnl(signal, mkt, res_yes, bet)
            bankroll += pnl
            bankroll = max(bankroll, 0)  # can't go below 0
            total_pnl += pnl
            trade_count += 1
            equity_curve.append(bankroll)

        max_dd = 0
        peak = equity_curve[0]
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        kelly_results[name] = {
            "trades": trade_count,
            "final": bankroll,
            "pnl": total_pnl,
            "roi": (bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100,
            "max_dd": max_dd,
            "curve": equity_curve,
        }

    for name, res in kelly_results.items():
        print(f"\n  [{name}]")
        print(f"    Starting bankroll : ${STARTING_BANKROLL:,.0f}")
        print(f"    Final bankroll    : ${res['final']:,.2f}")
        print(f"    Total P&L         : ${res['pnl']:+,.2f}")
        print(f"    ROI               : {res['roi']:+.1f}%")
        print(f"    Max drawdown      : {res['max_dd']:.1f}%")
        print(f"    Trades executed   : {res['trades']}")

    # ── Section 4: Equity curve (ASCII) ──
    print(f"\n{'═' * 70}")
    print("📈 EQUITY CURVES")
    print("═" * 70)

    for name, res in kelly_results.items():
        curve = res["curve"]
        if len(curve) < 2:
            continue
        # Normalize to 40 chars wide
        n_points = min(len(curve), 60)
        step = max(1, len(curve) // n_points)
        sampled = [curve[i] for i in range(0, len(curve), step)]
        mn, mx = min(sampled), max(sampled)
        rng = mx - mn if mx != mn else 1

        print(f"\n  {name} (${STARTING_BANKROLL:,.0f} → ${res['final']:,.0f})")
        height = 8
        for row in range(height, -1, -1):
            threshold = mn + (rng * row / height)
            line = "  "
            for val in sampled:
                if val >= threshold:
                    line += "█"
                else:
                    line += " "
            if row == height:
                line += f" ${mx:,.0f}"
            elif row == 0:
                line += f" ${mn:,.0f}"
            print(line)

    # ── Final summary ──
    print(f"\n{'═' * 70}")
    print("🏆 FINAL COMPARISON")
    print("═" * 70)

    brut_pnl = trades["PnL_Brut"].sum()
    slip_pnl = trades["PnL_Slip"].sum()
    liq_pnl = liquid_trades["PnL_Liq"].sum()
    brut_wr = len(trades[trades["Correct"] == True]) / len(trades) * 100
    liq_wr = len(liquid_trades[liquid_trades["Correct"] == True]) / len(liquid_trades) * 100 if len(liquid_trades) else 0

    print(f"""
  {'Metric':<30s} {'Brut':>12s} {'+ Slippage':>12s} {'Liquid only':>12s}
  {'─' * 66}
  {'Trades':<30s} {len(trades):>12d} {len(trades):>12d} {len(liquid_trades):>12d}
  {'Win rate':<30s} {brut_wr:>11.1f}% {brut_wr:>11.1f}% {liq_wr:>11.1f}%
  {'Total P&L':<30s} {'$'+f'{brut_pnl:+,.2f}':>12s} {'$'+f'{slip_pnl:+,.2f}':>12s} {'$'+f'{liq_pnl:+,.2f}':>12s}
  {'Avg P&L/trade':<30s} {'$'+f'{brut_pnl/len(trades):+,.2f}':>12s} {'$'+f'{slip_pnl/len(trades):+,.2f}':>12s} {'$'+f'{liq_pnl/len(liquid_trades):+,.2f}' if len(liquid_trades) else 'N/A':>12s}
  {'Slippage cost':<30s} {'$0':>12s} {'$'+f'{brut_pnl-slip_pnl:,.2f}':>12s} {'—':>12s}
  {'Illiquid excluded':<30s} {'0':>12s} {'0':>12s} {illiq_count:>12d}

  Kelly 25% final bankroll: ${kelly_results['Kelly 25%']['final']:,.2f} ({kelly_results['Kelly 25%']['roi']:+.1f}%)
  Kelly 50% final bankroll: ${kelly_results['Kelly 50%']['final']:,.2f} ({kelly_results['Kelly 50%']['roi']:+.1f}%)
""")

    # Save
    df.to_csv("backtest_results.csv", index=False)
    print("💾 Results saved to backtest_results.csv")


if __name__ == "__main__":
    main()
