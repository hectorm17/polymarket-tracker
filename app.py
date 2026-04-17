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

# ── Fix SSL for RSS feeds ──
ssl._create_default_https_context = ssl._create_unverified_context

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

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
    "^FCHI": "CAC 40", "MC.PA": "LVMH", "RMS.PA": "Hermès", "AIR.PA": "Airbus", "TTE.PA": "TotalEnergies",
    "EURUSD=X": "EUR/USD", "DX-Y.NYB": "DXY", "JPY=X": "USD/JPY",
    "^VIX": "VIX", "^TNX": "US 10Y", "GC=F": "Or", "CL=F": "Pétrole WTI",
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
    "hermès", "hermes", "lvmh", "apple", "nvidia", "tesla", "airbus", "total",
    "bitcoin", "btc", "ethereum", "eth", "solana",
    "fed", "bce", "ecb", "powell", "lagarde",
    "trump", "tarif", "tariff", "inflation", "cpi", "recession",
    "taux", "rate", "war", "guerre", "sanctions", "iran", "china", "chine",
    "crash", "rallye", "rally", "sell-off", "selloff", "plunge", "surge",
    "vix", "volatil", "pétrole", "oil", "gold", "or ",
]

# ─────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────

@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

db = get_supabase()

# ─────────────────────────────────────────────
# DATA FETCHERS — POLYMARKET
# ─────────────────────────────────────────────

DATA_API = "https://data-api.polymarket.com"
LB_API = "https://lb-api.polymarket.com"

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

# ─────────────────────────────────────────────
# DATA FETCHERS — MARKET
# ─────────────────────────────────────────────

@st.cache_data(ttl=60)
def fetch_all_prices():
    import yfinance as yf
    results = {}
    all_syms = [s for group in TICKERS.values() for s in group]
    for sym in all_syms:
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
                title_lower = title.lower()
                is_breaking = any(kw in title_lower for kw in BREAKING_KEYWORDS)
                matched = [lbl for lbl in TICKER_LABELS.values() if lbl.lower() in title_lower or lbl.upper() in title]
                items.append({"title": title, "source": source, "link": e.get("link", ""), "time": ts, "breaking": is_breaking, "tickers": matched})
        except Exception:
            continue
    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items

# ─────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────

def load_wallets():
    res = db.table("wallets").select("*").order("created_at").execute()
    return res.data

def add_wallet(address, label):
    db.table("wallets").insert({"address": address.lower(), "label": label}).execute()

def remove_wallet(address):
    db.table("wallets").delete().eq("address", address.lower()).execute()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def time_ago(ts):
    if not ts:
        return ""
    delta = datetime.now(timezone.utc) - ts
    mins = int(delta.total_seconds() / 60)
    if mins < 0:
        return "à l'instant"
    if mins < 60:
        return f"il y a {mins} min"
    if mins < 1440:
        return f"il y a {mins // 60}h"
    return f"il y a {mins // 1440}j"

def fmt_price(p, sym=""):
    if "JPY" in sym:
        return f"¥{p:,.2f}"
    if "EUR" in sym and "=" in sym:
        return f"${p:.4f}"
    if "TNX" in sym:
        return f"{p:.2f}%"
    if "VIX" in sym:
        return f"{p:.1f}"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    return f"${p:.4f}"

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────

for key, default in {
    "alerts": [],
    "last_trade_ts": {},
    "alert_threshold": 100.0,
    "analyses": [],
    "daily_summaries": [],
    "auto_analysis_done": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────
# PAGE CONFIG + STYLES
# ─────────────────────────────────────────────

st.set_page_config(page_title="Polymarket Tracker", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    div[data-testid="stMetric"] {
        background: #111827; border: 1px solid #1f2937;
        border-radius: 0.75rem; padding: 0.75rem;
    }
    div[data-testid="stMetric"] label { color: #9ca3af; font-size: 0.8rem; }
    .alert-item { padding: 0.5rem 0.75rem; border-left: 3px solid #6366f1; background: #111827; border-radius: 0 0.5rem 0.5rem 0; margin-bottom: 0.4rem; font-size: 0.85rem; }
    .alert-time { color: #6b7280; font-size: 0.75rem; }
    .news-item { padding: 0.6rem 0.75rem; border-bottom: 1px solid #1f2937; font-size: 0.9rem; }
    .news-breaking { padding: 0.6rem 0.75rem; border-left: 3px solid #ef4444; background: #1c0a0a; border-bottom: 1px solid #1f2937; font-size: 0.9rem; }
    .news-source { color: #6366f1; font-size: 0.75rem; font-weight: 600; }
    .news-time { color: #6b7280; font-size: 0.75rem; }
    .badge-breaking { display: inline-block; background: #dc2626; color: white; font-size: 0.65rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.25rem; margin-right: 0.4rem; vertical-align: middle; }
    .badge-ticker { display: inline-block; background: #312e81; color: #a5b4fc; font-size: 0.65rem; font-weight: 600; padding: 0.1rem 0.4rem; border-radius: 0.25rem; margin-right: 0.3rem; vertical-align: middle; }
    .signal-box { padding: 1.2rem 1.5rem; border-radius: 0.75rem; font-size: 1.3rem; font-weight: 700; text-align: center; margin: 0.5rem 0 1rem 0; }
    .signal-risk-off { background: #450a0a; border: 1px solid #dc2626; color: #fca5a5; }
    .signal-risk-on { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
    .signal-neutral { background: #1c1917; border: 1px solid #a16207; color: #fde68a; }
    .fg-bar-bg { background: #1f2937; border-radius: 0.5rem; height: 1.2rem; width: 100%; overflow: hidden; }
    .fg-bar-fill { height: 100%; border-radius: 0.5rem; transition: width 0.5s; }
    .analysis-box { background: #0f172a; border: 1px solid #1e3a5f; border-radius: 0.75rem; padding: 1.5rem; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

st.title("📊 Polymarket Wallet Tracker")

# ─────────────────────────────────────────────
# MAIN TABS
# ─────────────────────────────────────────────

main_tab_wallets, main_tab_analyst, main_tab_paper = st.tabs(["💰 Wallets", "🤖 Analyste IA", "🟡 Paper Trading"])

# ═════════════════════════════════════════════
# TAB 1: WALLETS
# ═════════════════════════════════════════════

with main_tab_wallets:
    st.caption("Track positions, trades & P&L across proxy wallets")

    with st.expander("🔔 Alerts", expanded=bool(st.session_state.alerts)):
        st.session_state.alert_threshold = st.number_input(
            "Min trade size (USDC)", min_value=0.0, value=st.session_state.alert_threshold,
            step=10.0, help="Only alert for trades above this amount",
        )
        if st.session_state.alerts:
            for a in st.session_state.alerts:
                st.markdown(f'<div class="alert-item">{a["msg"]}<br/><span class="alert-time">{a["time"]}</span></div>', unsafe_allow_html=True)
        else:
            st.caption("No alerts yet — new trades will appear here.")

    with st.form("add_wallet", clear_on_submit=True):
        cols = st.columns([3, 2, 1])
        address = cols[0].text_input("Wallet address", placeholder="0x...", label_visibility="collapsed")
        label = cols[1].text_input("Label", placeholder="Label (optional)", label_visibility="collapsed")
        submitted = cols[2].form_submit_button("➕ Add", use_container_width=True)

    if submitted and address.strip():
        addr = address.strip()
        lbl = label.strip() or (addr[:6] + "..." + addr[-4:])
        try:
            add_wallet(addr, lbl)
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            if "duplicate" in str(e).lower() or "23505" in str(e):
                st.warning("This wallet is already tracked.")
            else:
                st.error(f"Error: {e}")

    wallets = load_wallets()

    if not wallets:
        st.divider()
        st.markdown('<div style="text-align:center; padding:4rem 0; color:#6b7280;"><p style="font-size:1.2rem;">No wallets tracked yet</p><p>Add a Polymarket proxy wallet address above to get started</p></div>', unsafe_allow_html=True)
    else:
        for wallet in wallets:
            addr = wallet["address"]
            lbl = wallet["label"]
            short = addr[:6] + "..." + addr[-4:]
            st.divider()
            hcol1, hcol2 = st.columns([6, 1])
            hcol1.subheader(lbl)
            hcol1.caption(f"`{short}`")
            if hcol2.button("🗑️ Remove", key=f"rm_{addr}", use_container_width=True):
                remove_wallet(addr)
                st.cache_data.clear()
                st.rerun()
            try:
                positions = fetch_positions(addr)
                trades = fetch_trades(addr)
                total_pnl = fetch_pnl(addr)
            except Exception as e:
                st.error(f"Failed to fetch data for {lbl}: {e}")
                continue
            today = date.today()
            active_positions, closed_positions = [], []
            for p in positions:
                end = p.get("endDate")
                try:
                    is_active = date.fromisoformat(end) >= today if end else True
                except (ValueError, TypeError):
                    is_active = True
                (active_positions if is_active else closed_positions).append(p)
            wins = [p for p in closed_positions if float(p.get("cashPnl", 0)) + float(p.get("realizedPnl", 0)) > 0]
            win_rate = (len(wins) / len(closed_positions) * 100) if closed_positions else 0
            mcol1, mcol2, mcol3 = st.columns(3)
            mcol1.metric("Total P&L", f"${total_pnl:+,.2f}")
            mcol2.metric("Win Rate", f"{win_rate:.1f}%")
            mcol3.metric("Open Positions", len(active_positions))
            tab_pos, tab_trades = st.tabs([f"📈 Positions ({len(active_positions)})", f"🔄 Trades ({len(trades)})"])
            with tab_pos:
                if not active_positions:
                    st.info("No open positions")
                else:
                    rows = [{"Market": p.get("title", "?"), "Side": (p.get("outcome") or "Yes").upper(), "Size": float(p.get("size", 0)), "Avg Price": float(p.get("avgPrice", 0)), "Cur Price": float(p.get("curPrice", 0)), "P&L ($)": float(p.get("cashPnl", 0))} for p in active_positions]
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={"Size": st.column_config.NumberColumn(format="%.2f"), "Avg Price": st.column_config.NumberColumn(format="%.3f"), "Cur Price": st.column_config.NumberColumn(format="%.3f"), "P&L ($)": st.column_config.NumberColumn(format="%+.2f")})
            with tab_trades:
                if not trades:
                    st.info("No recent trades")
                else:
                    rows = [{"Market": t.get("title", "—"), "Side": (t.get("side") or "BUY").upper(), "Size": float(t.get("size", 0)), "Price": float(t.get("price", 0)), "Time": datetime.fromtimestamp(t["timestamp"]).strftime("%Y-%m-%d %H:%M") if t.get("timestamp") else "—"} for t in trades]
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={"Size": st.column_config.NumberColumn(format="%.2f"), "Price": st.column_config.NumberColumn(format="%.3f")})

        @st.fragment(run_every=60)
        def poll_alerts():
            wlist = load_wallets()
            if not wlist:
                return
            threshold = st.session_state.alert_threshold
            new_alerts = []
            for w in wlist:
                a, l = w["address"], w["label"]
                try:
                    recent = fetch_recent_trades(a)
                except Exception:
                    continue
                if not recent:
                    continue
                last_known = st.session_state.last_trade_ts.get(a, 0)
                if last_known == 0:
                    st.session_state.last_trade_ts[a] = recent[0].get("timestamp", 0)
                    continue
                for t in recent:
                    ts = t.get("timestamp", 0)
                    if ts <= last_known:
                        break
                    usdc = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
                    if usdc < threshold:
                        continue
                    msg = f'🚨 **{l}** — {(t.get("side") or "BUY").upper()} {float(t.get("size",0)):.1f} shares "{t.get("title","?")}" à {float(t.get("price",0))*100:.0f}¢'
                    new_alerts.append({"msg": msg, "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), "ts": ts})
                    st.toast(msg, icon="🚨")
                latest = recent[0].get("timestamp", 0)
                if latest > last_known:
                    st.session_state.last_trade_ts[a] = latest
            if new_alerts:
                st.session_state.alerts = (new_alerts + st.session_state.alerts)[:20]
        poll_alerts()
        st.divider()
        if st.button("🔄 Refresh all data", use_container_width=True, key="refresh_wallets"):
            st.cache_data.clear()
            st.rerun()

# ═════════════════════════════════════════════
# TAB 2: ANALYSTE IA
# ═════════════════════════════════════════════

with main_tab_analyst:

    now_str = datetime.now().strftime("%H:%M:%S")

    # ── Fetch all data upfront ──
    all_prices = fetch_all_prices()
    fg = fetch_fear_greed()
    all_news = fetch_all_news()

    has_api_key = "ANTHROPIC_API_KEY" in st.secrets

    # ── Build context for AI ──
    def build_full_context():
        lines = ["DONNÉES MARCHÉ :"]
        for cat, syms in TICKERS.items():
            for sym in syms:
                d = all_prices.get(sym, {"price": 0, "change": 0, "label": sym})
                lines.append(f"  {d['label']}: {fmt_price(d['price'], sym)} ({d['change']:+.2f}%)")
        # Macro indicators block
        vix = all_prices.get("^VIX", {"price": 0, "change": 0})
        tnx = all_prices.get("^TNX", {"price": 0, "change": 0})
        gold = all_prices.get("GC=F", {"price": 0, "change": 0})
        oil = all_prices.get("CL=F", {"price": 0, "change": 0})
        dxy = all_prices.get("DX-Y.NYB", {"price": 0, "change": 0})
        lines.append(f"\nINDICATEURS MACRO :")
        lines.append(f"  VIX : {vix['price']:.1f} ({vix['change']:+.2f}%)")
        lines.append(f"  Taux 10 ans US : {tnx['price']:.2f}% ({tnx['change']:+.2f}%)")
        lines.append(f"  Or : ${gold['price']:,.0f} ({gold['change']:+.2f}%)")
        lines.append(f"  Pétrole WTI : ${oil['price']:.2f} ({oil['change']:+.2f}%)")
        lines.append(f"  DXY : {dxy['price']:.2f} ({dxy['change']:+.2f}%)")
        lines.append(f"  Fear & Greed : {fg['value']} — {fg['label']}")
        breaking = [n for n in all_news if n["breaking"]]
        normal = [n for n in all_news if not n["breaking"]]
        top_news = (breaking + normal)[:10]
        lines.append(f"\nDERNIÈRES NEWS ({len(top_news)}) :")
        for n in top_news:
            prefix = "[BREAKING] " if n["breaking"] else ""
            lines.append(f"  - {prefix}[{n['source']}] {n['title']} ({time_ago(n['time'])})")
        return "\n".join(lines)

    SYSTEM_PROMPT = """Tu es un analyste financier senior. Tu couvres crypto, actions US et EU, forex et macro.

À partir des données marché et news fournies, génère systématiquement :
1. SIGNAL global (RISK ON 🟢 / RISK OFF 🔴 / NEUTRE ⚠️) en une phrase
2. CONTEXTE macro en 2 phrases max
3. 2 à 3 OPPORTUNITÉS ou ALERTES concrètes sur des actifs spécifiques, chacune avec :
   - Le nom de l'actif et sa variation
   - 1 phrase de justification basée sur les données
   - 1 recommandation directe (long / short / attendre / surveiller)

Sois direct et actionnable. Pas de disclaimer. Pas de 'il faudrait considérer'. Réponds en français."""

    def run_global_analysis():
        """Run Claude analysis and store in session_state."""
        context = build_full_context()
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"{context}\n\nGénère ton analyse."}],
            )
            text = resp.content[0].text
            st.session_state.analyses.append({"time": now_str, "text": text})
            st.session_state.analyses = st.session_state.analyses[-24:]
            return text
        except Exception as e:
            return f"Erreur Claude API : {e}"

    # ──────────────────────────────────────────
    # SECTION 1: ANALYSE IA (TOP OF PAGE)
    # ──────────────────────────────────────────

    st.markdown("#### 🤖 Analyse IA")

    if not has_api_key:
        st.warning("Ajoute `ANTHROPIC_API_KEY` dans tes secrets Streamlit pour activer l'analyse IA.")
    else:
        # Auto-generate on first load
        if not st.session_state.auto_analysis_done and not st.session_state.analyses:
            with st.spinner("Génération de l'analyse initiale..."):
                run_global_analysis()
            st.session_state.auto_analysis_done = True

        # Display latest analysis
        if st.session_state.analyses:
            latest = st.session_state.analyses[-1]
            text = latest["text"]
            text_lower = text.lower()

            # Signal box
            if "risk off" in text_lower:
                st.markdown('<div class="signal-box signal-risk-off">🔴 RISK OFF</div>', unsafe_allow_html=True)
            elif "risk on" in text_lower:
                st.markdown('<div class="signal-box signal-risk-on">🟢 RISK ON</div>', unsafe_allow_html=True)
            elif "neutre" in text_lower:
                st.markdown('<div class="signal-box signal-neutral">⚠️ NEUTRE</div>', unsafe_allow_html=True)

            st.markdown(f'<div class="analysis-box">', unsafe_allow_html=True)
            st.caption(f"Générée à {latest['time']}")
            st.markdown(text)
            st.markdown('</div>', unsafe_allow_html=True)

        # Regenerate button
        acol1, acol2 = st.columns(2)
        if acol1.button("🔄 Régénérer l'analyse", use_container_width=True, key="regen_analysis"):
            with st.spinner("Claude analyse les marchés..."):
                run_global_analysis()
            st.rerun()
        if acol2.button("📋 Résumé du jour", use_container_width=True, key="gen_summary"):
            if not st.session_state.analyses:
                st.warning("Aucune analyse à résumer.")
            else:
                all_a = "\n\n---\n\n".join(f"[{a['time']}]\n{a['text']}" for a in st.session_state.analyses)
                with st.spinner("Génération du résumé..."):
                    try:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                        resp = client.messages.create(
                            model="claude-sonnet-4-20250514", max_tokens=1500,
                            system="Tu es un analyste macro senior. Produis un résumé quotidien : 1) SYNTHÈSE DU JOUR 2) ÉVÉNEMENTS CLÉS 3) ÉVOLUTION DU SENTIMENT 4) SIGNAL DE FIN DE JOURNÉE. Réponds en français.",
                            messages=[{"role": "user", "content": f"Analyses de la journée :\n\n{all_a}"}],
                        )
                        st.session_state.daily_summaries.append({"date": date.today().isoformat(), "time": now_str, "text": resp.content[0].text})
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur : {e}")

        if st.session_state.daily_summaries:
            for s in reversed(st.session_state.daily_summaries):
                with st.expander(f"📋 Résumé du {s['date']} ({s['time']})"):
                    st.markdown(s["text"])

    st.divider()

    # ──────────────────────────────────────────
    # SECTION 2: LIVE HEADER + PRICES
    # ──────────────────────────────────────────

    hdr1, hdr2 = st.columns([6, 1])
    hdr1.markdown(f"🔴 **LIVE** &nbsp; Dernière MAJ : `{now_str}`")
    if hdr2.button("⟳ Refresh", key="refresh_analyst", use_container_width=True):
        st.cache_data.clear()
        st.session_state.auto_analysis_done = False
        st.rerun()

    # Prices by category
    cat_tabs = st.tabs(list(TICKERS.keys()))
    for cat_tab, (cat_name, syms) in zip(cat_tabs, TICKERS.items()):
        with cat_tab:
            cols = st.columns(len(syms))
            for col, sym in zip(cols, syms):
                d = all_prices.get(sym, {"price": 0, "change": 0, "label": sym})
                col.metric(d["label"], fmt_price(d["price"], sym), f"{d['change']:+.2f}%")

    # Fear & Greed
    fg_val = fg["value"]
    fg_label = fg["label"]
    if fg_val <= 25:
        fg_color, fg_emoji = "#dc2626", "😨"
    elif fg_val <= 45:
        fg_color, fg_emoji = "#f97316", "😟"
    elif fg_val <= 55:
        fg_color, fg_emoji = "#eab308", "😐"
    elif fg_val <= 75:
        fg_color, fg_emoji = "#22c55e", "😊"
    else:
        fg_color, fg_emoji = "#16a34a", "🤑"

    st.markdown(f"**Fear & Greed** : {fg_val} — {fg_label} {fg_emoji}")
    st.markdown(f'<div class="fg-bar-bg"><div class="fg-bar-fill" style="width:{fg_val}%; background:{fg_color};"></div></div>', unsafe_allow_html=True)

    st.divider()

    # ──────────────────────────────────────────
    # SECTION 3: NEWS FEED
    # ──────────────────────────────────────────

    st.markdown("#### 📰 Flux news live")

    fcol1, fcol2 = st.columns(2)
    filter_ticker = fcol1.text_input("Filtre ticker", placeholder="ex: Hermès, Bitcoin, Fed...", key="filter_ticker", label_visibility="collapsed")
    filter_kw = fcol2.text_input("Filtre mot-clé", placeholder="mot-clé...", key="filter_kw", label_visibility="collapsed")

    filtered_news = all_news
    if filter_ticker.strip():
        ft = filter_ticker.strip().lower()
        filtered_news = [n for n in filtered_news if ft in n["title"].lower() or any(ft in t.lower() for t in n["tickers"])]
    if filter_kw.strip():
        fk = filter_kw.strip().lower()
        filtered_news = [n for n in filtered_news if fk in n["title"].lower()]

    breaking = [n for n in filtered_news if n["breaking"]]
    normal = [n for n in filtered_news if not n["breaking"]]

    for idx, item in enumerate((breaking + normal)[:25]):
        is_brk = item["breaking"]
        css_class = "news-breaking" if is_brk else "news-item"
        badges = ""
        if is_brk:
            badges += '<span class="badge-breaking">BREAKING</span>'
        for t in item.get("tickers", []):
            badges += f'<span class="badge-ticker">{t}</span>'
        ago = time_ago(item["time"])

        st.markdown(f'<div class="{css_class}">{badges}{item["title"]}<br/><span class="news-source">{item["source"]}</span> · <span class="news-time">{ago}</span></div>', unsafe_allow_html=True)

        # Per-article AI analysis button
        if has_api_key and (is_brk or item.get("tickers")):
            if st.button("🧠 Analyser ce titre IA", key=f"analyze_news_{idx}", type="secondary"):
                ticker_match = item["tickers"][0] if item["tickers"] else None
                ticker_price_info = ""
                if ticker_match:
                    for sym, d in all_prices.items():
                        if d["label"] == ticker_match:
                            ticker_price_info = f"Prix actuel {d['label']}: {fmt_price(d['price'], sym)} ({d['change']:+.2f}%)"
                            break
                vix_info = all_prices.get("^VIX", {"price": 0})
                fg_summary = f"Fear & Greed: {fg_val} ({fg_label}), VIX: {vix_info['price']:.1f}"
                macro_summary = f"BTC {all_prices.get('BTC-USD',{}).get('change',0):+.1f}%, SPY {all_prices.get('SPY',{}).get('change',0):+.1f}%, DXY {all_prices.get('DX-Y.NYB',{}).get('change',0):+.1f}%"

                user_msg = f"""News détectée{f' sur {ticker_match}' if ticker_match else ''} :
"{item['title']}"
Source : {item['source']}, publié {ago}
{ticker_price_info}
Contexte macro : {macro_summary}. {fg_summary}.
Question : trade à envisager ? Si oui, dans quel sens, à quel niveau, quel stop ?"""

                with st.spinner(f"Analyse en cours..."):
                    try:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                        resp = client.messages.create(
                            model="claude-sonnet-4-20250514", max_tokens=800,
                            system="Tu es un analyste financier senior couvrant crypto, actions US/EU et forex. Sois direct, concis, actionnable. Pas de disclaimer. Donne des niveaux précis si pertinent. Réponds en français.",
                            messages=[{"role": "user", "content": user_msg}],
                        )
                        st.info(resp.content[0].text)
                    except Exception as e:
                        st.error(f"Erreur : {e}")

    # ── Auto-refresh every 60s ──
    @st.fragment(run_every=60)
    def auto_refresh_analyst():
        st.cache_data.clear()
    auto_refresh_analyst()

# ═════════════════════════════════════════════
# TAB 3: PAPER TRADING
# ═════════════════════════════════════════════

with main_tab_paper:

    GITHUB_RAW = "https://raw.githubusercontent.com/hectorm17/polymarket-tracker/main"
    PAPER_CSV_LOCAL = Path("paper_trades.csv")
    PAPER_STATE_LOCAL = Path("monitor_state.json")

    @st.cache_data(ttl=60)
    def load_paper_csv():
        # Try local first (when running locally alongside live_monitor)
        if PAPER_CSV_LOCAL.exists():
            return pd.read_csv(PAPER_CSV_LOCAL)
        # Fallback: fetch from GitHub
        try:
            import io
            r = requests.get(f"{GITHUB_RAW}/paper_trades.csv", timeout=10)
            if r.status_code == 200:
                return pd.read_csv(io.StringIO(r.text))
        except Exception:
            pass
        return None

    @st.cache_data(ttl=60)
    def load_paper_state():
        if PAPER_STATE_LOCAL.exists():
            with open(PAPER_STATE_LOCAL) as f:
                return _json.load(f)
        try:
            r = requests.get(f"{GITHUB_RAW}/monitor_state.json", timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    hdr1, hdr2 = st.columns([6, 1])
    hdr1.markdown(f"🟡 **PAPER TRADING LIVE** &nbsp; Dernière MAJ : `{datetime.now().strftime('%H:%M:%S')}`")
    if hdr2.button("⟳ Refresh", key="refresh_paper", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ── Load data ──
    df = load_paper_csv()
    state_data = load_paper_state()

    if df is None or state_data is None:
        st.warning("Live monitor pas encore démarré. Lance `python3 live_monitor.py` dans un terminal, ou les données n'ont pas encore été pushées sur GitHub.")
    else:

        bankroll = state_data.get("bankroll", 1000)
        start_time = state_data.get("start_time", "")
        all_trades = state_data.get("trades", [])

        resolved_trades = [t for t in all_trades if t.get("status") == "resolved"]
        pending_trades = [t for t in all_trades if t.get("status") == "pending"]

        total_pnl = sum(t.get("pnl", 0) for t in resolved_trades)
        wins = len([t for t in resolved_trades if t.get("pnl", 0) > 0])
        losses = len(resolved_trades) - wins
        win_rate = (wins / len(resolved_trades) * 100) if resolved_trades else 0
        roi = (bankroll - 1000) / 1000 * 100

        # ── Running time ──
        if start_time:
            try:
                delta = datetime.now() - datetime.fromisoformat(start_time)
                running = f"{delta.days}j {delta.seconds // 3600}h"
            except Exception:
                running = "—"
        else:
            running = "—"

        # ── Metrics row ──
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Bankroll", f"${bankroll:,.2f}", f"{roi:+.1f}%")
        m2.metric("Win Rate", f"{win_rate:.1f}%", f"{wins}W / {losses}L")
        m3.metric("P&L Total", f"${total_pnl:+,.2f}")
        m4.metric("Trades", f"{len(resolved_trades)} / {len(all_trades)}", f"Running {running}")

        # ── Go-live indicator ──
        if len(resolved_trades) >= 20 and win_rate > 75:
            st.success(f"🟢 **READY FOR LIVE** — Win rate {win_rate:.1f}% sur {len(resolved_trades)} trades résolus")
        elif len(resolved_trades) >= 20:
            st.error(f"🔴 Win rate {win_rate:.1f}% < 75% — Continuer le paper trading")
        else:
            st.info(f"⏳ {len(resolved_trades)}/20 trades résolus minimum avant évaluation")

        st.divider()

        # ── Equity curve ──
        if resolved_trades:
            st.markdown("##### 📈 Courbe de bankroll")
            sorted_resolved = sorted(resolved_trades, key=lambda t: t.get("timestamp", ""))
            cumulative = [1000]
            timestamps = [sorted_resolved[0].get("timestamp", "")[:10] if sorted_resolved else ""]
            for t in sorted_resolved:
                cumulative.append(cumulative[-1] + t.get("pnl", 0))
                timestamps.append(t.get("timestamp", "")[:16])

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=list(range(len(cumulative))),
                y=cumulative,
                mode="lines+markers",
                line=dict(color="#00ff88", width=2),
                marker=dict(size=4),
                name="Bankroll",
                hovertemplate="Trade #%{x}<br>Bankroll: $%{y:,.2f}<extra></extra>",
            ))
            fig.add_hline(y=1000, line_dash="dash", line_color="#6b7280", annotation_text="Start $1,000")
            fig.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="white"),
                xaxis=dict(title="Trade #", gridcolor="#1f2937"),
                yaxis=dict(title="Bankroll ($)", gridcolor="#1f2937"),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # ── Pending trades ──
        st.markdown(f"##### ⏳ En attente ({len(pending_trades)})")
        if pending_trades:
            pending_rows = []
            for t in sorted(pending_trades, key=lambda x: x.get("date", "")):
                pending_rows.append({
                    "Ville": t.get("city", ""),
                    "Date": t.get("date", ""),
                    "Target": t.get("target", ""),
                    "Prévision": f"{t.get('forecast', '')}°C",
                    "Prix mkt": f"{t.get('mkt_price', 0):.1%}",
                    "Edge": f"{t.get('edge', 0):+.3f}",
                    "Signal": t.get("signal", ""),
                    "Mise": f"${t.get('stake', 0):.0f}",
                })
            st.dataframe(pending_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Aucun trade en attente.")

        st.divider()

        # ── Resolved trades ──
        st.markdown(f"##### 📋 Résolus ({len(resolved_trades)})")
        if resolved_trades:
            resolved_rows = []
            for t in reversed(sorted(resolved_trades, key=lambda x: x.get("timestamp", ""))):
                pnl_val = t.get("pnl", 0)
                icon = "✅" if pnl_val > 0 else "❌"
                resolved_rows.append({
                    "": icon,
                    "Ville": t.get("city", ""),
                    "Date": t.get("date", ""),
                    "Target": t.get("target", ""),
                    "Signal": t.get("signal", ""),
                    "Réel": f"{t.get('real_temp', '?')}°C",
                    "Résultat": t.get("result", ""),
                    "P&L": f"${pnl_val:+.2f}",
                    "Mise": f"${t.get('stake', 0):.0f}",
                })
            st.dataframe(resolved_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Aucun trade résolu pour le moment.")

        # ── Stats by city ──
        if resolved_trades:
            st.divider()
            st.markdown("##### 🌍 Performance par ville")
            city_stats = {}
            for t in resolved_trades:
                c = t.get("city", "?")
                if c not in city_stats:
                    city_stats[c] = {"wins": 0, "losses": 0, "pnl": 0}
                if t.get("pnl", 0) > 0:
                    city_stats[c]["wins"] += 1
                else:
                    city_stats[c]["losses"] += 1
                city_stats[c]["pnl"] += t.get("pnl", 0)

            city_rows = []
            for c, s in sorted(city_stats.items(), key=lambda x: -x[1]["pnl"]):
                total = s["wins"] + s["losses"]
                wr = s["wins"] / total * 100 if total else 0
                city_rows.append({
                    "Ville": c,
                    "Trades": total,
                    "W/L": f"{s['wins']}/{s['losses']}",
                    "Win Rate": f"{wr:.0f}%",
                    "P&L": f"${s['pnl']:+.2f}",
                })
            st.dataframe(city_rows, use_container_width=True, hide_index=True)

    # ── Auto-refresh every 60s ──
    @st.fragment(run_every=60)
    def auto_refresh_paper():
        pass  # fragment triggers rerun
    auto_refresh_paper()
