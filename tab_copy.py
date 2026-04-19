"""Copy Trading tab -- reads copy_state.json and copy_trades.csv."""

import streamlit as st
import requests
import json as _json
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from pathlib import Path

GITHUB_RAW = "https://raw.githubusercontent.com/hectorm17/polymarket-tracker/main"
LB_API = "https://lb-api.polymarket.com"

WHALES = [
    {"addr": "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11", "label": "ColdMath",   "specialty": "weather"},
    {"addr": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1", "label": "Whale_2a2c", "specialty": "tennis"},
    {"addr": "0xbddf61af533ff524d27154e589d2d7a81510c684", "label": "Whale_bddf", "specialty": "nba"},
    {"addr": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf", "label": "Whale_ee61", "specialty": "tennis"},
]


@st.cache_data(ttl=60)
def load_copy_state():
    if Path("copy_state.json").exists():
        with open("copy_state.json") as f:
            return _json.load(f)
    try:
        r = requests.get(f"{GITHUB_RAW}/copy_state.json", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@st.cache_data(ttl=60)
def load_copy_csv():
    if Path("copy_trades.csv").exists():
        return pd.read_csv("copy_trades.csv")
    try:
        import io
        r = requests.get(f"{GITHUB_RAW}/copy_trades.csv", timeout=10)
        if r.status_code == 200:
            return pd.read_csv(io.StringIO(r.text))
    except Exception:
        pass
    return None


@st.cache_data(ttl=120)
def fetch_whale_pnl_cached(addr):
    try:
        r = requests.get(f"{LB_API}/profit", params={"window": "all", "address": addr.lower()}, timeout=10)
        data = r.json()
        return float(data[0].get("amount", 0)) if data else 0
    except Exception:
        return 0


@st.cache_data(ttl=60)
def fetch_whale_recent(addr):
    try:
        r = requests.get(f"https://data-api.polymarket.com/trades",
                         params={"user": addr.lower(), "limit": 10}, timeout=15)
        return r.json()
    except Exception:
        return []


def render_copy_tab():
    h1, h2 = st.columns([6, 1])
    h1.markdown(f'<div style="display:flex; align-items:center; gap:8px;"><span style="width:7px; height:7px; background:#4f6ef7; border-radius:50%; display:inline-block; animation:pulse 2s infinite;"></span><span style="color:#fff; font-weight:500;">Copy Trading</span><span style="color:#374151; font-size:12px;">{datetime.now().strftime("%H:%M:%S")}</span></div>', unsafe_allow_html=True)
    if h2.button("Refresh", key="refresh_copy", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    state = load_copy_state()

    if state is None:
        st.markdown('<div style="text-align:center; padding:48px; color:#374151;"><p style="font-size:14px;">Copy trader not started</p><p style="font-size:12px;">Run: python3 copy_trader.py</p></div>', unsafe_allow_html=True)
        return

    bankroll = state.get("bankroll", 1000)
    all_trades = state.get("trades", [])
    resolved = [t for t in all_trades if t.get("status") == "resolved"]
    pending = [t for t in all_trades if t.get("status") == "pending"]
    wins = len([t for t in resolved if t.get("pnl", 0) > 0])
    losses = len(resolved) - wins
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    wr = (wins / len(resolved) * 100) if resolved else 0
    roi = (bankroll - 1000) / 1000 * 100

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Bankroll", f"${bankroll:,.2f}", f"{roi:+.1f}%")
    m2.metric("Win Rate", f"{wr:.1f}%", f"{wins}W / {losses}L")
    m3.metric("P&L", f"${total_pnl:+,.2f}")
    m4.metric("Trades", f"{len(resolved)} / {len(all_trades)}")

    # Equity curve
    if resolved:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">EQUITY CURVE</div>', unsafe_allow_html=True)
        sorted_r = sorted(resolved, key=lambda t: t.get("timestamp", ""))
        cumul = [1000]
        for t in sorted_r:
            cumul.append(cumul[-1] + t.get("pnl", 0))
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=list(range(len(cumul))), y=cumul, mode="lines+markers",
            line=dict(color="#4f6ef7", width=2), marker=dict(size=3)))
        fig.add_hline(y=1000, line_dash="dash", line_color="#374151")
        fig.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#6b7280"), xaxis=dict(gridcolor="#1e2130", zeroline=False),
            yaxis=dict(gridcolor="#1e2130", zeroline=False))
        st.plotly_chart(fig, use_container_width=True)

    # Whale leaderboard
    st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">WHALES TRACKED</div>', unsafe_allow_html=True)

    st.markdown('''<div style="display:grid; grid-template-columns:1.5fr 100px 100px 100px 100px 100px;
        padding:8px 16px; color:#374151; font-size:10px; text-transform:uppercase; letter-spacing:0.08em; font-weight:600;">
        <div>WHALE</div><div>SPECIALTY</div><div style="text-align:right;">ALL-TIME P&L</div>
        <div style="text-align:right;">COPIES</div><div style="text-align:right;">OUR P&L</div>
        <div style="text-align:right;">OUR WR</div>
    </div>''', unsafe_allow_html=True)

    for w in WHALES:
        whale_pnl = fetch_whale_pnl_cached(w["addr"])
        w_trades = [t for t in all_trades if t.get("whale") == w["label"]]
        w_resolved = [t for t in w_trades if t.get("status") == "resolved"]
        w_pnl = sum(t.get("pnl", 0) for t in w_resolved)
        w_wins = len([t for t in w_resolved if t.get("pnl", 0) > 0])
        w_wr = (w_wins / len(w_resolved) * 100) if w_resolved else 0
        pc = "#10b981" if whale_pnl >= 0 else "#ef4444"
        opc = "#10b981" if w_pnl >= 0 else "#ef4444"

        st.markdown(f'''<div style="display:grid; grid-template-columns:1.5fr 100px 100px 100px 100px 100px;
            align-items:center; padding:12px 16px; border-bottom:1px solid #131620; font-size:13px;">
            <div><span style="color:#4f6ef7; font-family:monospace;">{w["addr"][:6]}...{w["addr"][-4:]}</span>
                <div style="color:#fff; font-size:12px; margin-top:2px;">{w["label"]}</div></div>
            <div style="color:#6b7280;">{w["specialty"]}</div>
            <div style="color:{pc}; text-align:right; font-weight:500;">${whale_pnl:+,.0f}</div>
            <div style="color:#6b7280; text-align:right;">{len(w_trades)}</div>
            <div style="color:{opc}; text-align:right; font-weight:500;">${w_pnl:+,.2f}</div>
            <div style="color:#6b7280; text-align:right;">{w_wr:.0f}%</div>
        </div>''', unsafe_allow_html=True)

    # Live whale feed
    st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">WHALE LIVE FEED</div>', unsafe_allow_html=True)

    all_whale_trades = []
    for w in WHALES:
        recent = fetch_whale_recent(w["addr"])
        for t in recent:
            t["_label"] = w["label"]
            all_whale_trades.append(t)
    all_whale_trades.sort(key=lambda t: t.get("timestamp", 0), reverse=True)

    for t in all_whale_trades[:20]:
        ts = t.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"
        side = (t.get("side") or "BUY").upper()
        sc = "background:#0d2818; color:#10b981;" if side == "BUY" else "background:#1f0d0d; color:#ef4444;"
        usdc = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
        ac = "#10b981" if side == "BUY" else "#ef4444"
        st.markdown(f'''<div style="display:flex; align-items:center; gap:12px; padding:10px 16px;
            border-bottom:1px solid #131620; font-size:13px;">
            <span style="color:#374151; min-width:55px; font-size:11px;">{time_str}</span>
            <span style="color:#4f6ef7; font-family:monospace; min-width:90px;">{t["_label"]}</span>
            <span style="{sc} padding:2px 8px; border-radius:4px; font-weight:600; font-size:11px;">{side}</span>
            <span style="color:#fff; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{t.get("title","?")[:50]}</span>
            <span style="color:{ac}; font-weight:500; min-width:60px; text-align:right;">${usdc:,.0f}</span>
        </div>''', unsafe_allow_html=True)

    # Our copied trades
    st.markdown(f'<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">OUR COPIES -- PENDING ({len(pending)})</div>', unsafe_allow_html=True)
    if pending:
        for t in pending[-10:]:
            st.markdown(f'''<div style="display:flex; align-items:center; gap:12px; padding:8px 16px;
                border-bottom:1px solid #131620; font-size:12px;">
                <span style="color:#4f6ef7;">{t.get("whale","")}</span>
                <span style="color:#fff; flex:1;">{t.get("market","")[:45]}</span>
                <span style="color:#6b7280;">@ {t.get("our_price",0):.3f}</span>
                <span style="color:#6b7280;">${t.get("our_stake",0):.0f}</span>
            </div>''', unsafe_allow_html=True)
    else:
        st.markdown('<p style="color:#374151; font-size:12px; padding:8px 16px;">No pending copies</p>', unsafe_allow_html=True)

    st.markdown(f'<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">RESOLVED ({len(resolved)})</div>', unsafe_allow_html=True)
    if resolved:
        for t in reversed(sorted(resolved, key=lambda x: x.get("timestamp", ""))):
            pv = t.get("pnl", 0)
            rc = "#10b981" if pv > 0 else "#ef4444"
            icon = "W" if pv > 0 else "L"
            st.markdown(f'''<div style="display:flex; align-items:center; gap:12px; padding:8px 16px;
                border-bottom:1px solid #131620; font-size:12px;">
                <span style="color:{rc}; font-weight:700; min-width:20px;">{icon}</span>
                <span style="color:#4f6ef7;">{t.get("whale","")}</span>
                <span style="color:#fff; flex:1;">{t.get("market","")[:45]}</span>
                <span style="color:{rc}; font-weight:500;">${pv:+.2f}</span>
                <span style="color:#374151; font-size:11px;">${t.get("our_stake",0):.0f}</span>
            </div>''', unsafe_allow_html=True)

    # Auto-refresh
    @st.fragment(run_every=30)
    def refresh_copy():
        st.cache_data.clear()
    refresh_copy()
