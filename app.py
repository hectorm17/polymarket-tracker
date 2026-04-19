import streamlit as st
import requests
import feedparser
import ssl
import time
import json as _json
import pandas as pd
import plotly.graph_objects as go
from datetime import date, datetime, timezone
from pathlib import Path
from supabase import create_client

ssl._create_default_https_context = ssl._create_unverified_context

# =============================================
# CONSTANTS
# =============================================

TICKERS = {
    "Crypto": ["BTC-USD", "ETH-USD", "SOL-USD"],
    "US": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
    "EU": ["^FCHI", "MC.PA", "RMS.PA", "AIR.PA", "TTE.PA"],
    "Forex": ["EURUSD=X", "DX-Y.NYB", "JPY=X"],
    "Macro": ["^VIX", "^TNX", "GC=F", "CL=F"],
}
TICKER_LABELS = {
    "BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL",
    "SPY": "SPY", "QQQ": "QQQ", "AAPL": "Apple", "NVDA": "Nvidia", "TSLA": "Tesla",
    "^FCHI": "CAC 40", "MC.PA": "LVMH", "RMS.PA": "Hermes", "AIR.PA": "Airbus", "TTE.PA": "TotalEnergies",
    "EURUSD=X": "EUR/USD", "DX-Y.NYB": "DXY", "JPY=X": "USD/JPY",
    "^VIX": "VIX", "^TNX": "US 10Y", "GC=F": "Gold", "CL=F": "Oil WTI",
}
FEEDS = {
    "Reuters": "https://news.google.com/rss/search?q=site:reuters.com+markets+economy&hl=en-US&gl=US&ceid=US:en",
    "Bloomberg": "https://news.google.com/rss/search?q=site:bloomberg.com+markets&hl=en-US&gl=US&ceid=US:en",
    "FT": "https://www.ft.com/rss/home/uk",
    "WSJ": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "Les Echos": "https://news.google.com/rss/search?q=site:lesechos.fr+bourse+finance+march%C3%A9&hl=fr-FR&gl=FR&ceid=FR:fr",
    "CoinDesk": "https://news.google.com/rss/search?q=site:coindesk.com+crypto+bitcoin&hl=en-US&gl=US&ceid=US:en",
    "ZeroHedge": "https://feeds.feedburner.com/zerohedge/feed",
    "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
}
BREAKING_KEYWORDS = [
    "hermes", "lvmh", "apple", "nvidia", "tesla", "airbus", "total",
    "bitcoin", "btc", "ethereum", "eth", "solana",
    "fed", "bce", "ecb", "powell", "lagarde",
    "trump", "tarif", "tariff", "inflation", "cpi", "recession",
    "taux", "rate", "war", "guerre", "sanctions", "iran", "china", "chine",
    "crash", "rallye", "rally", "sell-off", "selloff", "plunge", "surge",
    "vix", "volatil", "oil", "gold",
]
DATA_API = "https://data-api.polymarket.com"
LB_API = "https://lb-api.polymarket.com"

# =============================================
# SUPABASE
# =============================================

@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

db = get_supabase()

# =============================================
# DATA FETCHERS
# =============================================

@st.cache_data(ttl=60)
def fetch_positions(address):
    r = requests.get(f"{DATA_API}/positions", params={"user": address.lower(), "sizeThreshold": 0.1}, timeout=15)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=60)
def fetch_trades(address):
    r = requests.get(f"{DATA_API}/trades", params={"user": address.lower(), "limit": 10}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_recent_trades(address):
    r = requests.get(f"{DATA_API}/trades", params={"user": address.lower(), "limit": 5}, timeout=15)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=60)
def fetch_pnl(address):
    r = requests.get(f"{LB_API}/profit", params={"window": "all", "address": address.lower()}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return float(data[0].get("amount", 0)) if data else 0.0

@st.cache_data(ttl=60)
def fetch_all_prices():
    import yfinance as yf
    results = {}
    for sym in [s for g in TICKERS.values() for s in g]:
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = info.get("lastPrice", 0) or info.get("last_price", 0)
            prev = info.get("previousClose", 0) or info.get("previous_close", 0)
            chg = ((price - prev) / prev * 100) if prev else 0
            results[sym] = {"price": price, "change": chg, "label": TICKER_LABELS.get(sym, sym)}
        except Exception:
            results[sym] = {"price": 0, "change": 0, "label": TICKER_LABELS.get(sym, sym)}
    return results

@st.cache_data(ttl=300)
def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        data = r.json()["data"][0]
        return {"value": int(data["value"]), "label": data["value_classification"]}
    except Exception:
        return {"value": 0, "label": "N/A"}

@st.cache_data(ttl=120)
def fetch_all_news():
    items = []
    for source, url in FEEDS.items():
        try:
            d = feedparser.parse(url)
            for e in d.entries[:8]:
                pub = e.get("published_parsed")
                ts = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
                title = e.get("title", "")
                tl = title.lower()
                items.append({"title": title, "source": source, "link": e.get("link", ""), "time": ts,
                              "breaking": any(kw in tl for kw in BREAKING_KEYWORDS),
                              "tickers": [l for l in TICKER_LABELS.values() if l.lower() in tl or l.upper() in title]})
        except Exception:
            continue
    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items

@st.cache_data(ttl=30)
def fetch_whale_trades(address):
    try:
        r = requests.get(f"{DATA_API}/trades", params={"user": address.lower(), "limit": 20}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []

@st.cache_data(ttl=60)
def fetch_whale_value(address):
    try:
        r = requests.get(f"{DATA_API}/value", params={"user": address.lower()}, timeout=10)
        data = r.json()
        return float(data[0].get("value", 0)) if data else 0
    except Exception:
        return 0

@st.cache_data(ttl=60)
def fetch_whale_pnl(address):
    try:
        r = requests.get(f"{LB_API}/profit", params={"window": "all", "address": address.lower()}, timeout=10)
        data = r.json()
        return float(data[0].get("amount", 0)) if data else 0
    except Exception:
        return 0

# =============================================
# DB HELPERS
# =============================================

def load_wallets():
    return db.table("wallets").select("*").order("created_at").execute().data

def add_wallet(address, label):
    db.table("wallets").insert({"address": address.lower(), "label": label}).execute()

def remove_wallet(address):
    db.table("wallets").delete().eq("address", address.lower()).execute()

# =============================================
# HELPERS
# =============================================

def time_ago(ts):
    if not ts: return ""
    mins = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
    if mins < 0: return "now"
    if mins < 60: return f"{mins}m ago"
    if mins < 1440: return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"

def fmt_price(p, sym=""):
    if "JPY" in sym: return f"{p:,.2f}"
    if "EUR" in sym and "=" in sym: return f"${p:.4f}"
    if "TNX" in sym: return f"{p:.2f}%"
    if "VIX" in sym: return f"{p:.1f}"
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:,.2f}"
    return f"${p:.4f}"

def short_addr(addr):
    return addr[:6] + "..." + addr[-4:]

def detect_specialty(trades):
    cats = {"Sports": 0, "Crypto": 0, "Politics": 0, "Weather": 0, "Other": 0}
    for t in trades:
        title = (t.get("title") or "").lower()
        if any(k in title for k in ["win", "beat", "spread", "nba", "nfl", "nhl", "tennis", "fc ", "vs.", "match"]): cats["Sports"] += 1
        elif any(k in title for k in ["bitcoin", "btc", "eth", "crypto", "up or down"]): cats["Crypto"] += 1
        elif any(k in title for k in ["trump", "election", "president", "congress", "vote"]): cats["Politics"] += 1
        elif any(k in title for k in ["temperature", "weather", "highest temp"]): cats["Weather"] += 1
        else: cats["Other"] += 1
    return max(cats, key=cats.get) if trades else "N/A"

# =============================================
# SESSION STATE
# =============================================

for key, default in {"alerts": [], "last_trade_ts": {}, "alert_threshold": 100.0,
                      "analyses": [], "daily_summaries": [], "auto_analysis_done": False}.items():
    if key not in st.session_state:
        st.session_state[key] = default

if "tracked_whales" not in st.session_state:
    st.session_state.tracked_whales = [
        {"addr": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1", "label": "Whale-Tennis", "fav": True},
        {"addr": "0xbddf61af533ff524d27154e589d2d7a81510c684", "label": "Whale-NBA", "fav": True},
        {"addr": "0x2005d16a84ceefa912d4e380cd32e7ff827875ea", "label": "Whale-Football", "fav": False},
        {"addr": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf", "label": "Whale-Mixed", "fav": False},
        {"addr": "0xc2e7800b5af46e6093872b177b7a5e7f0563be51", "label": "Warriors-Fan", "fav": False},
        {"addr": "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11", "label": "ColdMath", "fav": False},
    ]

# =============================================
# PAGE CONFIG + DESIGN SYSTEM
# =============================================

st.set_page_config(page_title="PolyTracker", page_icon="P", layout="wide")

st.markdown('''
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
* { font-family: Inter, system-ui, sans-serif !important; }
.stApp { background-color: #0d0f14 !important; }
section[data-testid="stSidebar"] { display: none; }
header[data-testid="stHeader"] { background: #0d0f14 !important; border-bottom: 1px solid #1e2130; }
.block-container { padding: 0 2rem 2rem 2rem !important; max-width: 1200px !important; }

div[data-testid="stTabs"] button {
    background: transparent !important; color: #6b7280 !important; border: none !important;
    font-size: 14px !important; font-weight: 500 !important; padding: 12px 20px !important;
}
div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #ffffff !important; border-bottom: 2px solid #4f6ef7 !important;
}
div[data-testid="stMetricValue"] { color: #ffffff !important; }
div[data-testid="stMetricLabel"] { color: #6b7280 !important; text-transform: uppercase !important;
    letter-spacing: 0.08em !important; font-size: 11px !important; }
div[data-testid="metric-container"] {
    background: #131620 !important; border: 1px solid #1e2130 !important; border-radius: 12px !important; padding: 20px !important;
}
div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
.stDataFrame table { background: #131620 !important; }

.pt-card { background: #131620; border: 1px solid #1e2130; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
.pt-row { display: flex; align-items: center; gap: 16px; padding: 14px 16px; border-bottom: 1px solid #131620;
           font-size: 13px; transition: background 0.1s; }
.pt-row:hover { background: #131620; }
.pt-time { color: #374151; min-width: 60px; font-size: 12px; }
.pt-addr { color: #4f6ef7; font-family: 'Courier New', monospace !important; font-size: 13px; }
.pt-side-buy { background: #0d2818; color: #10b981; padding: 2px 8px; border-radius: 4px;
               font-weight: 600; font-size: 11px; min-width: 35px; text-align: center; display: inline-block; }
.pt-side-sell { background: #1f0d0d; color: #ef4444; padding: 2px 8px; border-radius: 4px;
                font-weight: 600; font-size: 11px; min-width: 35px; text-align: center; display: inline-block; }
.pt-title { color: #fff; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pt-amount-green { color: #10b981; font-weight: 500; min-width: 70px; text-align: right; }
.pt-amount-red { color: #ef4444; font-weight: 500; min-width: 70px; text-align: right; }
.pt-amount { color: #6b7280; font-weight: 500; min-width: 70px; text-align: right; }
.pt-whale-badge { background: #1a2040; color: #4f6ef7; font-size: 11px; padding: 2px 8px;
                  border-radius: 4px; border: 1px solid #2a3a6a; display: inline-block; }
.pt-breaking-badge { background: #ef4444; color: #fff; font-size: 10px; font-weight: 700;
                     padding: 2px 6px; border-radius: 3px; display: inline-block; margin-right: 6px; }
.pt-ticker-badge { background: #1a2040; color: #818cf8; font-size: 10px; font-weight: 600;
                   padding: 2px 6px; border-radius: 3px; display: inline-block; margin-right: 4px; }
.pt-news { padding: 12px 16px; border-bottom: 1px solid #1e2130; font-size: 13px; }
.pt-news-breaking { padding: 12px 16px; border-left: 3px solid #ef4444; background: #1a0a0a;
                    border-bottom: 1px solid #1e2130; font-size: 13px; }
.pt-source { color: #4f6ef7; font-size: 11px; font-weight: 600; }
.pt-meta { color: #374151; font-size: 11px; }
.pt-signal { display: inline-flex; align-items: center; gap: 8px; border-radius: 8px;
             padding: 8px 16px; margin-bottom: 16px; }
.pt-signal-off { background: #1f0d0d; border: 1px solid #ef444433; }
.pt-signal-off span { color: #ef4444; }
.pt-signal-on { background: #0d2818; border: 1px solid #10b98133; }
.pt-signal-on span { color: #10b981; }
.pt-signal-neutral { background: #1f1a0d; border: 1px solid #f59e0b33; }
.pt-signal-neutral span { color: #f59e0b; }
.pt-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.pt-section { color: #6b7280; font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
              font-weight: 600; margin: 24px 0 12px 0; }
.pt-alert { padding: 10px 16px; border-left: 3px solid #4f6ef7; background: #131620;
            border-radius: 0 8px 8px 0; margin-bottom: 6px; font-size: 13px; }
.pt-fg-bar { background: #1e2130; border-radius: 6px; height: 8px; width: 100%; overflow: hidden; }
.pt-fg-fill { height: 100%; border-radius: 6px; }
.pt-empty { text-align: center; padding: 48px 0; color: #374151; }
</style>
''', unsafe_allow_html=True)

# Header
st.markdown('''
<div style="display:flex; justify-content:space-between; align-items:center;
            padding:20px 0; border-bottom:1px solid #1e2130; margin-bottom:24px;">
  <div style="display:flex; align-items:center; gap:12px;">
    <div style="background:#4f6ef7; border-radius:8px; width:32px; height:32px;
                display:flex; align-items:center; justify-content:center;">
      <span style="color:#fff; font-weight:700; font-size:14px;">P</span>
    </div>
    <span style="color:#fff; font-size:18px; font-weight:600; letter-spacing:-0.3px;">PolyTracker</span>
  </div>
  <div style="display:flex; align-items:center; gap:6px;">
    <span style="display:inline-block; width:7px; height:7px; background:#10b981;
                 border-radius:50%; animation: pulse 2s infinite;"></span>
    <span style="color:#10b981; font-size:13px; font-weight:500;">Live</span>
  </div>
</div>
''', unsafe_allow_html=True)

# =============================================
# TABS
# =============================================

tab_wallets, tab_analyst, tab_portfolio, tab_ideas, tab_paper, tab_live = st.tabs(["Wallets", "Analyst", "Portfolio", "Trade Ideas", "Paper Trading", "Live Feed"])

# =============================================
# TAB 1: WALLETS
# =============================================

with tab_wallets:

    with st.expander("Alerts", expanded=bool(st.session_state.alerts)):
        st.session_state.alert_threshold = st.number_input(
            "Min trade size (USDC)", min_value=0.0, value=st.session_state.alert_threshold, step=10.0)
        if st.session_state.alerts:
            for a in st.session_state.alerts:
                st.markdown(f'<div class="pt-alert">{a["msg"]}<br/><span class="pt-meta">{a["time"]}</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="pt-meta">No alerts yet</p>', unsafe_allow_html=True)

    with st.form("add_wallet", clear_on_submit=True):
        cols = st.columns([3, 2, 1])
        address = cols[0].text_input("Wallet", placeholder="0x... proxy wallet address", label_visibility="collapsed")
        label = cols[1].text_input("Label", placeholder="Label (optional)", label_visibility="collapsed")
        submitted = cols[2].form_submit_button("Add Wallet", use_container_width=True)

    if submitted and address.strip():
        addr = address.strip()
        lbl = label.strip() or short_addr(addr)
        try:
            add_wallet(addr, lbl)
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            if "duplicate" in str(e).lower() or "23505" in str(e):
                st.warning("Wallet already tracked.")
            else:
                st.error(f"Error: {e}")

    wallets = load_wallets()
    if not wallets:
        st.markdown('<div class="pt-empty"><p style="font-size:16px; font-weight:500;">No wallets tracked</p><p style="font-size:13px;">Add a Polymarket proxy wallet address above</p></div>', unsafe_allow_html=True)
    else:
        for wallet in wallets:
            addr, lbl = wallet["address"], wallet["label"]
            st.markdown(f'<div style="border-top:1px solid #1e2130; margin-top:16px;"></div>', unsafe_allow_html=True)
            hcol1, hcol2 = st.columns([6, 1])
            hcol1.markdown(f'<div style="margin-top:16px;"><span style="color:#fff; font-size:16px; font-weight:600;">{lbl}</span><br/><span class="pt-addr">{short_addr(addr)}</span></div>', unsafe_allow_html=True)
            if hcol2.button("Remove", key=f"rm_{addr}", use_container_width=True):
                remove_wallet(addr)
                st.cache_data.clear()
                st.rerun()
            try:
                positions, trades, total_pnl = fetch_positions(addr), fetch_trades(addr), fetch_pnl(addr)
            except Exception as e:
                st.error(f"Failed to fetch: {e}")
                continue
            today_d = date.today()
            active_pos, closed_pos = [], []
            for p in positions:
                end = p.get("endDate")
                try: is_active = date.fromisoformat(end) >= today_d if end else True
                except: is_active = True
                (active_pos if is_active else closed_pos).append(p)
            w_wins = [p for p in closed_pos if float(p.get("cashPnl", 0)) + float(p.get("realizedPnl", 0)) > 0]
            w_wr = (len(w_wins) / len(closed_pos) * 100) if closed_pos else 0
            m1, m2, m3 = st.columns(3)
            pnl_color = "#10b981" if total_pnl >= 0 else "#ef4444"
            m1.metric("Total P&L", f"${total_pnl:+,.2f}")
            m2.metric("Win Rate", f"{w_wr:.1f}%")
            m3.metric("Open Positions", len(active_pos))
            t_pos, t_trades = st.tabs([f"Positions ({len(active_pos)})", f"Trades ({len(trades)})"])
            with t_pos:
                if not active_pos:
                    st.markdown('<p class="pt-meta">No open positions</p>', unsafe_allow_html=True)
                else:
                    rows = [{"Market": p.get("title","?"), "Side": (p.get("outcome") or "Yes").upper(), "Size": float(p.get("size",0)), "Avg": float(p.get("avgPrice",0)), "Current": float(p.get("curPrice",0)), "P&L": float(p.get("cashPnl",0))} for p in active_pos]
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={"Size": st.column_config.NumberColumn(format="%.2f"), "Avg": st.column_config.NumberColumn(format="%.3f"), "Current": st.column_config.NumberColumn(format="%.3f"), "P&L": st.column_config.NumberColumn(format="%+.2f")})
            with t_trades:
                if not trades:
                    st.markdown('<p class="pt-meta">No recent trades</p>', unsafe_allow_html=True)
                else:
                    rows = [{"Market": t.get("title","--"), "Side": (t.get("side") or "BUY").upper(), "Size": float(t.get("size",0)), "Price": float(t.get("price",0)), "Time": datetime.fromtimestamp(t["timestamp"]).strftime("%Y-%m-%d %H:%M") if t.get("timestamp") else "--"} for t in trades]
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={"Size": st.column_config.NumberColumn(format="%.2f"), "Price": st.column_config.NumberColumn(format="%.3f")})

        @st.fragment(run_every=60)
        def poll_alerts():
            wlist = load_wallets()
            if not wlist: return
            threshold = st.session_state.alert_threshold
            new_alerts = []
            for w in wlist:
                a, l = w["address"], w["label"]
                try: recent = fetch_recent_trades(a)
                except: continue
                if not recent: continue
                last_known = st.session_state.last_trade_ts.get(a, 0)
                if last_known == 0:
                    st.session_state.last_trade_ts[a] = recent[0].get("timestamp", 0)
                    continue
                for t in recent:
                    ts = t.get("timestamp", 0)
                    if ts <= last_known: break
                    usdc = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
                    if usdc < threshold: continue
                    msg = f'<b>{l}</b> -- {(t.get("side") or "BUY").upper()} {float(t.get("size",0)):.1f} shares "{t.get("title","?")[:50]}" at {float(t.get("price",0))*100:.0f}c'
                    new_alerts.append({"msg": msg, "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), "ts": ts})
                    st.toast(f'{l} -- new trade detected', icon=None)
                latest = recent[0].get("timestamp", 0)
                if latest > last_known: st.session_state.last_trade_ts[a] = latest
            if new_alerts: st.session_state.alerts = (new_alerts + st.session_state.alerts)[:20]
        poll_alerts()
        if st.button("Refresh", use_container_width=True, key="refresh_wallets"):
            st.cache_data.clear()
            st.rerun()

# =============================================
# TAB 2: ANALYST
# =============================================

# Global data (shared across tabs)
all_prices = fetch_all_prices()
fg = fetch_fear_greed()
all_news = fetch_all_news()
has_api_key = "ANTHROPIC_API_KEY" in st.secrets

with tab_analyst:
    now_str = datetime.now().strftime("%H:%M:%S")

    def build_full_context():
        lines = ["MARKET DATA:"]
        for cat, syms in TICKERS.items():
            for sym in syms:
                d = all_prices.get(sym, {"price": 0, "change": 0, "label": sym})
                lines.append(f"  {d['label']}: {fmt_price(d['price'], sym)} ({d['change']:+.2f}%)")
        vix, tnx = all_prices.get("^VIX", {"price":0,"change":0}), all_prices.get("^TNX", {"price":0,"change":0})
        gold, oil = all_prices.get("GC=F", {"price":0,"change":0}), all_prices.get("CL=F", {"price":0,"change":0})
        dxy = all_prices.get("DX-Y.NYB", {"price":0,"change":0})
        lines.append(f"\nMACRO:\n  VIX: {vix['price']:.1f} ({vix['change']:+.2f}%)\n  US 10Y: {tnx['price']:.2f}% ({tnx['change']:+.2f}%)")
        lines.append(f"  Gold: ${gold['price']:,.0f} ({gold['change']:+.2f}%)\n  Oil WTI: ${oil['price']:.2f} ({oil['change']:+.2f}%)")
        lines.append(f"  DXY: {dxy['price']:.2f} ({dxy['change']:+.2f}%)\n  Fear & Greed: {fg['value']} -- {fg['label']}")
        brk = [n for n in all_news if n["breaking"]]
        nrm = [n for n in all_news if not n["breaking"]]
        lines.append("\nNEWS:")
        for n in (brk + nrm)[:10]:
            lines.append(f"  - {'[BREAKING] ' if n['breaking'] else ''}[{n['source']}] {n['title']} ({time_ago(n['time'])})")
        return "\n".join(lines)

    SYSTEM_PROMPT = """Tu es un analyste financier senior. Tu couvres crypto, actions US et EU, forex et macro.
A partir des donnees marche et news fournies, genere systematiquement :
1. SIGNAL global (RISK ON / RISK OFF / NEUTRE) en une phrase
2. CONTEXTE macro en 2 phrases max
3. 2 a 3 OPPORTUNITES ou ALERTES concretes sur des actifs specifiques, chacune avec :
   - Le nom de l'actif et sa variation
   - 1 phrase de justification basee sur les donnees
   - 1 recommandation directe (long / short / attendre / surveiller)
Sois direct et actionnable. Pas de disclaimer. Reponds en francais."""

    def run_global_analysis():
        context = build_full_context()
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
            resp = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=1200, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": f"{context}\n\nGenere ton analyse."}])
            text = resp.content[0].text
            st.session_state.analyses.append({"time": now_str, "text": text})
            st.session_state.analyses = st.session_state.analyses[-24:]
            return text
        except Exception as e:
            return f"Claude API error: {e}"

    # -- AI Analysis section --
    st.markdown('<div class="pt-section">AI ANALYSIS</div>', unsafe_allow_html=True)
    if not has_api_key:
        st.warning("Add ANTHROPIC_API_KEY to Streamlit secrets to enable AI analysis.")
    else:
        if not st.session_state.auto_analysis_done and not st.session_state.analyses:
            with st.spinner("Generating initial analysis..."):
                run_global_analysis()
            st.session_state.auto_analysis_done = True

        if st.session_state.analyses:
            latest = st.session_state.analyses[-1]
            text = latest["text"]
            tl = text.lower()
            if "risk off" in tl:
                st.markdown('<div class="pt-signal pt-signal-off"><span class="pt-dot" style="background:#ef4444;"></span><span style="font-weight:600; font-size:14px;">RISK OFF</span></div>', unsafe_allow_html=True)
            elif "risk on" in tl:
                st.markdown('<div class="pt-signal pt-signal-on"><span class="pt-dot" style="background:#10b981;"></span><span style="font-weight:600; font-size:14px;">RISK ON</span></div>', unsafe_allow_html=True)
            elif "neutre" in tl:
                st.markdown('<div class="pt-signal pt-signal-neutral"><span class="pt-dot" style="background:#f59e0b;"></span><span style="font-weight:600; font-size:14px;">NEUTRAL</span></div>', unsafe_allow_html=True)
            st.markdown(f'<div class="pt-card"><div class="pt-meta" style="margin-bottom:12px;">Generated at {latest["time"]}</div>', unsafe_allow_html=True)
            st.markdown(text)
            st.markdown('</div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        if c1.button("Regenerate Analysis", use_container_width=True, key="regen"):
            with st.spinner("Analyzing..."): run_global_analysis()
            st.rerun()
        if c2.button("Daily Summary", use_container_width=True, key="summary"):
            if not st.session_state.analyses: st.warning("No analyses to summarize.")
            else:
                all_a = "\n\n---\n\n".join(f"[{a['time']}]\n{a['text']}" for a in st.session_state.analyses)
                with st.spinner("Generating..."):
                    try:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                        resp = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=1500,
                            system="Resume quotidien. 1) SYNTHESE 2) EVENEMENTS CLES 3) EVOLUTION DU SENTIMENT 4) SIGNAL. Francais.",
                            messages=[{"role": "user", "content": f"Analyses:\n\n{all_a}"}])
                        st.session_state.daily_summaries.append({"date": date.today().isoformat(), "time": now_str, "text": resp.content[0].text})
                        st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

        if st.session_state.daily_summaries:
            for s in reversed(st.session_state.daily_summaries):
                with st.expander(f"Summary {s['date']} ({s['time']})"):
                    st.markdown(s["text"])

    # -- Prices --
    st.markdown('<div class="pt-section">MARKET PRICES</div>', unsafe_allow_html=True)
    hdr1, hdr2 = st.columns([6, 1])
    hdr1.markdown(f'<div style="display:flex; align-items:center; gap:6px;"><span class="pt-dot" style="background:#10b981; animation:pulse 2s infinite;"></span><span style="color:#6b7280; font-size:12px;">Last update: {now_str}</span></div>', unsafe_allow_html=True)
    if hdr2.button("Refresh", key="refresh_analyst", use_container_width=True):
        st.cache_data.clear()
        st.session_state.auto_analysis_done = False
        st.rerun()

    cat_tabs = st.tabs(list(TICKERS.keys()))
    for cat_tab, (_, syms) in zip(cat_tabs, TICKERS.items()):
        with cat_tab:
            cols = st.columns(len(syms))
            for col, sym in zip(cols, syms):
                d = all_prices.get(sym, {"price": 0, "change": 0, "label": sym})
                col.metric(d["label"], fmt_price(d["price"], sym), f"{d['change']:+.2f}%")

    # Fear & Greed
    fg_val, fg_label = fg["value"], fg["label"]
    fg_color = "#ef4444" if fg_val <= 25 else "#f97316" if fg_val <= 45 else "#eab308" if fg_val <= 55 else "#10b981"
    st.markdown(f'<div style="display:flex; align-items:center; gap:12px; margin:12px 0;"><span style="color:#6b7280; font-size:13px;">Fear & Greed</span><span style="color:#fff; font-weight:600;">{fg_val}</span><span style="color:{fg_color}; font-size:13px;">{fg_label}</span></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="pt-fg-bar"><div class="pt-fg-fill" style="width:{fg_val}%; background:{fg_color};"></div></div>', unsafe_allow_html=True)

    # -- News --
    st.markdown('<div class="pt-section">NEWS FEED</div>', unsafe_allow_html=True)
    fc1, fc2 = st.columns(2)
    filter_ticker = fc1.text_input("Filter ticker", placeholder="e.g. Hermes, Bitcoin, Fed...", key="filter_ticker", label_visibility="collapsed")
    filter_kw = fc2.text_input("Filter keyword", placeholder="keyword...", key="filter_kw", label_visibility="collapsed")
    filtered = all_news
    if filter_ticker.strip():
        ft = filter_ticker.strip().lower()
        filtered = [n for n in filtered if ft in n["title"].lower() or any(ft in t.lower() for t in n["tickers"])]
    if filter_kw.strip():
        fk = filter_kw.strip().lower()
        filtered = [n for n in filtered if fk in n["title"].lower()]
    brk = [n for n in filtered if n["breaking"]]
    nrm = [n for n in filtered if not n["breaking"]]
    for idx, item in enumerate((brk + nrm)[:25]):
        css = "pt-news-breaking" if item["breaking"] else "pt-news"
        badges = '<span class="pt-breaking-badge">BREAKING</span>' if item["breaking"] else ""
        for t in item.get("tickers", []):
            badges += f'<span class="pt-ticker-badge">{t}</span>'
        ago = time_ago(item["time"])
        st.markdown(f'<div class="{css}">{badges}<span style="color:#fff;">{item["title"]}</span><br/><span class="pt-source">{item["source"]}</span> <span class="pt-meta">{ago}</span></div>', unsafe_allow_html=True)
        if has_api_key and (item["breaking"] or item.get("tickers")):
            if st.button("Analyze", key=f"an_{idx}", type="secondary"):
                tk = item["tickers"][0] if item["tickers"] else None
                tpi = ""
                if tk:
                    for sym, d in all_prices.items():
                        if d["label"] == tk: tpi = f"Current price {d['label']}: {fmt_price(d['price'], sym)} ({d['change']:+.2f}%)"; break
                vix_p = all_prices.get("^VIX", {"price": 0})
                macro = f"BTC {all_prices.get('BTC-USD',{}).get('change',0):+.1f}%, SPY {all_prices.get('SPY',{}).get('change',0):+.1f}%, DXY {all_prices.get('DX-Y.NYB',{}).get('change',0):+.1f}%"
                user_msg = f'News{f" on {tk}" if tk else ""}:\n"{item["title"]}"\nSource: {item["source"]}, {ago}\n{tpi}\nMacro: {macro}. F&G: {fg_val} ({fg_label}), VIX: {vix_p["price"]:.1f}\nTrade idea? Direction, level, stop?'
                with st.spinner("Analyzing..."):
                    try:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                        resp = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=800,
                            system="Analyste financier senior. Direct, concis, actionnable. Niveaux precis. Francais.",
                            messages=[{"role": "user", "content": user_msg}])
                        st.markdown(f'<div class="pt-card">{resp.content[0].text}</div>', unsafe_allow_html=True)
                    except Exception as e: st.error(f"Error: {e}")

    @st.fragment(run_every=60)
    def auto_refresh_analyst():
        st.cache_data.clear()
    auto_refresh_analyst()

# =============================================
# TAB 3: PORTFOLIO
# =============================================

with tab_portfolio:
    from tab_portfolio import render_portfolio_tab
    pf_rows, pf_alerts, pf_total_value, pf_total_pnl = render_portfolio_tab()

# =============================================
# TAB 4: TRADE IDEAS
# =============================================

with tab_ideas:
    from tab_ideas import render_ideas_tab
    render_ideas_tab(portfolio_rows=pf_rows, all_prices=all_prices, fg=fg)

# =============================================
# TAB 5: PAPER TRADING
# =============================================

with tab_paper:
    GITHUB_RAW = "https://raw.githubusercontent.com/hectorm17/polymarket-tracker/main"

    @st.cache_data(ttl=60)
    def load_paper_csv():
        if Path("paper_trades.csv").exists(): return pd.read_csv("paper_trades.csv")
        try:
            import io
            r = requests.get(f"{GITHUB_RAW}/paper_trades.csv", timeout=10)
            if r.status_code == 200: return pd.read_csv(io.StringIO(r.text))
        except: pass
        return None

    @st.cache_data(ttl=60)
    def load_paper_state():
        if Path("monitor_state.json").exists():
            with open("monitor_state.json") as f: return _json.load(f)
        try:
            r = requests.get(f"{GITHUB_RAW}/monitor_state.json", timeout=10)
            if r.status_code == 200: return r.json()
        except: pass
        return None

    hdr1, hdr2 = st.columns([6, 1])
    hdr1.markdown(f'<div style="display:flex; align-items:center; gap:8px;"><span class="pt-dot" style="background:#f59e0b;"></span><span style="color:#fff; font-weight:500;">Paper Trading</span><span class="pt-meta">{datetime.now().strftime("%H:%M:%S")}</span></div>', unsafe_allow_html=True)
    if hdr2.button("Refresh", key="refresh_paper", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    df_paper = load_paper_csv()
    state_paper = load_paper_state()

    if df_paper is None or state_paper is None:
        st.markdown('<div class="pt-empty"><p style="font-size:14px;">Paper trading not started</p><p style="font-size:12px;">Run python3 live_monitor.py to begin</p></div>', unsafe_allow_html=True)
    else:
        bankroll = state_paper.get("bankroll", 1000)
        all_pt = state_paper.get("trades", [])
        resolved_pt = [t for t in all_pt if t.get("status") == "resolved"]
        pending_pt = [t for t in all_pt if t.get("status") == "pending"]
        total_pt_pnl = sum(t.get("pnl", 0) for t in resolved_pt)
        pt_wins = len([t for t in resolved_pt if t.get("pnl", 0) > 0])
        pt_losses = len(resolved_pt) - pt_wins
        pt_wr = (pt_wins / len(resolved_pt) * 100) if resolved_pt else 0
        pt_roi = (bankroll - 1000) / 1000 * 100
        start_t = state_paper.get("start_time", "")
        try:
            d = datetime.now() - datetime.fromisoformat(start_t)
            running = f"{d.days}d {d.seconds // 3600}h"
        except: running = "--"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Bankroll", f"${bankroll:,.2f}", f"{pt_roi:+.1f}%")
        m2.metric("Win Rate", f"{pt_wr:.1f}%", f"{pt_wins}W / {pt_losses}L")
        m3.metric("P&L", f"${total_pt_pnl:+,.2f}")
        m4.metric("Trades", f"{len(resolved_pt)} / {len(all_pt)}", f"Running {running}")

        # Go-live indicator
        if len(resolved_pt) >= 20 and pt_wr > 75:
            st.markdown('<div class="pt-signal pt-signal-on"><span class="pt-dot" style="background:#10b981;"></span><span style="font-weight:600; font-size:13px;">READY FOR LIVE -- Win rate {:.1f}% on {} trades</span></div>'.format(pt_wr, len(resolved_pt)), unsafe_allow_html=True)
        elif len(resolved_pt) >= 20:
            st.markdown(f'<div class="pt-signal pt-signal-off"><span class="pt-dot" style="background:#ef4444;"></span><span style="font-weight:600; font-size:13px;">Win rate {pt_wr:.1f}% below 75% -- continue paper trading</span></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="pt-signal pt-signal-neutral"><span class="pt-dot" style="background:#f59e0b;"></span><span style="font-weight:600; font-size:13px;">{len(resolved_pt)}/20 resolved trades before evaluation</span></div>', unsafe_allow_html=True)

        # Equity curve
        if resolved_pt:
            st.markdown('<div class="pt-section">EQUITY CURVE</div>', unsafe_allow_html=True)
            sorted_r = sorted(resolved_pt, key=lambda t: t.get("timestamp", ""))
            cumul = [1000]
            for t in sorted_r: cumul.append(cumul[-1] + t.get("pnl", 0))
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=list(range(len(cumul))), y=cumul, mode="lines+markers",
                line=dict(color="#4f6ef7", width=2), marker=dict(size=3, color="#4f6ef7"),
                hovertemplate="Trade #%{x}<br>$%{y:,.2f}<extra></extra>"))
            fig.add_hline(y=1000, line_dash="dash", line_color="#374151")
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6b7280", family="Inter"),
                xaxis=dict(title="Trade #", gridcolor="#1e2130", zeroline=False),
                yaxis=dict(title="Bankroll ($)", gridcolor="#1e2130", zeroline=False))
            st.plotly_chart(fig, use_container_width=True)

        # Pending
        st.markdown(f'<div class="pt-section">PENDING ({len(pending_pt)})</div>', unsafe_allow_html=True)
        if pending_pt:
            rows = [{"City": t.get("city",""), "Date": t.get("date",""), "Target": t.get("target",""),
                     "Forecast": f"{t.get('forecast','')}C", "Market": f"{t.get('mkt_price',0):.1%}",
                     "Edge": f"{t.get('edge',0):+.3f}", "Signal": t.get("signal",""), "Stake": f"${t.get('stake',0):.0f}"}
                    for t in sorted(pending_pt, key=lambda x: x.get("date",""))]
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.markdown('<p class="pt-meta">No pending trades</p>', unsafe_allow_html=True)

        # Resolved
        st.markdown(f'<div class="pt-section">RESOLVED ({len(resolved_pt)})</div>', unsafe_allow_html=True)
        if resolved_pt:
            rows = []
            for t in reversed(sorted(resolved_pt, key=lambda x: x.get("timestamp",""))):
                pv = t.get("pnl", 0)
                rows.append({"Status": "WIN" if pv > 0 else "LOSS", "City": t.get("city",""), "Date": t.get("date",""),
                             "Target": t.get("target",""), "Signal": t.get("signal",""),
                             "Actual": f"{t.get('real_temp','?')}C", "Result": t.get("result",""),
                             "P&L": f"${pv:+.2f}", "Stake": f"${t.get('stake',0):.0f}"})
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.markdown('<p class="pt-meta">No resolved trades yet</p>', unsafe_allow_html=True)

        # By city
        if resolved_pt:
            st.markdown('<div class="pt-section">PERFORMANCE BY CITY</div>', unsafe_allow_html=True)
            cs = {}
            for t in resolved_pt:
                c = t.get("city", "?")
                if c not in cs: cs[c] = {"w": 0, "l": 0, "pnl": 0}
                if t.get("pnl", 0) > 0: cs[c]["w"] += 1
                else: cs[c]["l"] += 1
                cs[c]["pnl"] += t.get("pnl", 0)
            rows = [{"City": c, "Trades": s["w"]+s["l"], "W/L": f"{s['w']}/{s['l']}",
                     "Win Rate": f"{s['w']/(s['w']+s['l'])*100:.0f}%", "P&L": f"${s['pnl']:+.2f}"}
                    for c, s in sorted(cs.items(), key=lambda x: -x[1]["pnl"])]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    @st.fragment(run_every=60)
    def auto_refresh_paper():
        pass
    auto_refresh_paper()

# =============================================
# TAB 4: LIVE FEED
# =============================================

with tab_live:
    WHALE_THRESHOLD = 500
    now_str_live = datetime.now().strftime("%H:%M:%S")

    st.markdown(f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;"><span class="pt-dot" style="background:#10b981; animation:pulse 2s infinite;"></span><span style="color:#fff; font-weight:500;">Live Feed</span><span class="pt-meta">{now_str_live} -- 30s refresh</span></div>', unsafe_allow_html=True)

    all_feed, whale_alerts, lb_data = [], [], []
    tracked = st.session_state.tracked_whales

    for w in tracked:
        addr, label = w["addr"], w["label"]
        tl = fetch_whale_trades(addr)
        val = fetch_whale_value(addr)
        pnl = fetch_whale_pnl(addr)
        spec = detect_specialty(tl)
        for t in tl:
            usdc = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
            t["_label"], t["_addr"], t["_fav"], t["_usdc"] = label, addr, w.get("fav", False), usdc
            all_feed.append(t)
            if usdc >= WHALE_THRESHOLD: whale_alerts.append(t)
        last_ts = tl[0].get("timestamp", 0) if tl else 0
        lb_data.append({"fav": w.get("fav", False), "addr": addr, "label": label, "pnl": pnl, "value": val,
                        "specialty": spec, "last": datetime.fromtimestamp(last_ts).strftime("%H:%M") if last_ts else "--",
                        "trades": len(tl)})

    # Whale alerts
    if whale_alerts:
        whale_alerts.sort(key=lambda t: t.get("timestamp", 0), reverse=True)
        for t in whale_alerts[:5]:
            side = (t.get("side") or "BUY").upper()
            sc = "pt-side-buy" if side == "BUY" else "pt-side-sell"
            st.markdown(f'<div class="pt-row" style="background:#131620; border:1px solid #1e2130; border-radius:8px; margin-bottom:4px;"><span class="pt-whale-badge">WHALE ${t["_usdc"]:,.0f}</span><span class="{sc}">{side}</span><span class="pt-addr">{t["_label"]}</span><span class="pt-title">{t.get("title","?")[:55]}</span></div>', unsafe_allow_html=True)
        st.markdown('<div style="border-top:1px solid #1e2130; margin:12px 0;"></div>', unsafe_allow_html=True)

    # Leaderboard
    st.markdown('<div class="pt-section">LEADERBOARD</div>', unsafe_allow_html=True)
    lb_data.sort(key=lambda x: (-x["fav"], -x["pnl"]))

    # Header row
    st.markdown('''<div style="display:grid; grid-template-columns:40px 2fr 120px 120px 100px 1fr;
                padding:8px 16px; color:#374151; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; font-weight:600;">
        <div>#</div><div>WALLET</div><div style="text-align:right;">PROFIT</div>
        <div style="text-align:right;">VALUE</div><div style="text-align:right;">TRADES</div><div style="text-align:right;">SPECIALTY</div>
    </div>''', unsafe_allow_html=True)

    for i, d in enumerate(lb_data):
        rank = i + 1
        rc = {1: "#c9a84c", 2: "#888", 3: "#a0522d"}.get(rank, "#374151")
        pc = "#10b981" if d["pnl"] >= 0 else "#ef4444"
        sign = "+" if d["pnl"] >= 0 else ""
        whale = '<span class="pt-whale-badge" style="margin-left:6px;">Whale</span>' if d["pnl"] > 100000 else ""
        fav_mark = '<span style="color:#c9a84c; margin-right:4px;">*</span>' if d["fav"] else ""
        st.markdown(f'''<div style="display:grid; grid-template-columns:40px 2fr 120px 120px 100px 1fr;
            align-items:center; padding:14px 16px; border-bottom:1px solid #131620; transition:background 0.1s;"
            onmouseover="this.style.background='#131620'" onmouseout="this.style.background='transparent'">
            <div style="color:{rc}; font-size:14px; font-weight:600;">{rank}</div>
            <div>{fav_mark}<span class="pt-addr">{short_addr(d["addr"])}</span>{whale}<div style="color:#6b7280; font-size:12px; margin-top:2px;">{d["label"]}</div></div>
            <div style="color:{pc}; font-weight:500; text-align:right;">{sign}${d["pnl"]:,.0f}</div>
            <div style="color:#6b7280; text-align:right;">${d["value"]:,.0f}</div>
            <div style="color:#6b7280; text-align:right;">{d["trades"]}</div>
            <div style="color:#374151; text-align:right; font-size:12px;">{d["specialty"]}</div>
        </div>''', unsafe_allow_html=True)

    # Live feed
    st.markdown('<div class="pt-section">RECENT TRADES</div>', unsafe_allow_html=True)
    all_feed.sort(key=lambda t: t.get("timestamp", 0), reverse=True)
    for t in all_feed[:30]:
        ts = t.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"
        side = (t.get("side") or "BUY").upper()
        sc = "pt-side-buy" if side == "BUY" else "pt-side-sell"
        usdc = t["_usdc"]
        ac = "pt-amount-green" if side == "BUY" else "pt-amount-red"
        st.markdown(f'<div class="pt-row"><span class="pt-time">{time_str}</span><span class="pt-addr">{short_addr(t["_addr"])}</span><span class="{sc}">{side}</span><span class="pt-title">{t.get("title","?")[:55]}</span><span class="{ac}">${usdc:,.0f}</span></div>', unsafe_allow_html=True)

    # Wallet Discovery
    st.markdown('<div class="pt-section">WALLET DISCOVERY</div>', unsafe_allow_html=True)
    with st.form("discover_wallet", clear_on_submit=True):
        dc = st.columns([4, 1])
        disc_addr = dc[0].text_input("Address", placeholder="0x...", label_visibility="collapsed")
        disc_btn = dc[1].form_submit_button("Search", use_container_width=True)

    if disc_btn and disc_addr.strip():
        a = disc_addr.strip().lower()
        with st.spinner("Searching..."):
            dt, dp, dv, ds = fetch_whale_trades(a), fetch_whale_pnl(a), fetch_whale_value(a), detect_specialty(fetch_whale_trades(a))
        st.markdown(f'<div style="margin:8px 0;"><span class="pt-addr">{short_addr(a)}</span> <span class="pt-meta">-- {ds}</span></div>', unsafe_allow_html=True)
        d1, d2, d3 = st.columns(3)
        d1.metric("P&L", f"${dp:+,.0f}")
        d2.metric("Value", f"${dv:,.0f}")
        d3.metric("Recent Trades", len(dt))
        if dt:
            for t in dt[:5]:
                side = (t.get("side") or "BUY").upper()
                sc = "pt-side-buy" if side == "BUY" else "pt-side-sell"
                usdc = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
                st.markdown(f'<div class="pt-row"><span class="{sc}">{side}</span><span style="color:#6b7280;">{t.get("outcome","")}</span><span class="pt-title">{t.get("title","?")[:50]}</span><span class="pt-amount">${usdc:,.0f}</span></div>', unsafe_allow_html=True)
        already = any(w["addr"] == a for w in st.session_state.tracked_whales)
        if not already:
            lbl = st.text_input("Label", value=short_addr(a), key="disc_label")
            if st.button("Add to tracking", key="add_disc"):
                st.session_state.tracked_whales.append({"addr": a, "label": lbl, "fav": False})
                st.cache_data.clear()
                st.rerun()
        else:
            st.markdown('<p class="pt-meta">Already tracked</p>', unsafe_allow_html=True)

    # Manage
    with st.expander("Manage tracked wallets"):
        for i, w in enumerate(st.session_state.tracked_whales):
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            c1.text(w["label"])
            c2.markdown(f'<span class="pt-addr">{short_addr(w["addr"])}</span>', unsafe_allow_html=True)
            if c3.button("Fav" if not w["fav"] else "Unfav", key=f"fav_{i}", use_container_width=True):
                st.session_state.tracked_whales[i]["fav"] = not w["fav"]
                st.rerun()
            if c4.button("Del", key=f"rm_w_{i}", use_container_width=True):
                st.session_state.tracked_whales.pop(i)
                st.rerun()

    # Export
    if st.button("Export CSV", key="export_csv", use_container_width=True):
        exp = [{"timestamp": datetime.fromtimestamp(t.get("timestamp",0)).isoformat() if t.get("timestamp") else "",
                "wallet": t.get("_label",""), "address": t.get("_addr",""), "side": t.get("side",""),
                "outcome": t.get("outcome",""), "market": t.get("title",""),
                "size": t.get("size",0), "price": t.get("price",0), "usdc": t.get("_usdc",0)} for t in all_feed]
        st.download_button("Download", pd.DataFrame(exp).to_csv(index=False), "trades_export.csv", "text/csv")

    @st.fragment(run_every=30)
    def auto_refresh_live():
        st.cache_data.clear()
    auto_refresh_live()
