#!/usr/bin/env python3
"""
Polymarket Copy Trader
Monitors whale wallets and paper-copies their BUY trades in real time.
"""

import requests
import json
import csv
import time
import os
import sys
import base64
from datetime import datetime
from pathlib import Path

# =============================================
# CONFIG
# =============================================

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
LB_API = "https://lb-api.polymarket.com"

SCAN_INTERVAL = 120          # 2 minutes
STARTING_BANKROLL = 1000.0
MAX_POSITION_PCT = 0.02      # 2% of bankroll per trade
MIN_TRADE_USDC = 100         # ignore trades below $100
MAX_PRICE_DRIFT = 0.10       # skip if price moved >10% since whale entry

WHALES = [
    {"addr": "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11", "label": "ColdMath",   "specialty": "weather", "weight": 1.0},
    {"addr": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1", "label": "Whale_2a2c", "specialty": "tennis",  "weight": 0.5},
    {"addr": "0xbddf61af533ff524d27154e589d2d7a81510c684", "label": "Whale_bddf", "specialty": "nba",     "weight": 0.5},
    {"addr": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf", "label": "Whale_ee61", "specialty": "tennis",  "weight": 0.5},
]

CSV_FILE = Path("copy_trades.csv")
STATE_FILE = Path("copy_state.json")

# GitHub sync
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "hectorm17/polymarket-tracker"

# =============================================
# STATE
# =============================================

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "bankroll": STARTING_BANKROLL,
        "trades": [],
        "known_tx": [],        # transaction hashes we've already seen
        "start_time": datetime.now().isoformat(),
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_csv(row):
    exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "whale", "whale_addr", "market", "outcome", "side",
            "whale_price", "our_price", "whale_size_usdc", "our_stake",
            "asset", "condition_id", "event_slug", "status", "result", "pnl",
        ])
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def push_to_github(filename):
    if not GITHUB_TOKEN:
        return
    try:
        with open(filename, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        r = requests.get(api_url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None
        payload = {"message": f"auto: update {filename} {datetime.now().strftime('%H:%M')}", "content": content, "branch": "main"}
        if sha:
            payload["sha"] = sha
        requests.put(api_url, headers=headers, json=payload, timeout=15)
    except Exception:
        pass


def sync_to_github():
    push_to_github(str(CSV_FILE))
    push_to_github(str(STATE_FILE))


# =============================================
# DATA FETCHERS
# =============================================

def fetch_whale_trades(addr):
    try:
        r = requests.get(f"{DATA_API}/trades", params={"user": addr.lower(), "limit": 10}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def get_current_price(asset_id):
    """Get current market price for a token."""
    try:
        r = requests.get(f"{CLOB_API}/midpoint", params={"token_id": asset_id}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return float(data.get("mid", 0))
    except Exception:
        pass
    return None


def fetch_whale_pnl(addr):
    try:
        r = requests.get(f"{LB_API}/profit", params={"window": "all", "address": addr.lower()}, timeout=10)
        data = r.json()
        return float(data[0].get("amount", 0)) if data else 0
    except Exception:
        return 0


def check_resolution(condition_id):
    """Check if a market has resolved by looking at positions data."""
    # We check by fetching the market from gamma API
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets",
                         params={"condition_id": condition_id}, timeout=10)
        data = r.json()
        if data:
            m = data[0]
            if m.get("closed") and m.get("resolutionSource"):
                prices = m.get("outcomePrices", "")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if prices:
                    yes_price = float(prices[0])
                    return "YES" if yes_price > 0.5 else "NO"
    except Exception:
        pass
    return None


# =============================================
# CORE LOGIC
# =============================================

def scan_whales(state):
    """Scan whale wallets for new BUY trades to copy."""
    new_copies = []
    known_tx = set(state["known_tx"])

    for whale in WHALES:
        addr = whale["addr"]
        label = whale["label"]
        weight = whale["weight"]

        trades = fetch_whale_trades(addr)

        for t in trades:
            tx_hash = t.get("transactionHash", "")
            if not tx_hash or tx_hash in known_tx:
                continue

            # Mark as seen
            known_tx.add(tx_hash)

            side = (t.get("side") or "").upper()
            if side != "BUY":
                continue  # only copy buys

            usdc_size = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
            if usdc_size < MIN_TRADE_USDC:
                continue  # too small

            whale_price = float(t.get("price", 0))
            asset = t.get("asset", "")
            title = t.get("title", "?")
            outcome = t.get("outcome", "?")
            condition_id = t.get("conditionId", "")
            event_slug = t.get("eventSlug", "")

            # Check current price
            cur_price = get_current_price(asset)
            if cur_price is None:
                continue

            # Check price drift
            drift = abs(cur_price - whale_price)
            if drift > MAX_PRICE_DRIFT:
                print(f"    SKIP (drift {drift:.3f}): {label} {title[:40]}")
                continue

            # Calculate our stake
            our_stake = state["bankroll"] * MAX_POSITION_PCT * weight
            our_stake = min(our_stake, state["bankroll"] * 0.05)  # hard cap 5%
            if our_stake < 1:
                continue

            trade = {
                "timestamp": datetime.now().isoformat(),
                "whale": label,
                "whale_addr": addr,
                "market": title,
                "outcome": outcome,
                "side": "BUY",
                "whale_price": round(whale_price, 4),
                "our_price": round(cur_price, 4),
                "whale_size_usdc": round(usdc_size, 2),
                "our_stake": round(our_stake, 2),
                "asset": asset,
                "condition_id": condition_id,
                "event_slug": event_slug,
                "status": "pending",
                "result": None,
                "pnl": 0,
            }

            state["trades"].append(trade)
            new_copies.append(trade)

            append_csv(trade)
            print(f"    COPY: {label} BUY {outcome} | {title[:45]} | whale ${usdc_size:.0f} @ {whale_price:.3f} | us ${our_stake:.0f} @ {cur_price:.3f}")

    state["known_tx"] = list(known_tx)[-500:]  # keep last 500 tx hashes
    return new_copies


def resolve_pending(state):
    """Check pending trades for resolution."""
    updated = False
    for trade in state["trades"]:
        if trade["status"] != "pending":
            continue

        resolution = check_resolution(trade["condition_id"])
        if resolution is None:
            continue

        our_price = trade["our_price"]
        our_stake = trade["our_stake"]
        outcome = trade["outcome"]

        # Did our outcome win?
        won = (resolution == "YES" and outcome in ("Yes", "YES")) or \
              (resolution == "NO" and outcome in ("No", "NO"))

        if won:
            pnl = our_stake * (1 - our_price)
        else:
            pnl = -our_stake * our_price

        trade["status"] = "resolved"
        trade["result"] = "WIN" if won else "LOSS"
        trade["pnl"] = round(pnl, 2)
        state["bankroll"] += pnl
        updated = True

        icon = "WIN" if won else "LOSS"
        print(f"    RESOLVED: [{icon}] {trade['whale']} | {trade['market'][:40]} | P&L ${pnl:+.2f}")

    if updated:
        save_state(state)
    return updated


# =============================================
# DISPLAY
# =============================================

def print_dashboard(state, new_copies):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bankroll = state["bankroll"]
    trades = state["trades"]
    roi = (bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100

    resolved = [t for t in trades if t["status"] == "resolved"]
    pending = [t for t in trades if t["status"] == "pending"]
    wins = len([t for t in resolved if t.get("pnl", 0) > 0])
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    wr = (wins / len(resolved) * 100) if resolved else 0

    os.system("clear" if os.name != "nt" else "cls")
    print("=" * 65)
    print(f"  COPY TRADER -- {now}")
    print("=" * 65)
    print(f"  Bankroll    : ${bankroll:,.2f} ({roi:+.1f}%)")
    print(f"  Resolved    : {len(resolved)} | {wins}W / {len(resolved)-wins}L | WR {wr:.1f}% | P&L ${total_pnl:+,.2f}")
    print(f"  Pending     : {len(pending)} trades")
    print(f"  Total       : {len(trades)} trades copied")

    # Whale stats
    print(f"\n{'─' * 65}")
    print(f"  WHALE PERFORMANCE")
    for whale in WHALES:
        pnl = fetch_whale_pnl(whale["addr"])
        w_trades = [t for t in trades if t["whale"] == whale["label"]]
        w_resolved = [t for t in w_trades if t["status"] == "resolved"]
        w_pnl = sum(t.get("pnl", 0) for t in w_resolved)
        w_wins = len([t for t in w_resolved if t.get("pnl", 0) > 0])
        w_wr = (w_wins / len(w_resolved) * 100) if w_resolved else 0
        print(f"    {whale['label']:15s} | All-time P&L ${pnl:+,.0f} | Our copies: {len(w_trades)} ({len(w_resolved)} resolved, WR {w_wr:.0f}%, P&L ${w_pnl:+,.2f})")

    if new_copies:
        print(f"\n{'─' * 65}")
        print(f"  NEW COPIES ({len(new_copies)})")
        for c in new_copies:
            print(f"    {c['whale']:15s} BUY {c['outcome']} | {c['market'][:40]} | ${c['our_stake']:.0f} @ {c['our_price']:.3f}")

    if pending:
        print(f"\n{'─' * 65}")
        print(f"  PENDING ({len(pending)})")
        for t in pending[-8:]:
            print(f"    {t['whale']:15s} {t['outcome']} | {t['market'][:40]} | ${t['our_stake']:.0f}")

    print(f"\n{'─' * 65}")
    print(f"  Next scan in {SCAN_INTERVAL}s. Ctrl+C to stop.")


# =============================================
# MAIN
# =============================================

def main():
    state = load_state()
    print("  Copy Trader starting...")
    print(f"  Bankroll: ${state['bankroll']:,.2f}")
    print(f"  Whales: {len(WHALES)}")
    print(f"  Existing trades: {len(state['trades'])}")
    print(f"  Scan every {SCAN_INTERVAL}s\n")

    # Fix known_tx if it was accidentally set to a string/int
    if not isinstance(state.get("known_tx"), list):
        state["known_tx"] = []

    while True:
        try:
            resolve_pending(state)
            new_copies = scan_whales(state)
            save_state(state)
            print_dashboard(state, new_copies)
            sync_to_github()

            for remaining in range(SCAN_INTERVAL, 0, -1):
                mins, secs = divmod(remaining, 60)
                sys.stdout.write(f"\r  Next scan in {mins:02d}:{secs:02d}  ")
                sys.stdout.flush()
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n  Stopping copy trader...")
            save_state(state)
            resolved = [t for t in state["trades"] if t["status"] == "resolved"]
            wins = len([t for t in resolved if t.get("pnl", 0) > 0])
            total_pnl = sum(t.get("pnl", 0) for t in resolved)
            wr = (wins / len(resolved) * 100) if resolved else 0
            print(f"\n  Final bankroll: ${state['bankroll']:,.2f}")
            print(f"  Trades: {len(state['trades'])} ({len(resolved)} resolved)")
            print(f"  Win rate: {wr:.1f}% | P&L: ${total_pnl:+,.2f}")
            print(f"  State saved to {STATE_FILE}")
            break

        except Exception as e:
            print(f"\n  Error: {e}")
            save_state(state)
            time.sleep(30)


if __name__ == "__main__":
    main()
