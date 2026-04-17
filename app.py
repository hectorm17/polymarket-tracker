import streamlit as st
import requests
import feedparser
import ssl
import time
from datetime import date, datetime, timezone
from supabase import create_client

# ── Fix SSL for RSS feeds ──
ssl._create_default_https_context = ssl._create_unverified_context

# ── Supabase setup ──
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

db = get_supabase()

# ─────────────────────────────────────────────
# DATA FETCHERS
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

# ── Market data ──
@st.cache_data(ttl=60)
def fetch_market_prices():
    import yfinance as yf
    symbols = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "SPY": "SPY",
        "QQQ": "QQQ",
        "EUR/USD": "EURUSD=X",
        "DXY": "DX-Y.NYB",
    }
    results = {}
    for label, sym in symbols.items():
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = info.get("lastPrice", 0) or info.get("last_price", 0)
            prev = info.get("previousClose", 0) or info.get("previous_close", 0)
            chg = ((price - prev) / prev * 100) if prev else 0
            results[label] = {"price": price, "change": chg}
        except Exception:
            results[label] = {"price": 0, "change": 0}
    return results

@st.cache_data(ttl=60)
def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        data = r.json()["data"][0]
        return {"value": int(data["value"]), "label": data["value_classification"]}
    except Exception:
        return {"value": 0, "label": "N/A"}

@st.cache_data(ttl=300)
def fetch_news_trump():
    d = feedparser.parse("https://news.google.com/rss/search?q=Trump+tariffs+economy+policy&hl=en-US&gl=US&ceid=US:en")
    items = []
    for e in d.entries[:8]:
        pub = e.get("published_parsed")
        ts = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
        items.append({"title": e.get("title", ""), "link": e.get("link", ""), "time": ts})
    return items

@st.cache_data(ttl=300)
def fetch_news_musk():
    d = feedparser.parse("https://news.google.com/rss/search?q=Elon+Musk+DOGE+Tesla+economy&hl=en-US&gl=US&ceid=US:en")
    items = []
    for e in d.entries[:5]:
        pub = e.get("published_parsed")
        ts = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
        items.append({"title": e.get("title", ""), "link": e.get("link", ""), "time": ts})
    return items

@st.cache_data(ttl=300)
def fetch_news_macro():
    feeds = [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ]
    items = []
    for url in feeds:
        d = feedparser.parse(url)
        for e in d.entries[:5]:
            pub = e.get("published_parsed")
            ts = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
            items.append({"title": e.get("title", ""), "source": d.feed.get("title", ""), "link": e.get("link", ""), "time": ts})
    items.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:10]

# ── DB helpers ──
def load_wallets():
    res = db.table("wallets").select("*").order("created_at").execute()
    return res.data

def add_wallet(address, label):
    db.table("wallets").insert({"address": address.lower(), "label": label}).execute()

def remove_wallet(address):
    db.table("wallets").delete().eq("address", address.lower()).execute()

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────

for key, default in {
    "alerts": [],
    "last_trade_ts": {},
    "alert_threshold": 100.0,
    "analyses": [],
    "daily_summaries": [],
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
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 0.75rem;
        padding: 1rem;
    }
    div[data-testid="stMetric"] label { color: #9ca3af; }
    .alert-item {
        padding: 0.5rem 0.75rem;
        border-left: 3px solid #6366f1;
        background: #111827;
        border-radius: 0 0.5rem 0.5rem 0;
        margin-bottom: 0.4rem;
        font-size: 0.85rem;
    }
    .alert-time { color: #6b7280; font-size: 0.75rem; }
    .news-item {
        padding: 0.4rem 0;
        border-bottom: 1px solid #1f2937;
        font-size: 0.9rem;
    }
    .news-source { color: #6366f1; font-size: 0.75rem; font-weight: 600; }
    .news-time { color: #6b7280; font-size: 0.75rem; }
    .signal-box {
        padding: 1rem 1.5rem;
        border-radius: 0.75rem;
        font-size: 1.1rem;
        font-weight: 700;
        text-align: center;
        margin: 1rem 0;
    }
    .signal-risk-off { background: #450a0a; border: 1px solid #dc2626; color: #fca5a5; }
    .signal-risk-on { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
    .signal-neutral { background: #1c1917; border: 1px solid #a16207; color: #fde68a; }
</style>
""", unsafe_allow_html=True)

st.title("📊 Polymarket Wallet Tracker")

# ─────────────────────────────────────────────
# MAIN TABS
# ─────────────────────────────────────────────

main_tab_wallets, main_tab_analyst = st.tabs(["💰 Wallets", "🤖 Analyste IA"])

# ═════════════════════════════════════════════
# TAB 1: WALLETS
# ═════════════════════════════════════════════

with main_tab_wallets:
    st.caption("Track positions, trades & P&L across proxy wallets")

    # ── Alert settings + panel ──
    with st.expander("🔔 Alerts", expanded=bool(st.session_state.alerts)):
        st.session_state.alert_threshold = st.number_input(
            "Min trade size (USDC)", min_value=0.0, value=st.session_state.alert_threshold,
            step=10.0, help="Only alert for trades above this amount",
        )
        if st.session_state.alerts:
            for a in st.session_state.alerts:
                st.markdown(
                    f'<div class="alert-item">{a["msg"]}<br/>'
                    f'<span class="alert-time">{a["time"]}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No alerts yet — new trades will appear here.")

    # ── Add wallet form ──
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

    # ── Display wallets ──
    wallets = load_wallets()

    if not wallets:
        st.divider()
        st.markdown(
            "<div style='text-align:center; padding:4rem 0; color:#6b7280;'>"
            "<p style='font-size:1.2rem;'>No wallets tracked yet</p>"
            "<p>Add a Polymarket proxy wallet address above to get started</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        for wallet in wallets:
            addr = wallet["address"]
            lbl = wallet["label"]
            short = addr[:6] + "..." + addr[-4:]

            st.divider()

            hcol1, hcol2 = st.columns([6, 1])
            hcol1.subheader(f"{lbl}")
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
            active_positions = []
            closed_positions = []
            for p in positions:
                end = p.get("endDate")
                try:
                    is_active = date.fromisoformat(end) >= today if end else True
                except (ValueError, TypeError):
                    is_active = True
                if is_active:
                    active_positions.append(p)
                else:
                    closed_positions.append(p)

            wins = [p for p in closed_positions if float(p.get("cashPnl", 0)) + float(p.get("realizedPnl", 0)) > 0]
            win_rate = (len(wins) / len(closed_positions) * 100) if closed_positions else 0

            mcol1, mcol2, mcol3 = st.columns(3)
            mcol1.metric("Total P&L", f"${total_pnl:+,.2f}")
            mcol2.metric("Win Rate", f"{win_rate:.1f}%")
            mcol3.metric("Open Positions", len(active_positions))

            tab_pos, tab_trades = st.tabs([f"📈 Positions ({len(active_positions)})", f"🔄 Recent Trades ({len(trades)})"])

            with tab_pos:
                if not active_positions:
                    st.info("No open positions")
                else:
                    rows = []
                    for p in active_positions:
                        side = (p.get("outcome") or "Yes").upper()
                        rows.append({
                            "Market": p.get("title", "Unknown"),
                            "Side": side,
                            "Size": float(p.get("size", 0)),
                            "Avg Price": float(p.get("avgPrice", 0)),
                            "Cur Price": float(p.get("curPrice", 0)),
                            "P&L ($)": float(p.get("cashPnl", 0)),
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={
                        "Size": st.column_config.NumberColumn(format="%.2f"),
                        "Avg Price": st.column_config.NumberColumn(format="%.3f"),
                        "Cur Price": st.column_config.NumberColumn(format="%.3f"),
                        "P&L ($)": st.column_config.NumberColumn(format="%+.2f"),
                    })

            with tab_trades:
                if not trades:
                    st.info("No recent trades")
                else:
                    rows = []
                    for t in trades:
                        ts = t.get("timestamp")
                        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"
                        rows.append({
                            "Market": t.get("title", "—"),
                            "Side": (t.get("side") or "BUY").upper(),
                            "Size": float(t.get("size", 0)),
                            "Price": float(t.get("price", 0)),
                            "Time": time_str,
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True, column_config={
                        "Size": st.column_config.NumberColumn(format="%.2f"),
                        "Price": st.column_config.NumberColumn(format="%.3f"),
                    })

        # ── Alert poller ──
        @st.fragment(run_every=60)
        def poll_alerts():
            wlist = load_wallets()
            if not wlist:
                return
            threshold = st.session_state.alert_threshold
            new_alerts = []
            for w in wlist:
                a = w["address"]
                l = w["label"]
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
                    usdc_size = float(t.get("usdcSize", 0)) or float(t.get("size", 0)) * float(t.get("price", 0))
                    if usdc_size < threshold:
                        continue
                    side = (t.get("side") or "BUY").upper()
                    market = t.get("title", "Unknown")
                    size = float(t.get("size", 0))
                    price = float(t.get("price", 0))
                    msg = f'🚨 **{l}** vient d\'ouvrir une position sur "{market}" — {side} {size:.1f} shares à {price*100:.0f}¢'
                    new_alerts.append({"msg": msg, "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), "ts": ts})
                    st.toast(msg, icon="🚨")
                latest_ts = recent[0].get("timestamp", 0)
                if latest_ts > last_known:
                    st.session_state.last_trade_ts[a] = latest_ts
            if new_alerts:
                st.session_state.alerts = (new_alerts + st.session_state.alerts)[:20]

        poll_alerts()

        st.divider()
        if st.button("🔄 Refresh all data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

# ═════════════════════════════════════════════
# TAB 2: ANALYSTE IA
# ═════════════════════════════════════════════

with main_tab_analyst:

    # ── Live header ──
    now_str = datetime.now().strftime("%H:%M:%S")
    st.markdown(f"🔴 **LIVE** &nbsp;&nbsp; Last update: `{now_str}`")

    # ── Market prices ──
    prices = fetch_market_prices()
    fg = fetch_fear_greed()

    pcols = st.columns(len(prices) + 1)
    for i, (label, d) in enumerate(prices.items()):
        p = d["price"]
        c = d["change"]
        if p >= 1000:
            fmt = f"${p:,.0f}"
        elif p >= 1:
            fmt = f"${p:,.2f}"
        else:
            fmt = f"${p:.4f}"
        pcols[i].metric(label, fmt, f"{c:+.2f}%")

    # Fear & Greed
    fg_val = fg["value"]
    fg_label = fg["label"]
    if fg_val <= 25:
        fg_emoji = "😨"
    elif fg_val <= 45:
        fg_emoji = "😟"
    elif fg_val <= 55:
        fg_emoji = "😐"
    elif fg_val <= 75:
        fg_emoji = "😊"
    else:
        fg_emoji = "🤑"
    pcols[-1].metric("Fear & Greed", f"{fg_val}", f"{fg_label} {fg_emoji}")

    st.divider()

    # ── Trump / Musk news ──
    col_trump, col_musk = st.columns(2)

    with col_trump:
        st.markdown("##### 🇺🇸 Trump — Dernières news")
        trump_news = fetch_news_trump()
        if trump_news:
            for item in trump_news[:5]:
                ago = ""
                if item["time"]:
                    delta = datetime.now(timezone.utc) - item["time"]
                    mins = int(delta.total_seconds() / 60)
                    if mins < 60:
                        ago = f"il y a {mins} min"
                    elif mins < 1440:
                        ago = f"il y a {mins // 60}h"
                    else:
                        ago = f"il y a {mins // 1440}j"
                st.markdown(
                    f'<div class="news-item">{item["title"]}<br/>'
                    f'<span class="news-time">{ago}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Aucune news récente")

    with col_musk:
        st.markdown("##### 🚀 Musk — Dernières news")
        musk_news = fetch_news_musk()
        if musk_news:
            for item in musk_news[:5]:
                ago = ""
                if item["time"]:
                    delta = datetime.now(timezone.utc) - item["time"]
                    mins = int(delta.total_seconds() / 60)
                    if mins < 60:
                        ago = f"il y a {mins} min"
                    elif mins < 1440:
                        ago = f"il y a {mins // 60}h"
                    else:
                        ago = f"il y a {mins // 1440}j"
                st.markdown(
                    f'<div class="news-item">{item["title"]}<br/>'
                    f'<span class="news-time">{ago}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Aucune news récente")

    st.divider()

    # ── Macro news ──
    st.markdown("##### 📡 Flux news macro")
    macro_news = fetch_news_macro()
    if macro_news:
        for item in macro_news:
            ago = ""
            if item["time"]:
                delta = datetime.now(timezone.utc) - item["time"]
                mins = int(delta.total_seconds() / 60)
                if mins < 60:
                    ago = f"il y a {mins} min"
                elif mins < 1440:
                    ago = f"il y a {mins // 60}h"
                else:
                    ago = f"il y a {mins // 1440}j"
            src = item.get("source", "")
            st.markdown(
                f'<div class="news-item">{item["title"]}<br/>'
                f'<span class="news-source">{src}</span> · <span class="news-time">{ago}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("Aucune news macro disponible")

    st.divider()

    # ── AI Analysis ──
    st.markdown("##### 🤖 Analyse IA")

    def build_analysis_context():
        """Compile all live data into a text block for Claude."""
        lines = ["=== PRIX MARCHÉS ==="]
        for label, d in prices.items():
            lines.append(f"{label}: ${d['price']:,.2f} ({d['change']:+.2f}%)")
        lines.append(f"\n=== FEAR & GREED INDEX ===\nValue: {fg['value']} — {fg['label']}")
        lines.append("\n=== NEWS TRUMP ===")
        for item in (trump_news or [])[:5]:
            lines.append(f"- {item['title']}")
        lines.append("\n=== NEWS MUSK ===")
        for item in (musk_news or [])[:5]:
            lines.append(f"- {item['title']}")
        lines.append("\n=== NEWS MACRO (CNBC / BBC) ===")
        for item in (macro_news or [])[:8]:
            lines.append(f"- [{item.get('source','')}] {item['title']}")
        return "\n".join(lines)

    SYSTEM_PROMPT = """Tu es un analyste macro senior. On te donne en temps réel :
- Les prix et variations des actifs clés
- Le Fear & Greed index
- Les dernières news sur Trump et Musk
- Les dernières news macro (CNBC / BBC)

Produis une analyse concise en 3 parties :
1. CONTEXTE (2-3 phrases sur le sentiment global)
2. POINTS D'ATTENTION (3 bullets max sur les signaux importants)
3. SIGNAL DIRECTIONNEL : RISK ON 🟢 / RISK OFF 🔴 / NEUTRE ⚠️ avec justification courte

Sois direct, factuel, sans hedging excessif. Réponds en français."""

    has_api_key = "ANTHROPIC_API_KEY" in st.secrets

    if not has_api_key:
        st.warning("Ajoute `ANTHROPIC_API_KEY` dans tes secrets Streamlit pour activer l'analyse IA.")
    else:
        acol1, acol2 = st.columns([1, 1])
        analyze_now = acol1.button("🧠 Analyser maintenant", use_container_width=True)
        generate_summary = acol2.button("📋 Générer résumé du jour", use_container_width=True)

        if analyze_now:
            context = build_analysis_context()
            with st.spinner("Claude analyse les données..."):
                try:
                    from anthropic import Anthropic
                    client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                    response = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=1024,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": f"Voici les données live à {now_str} :\n\n{context}"}],
                    )
                    analysis_text = response.content[0].text
                    st.session_state.analyses.append({
                        "time": now_str,
                        "text": analysis_text,
                        "context": context,
                    })
                    # Keep last 24
                    st.session_state.analyses = st.session_state.analyses[-24:]
                except Exception as e:
                    st.error(f"Erreur Claude API : {e}")
                    analysis_text = None

        if generate_summary:
            if not st.session_state.analyses:
                st.warning("Aucune analyse à résumer. Clique d'abord sur 'Analyser maintenant'.")
            else:
                all_analyses = "\n\n---\n\n".join(
                    f"[{a['time']}]\n{a['text']}" for a in st.session_state.analyses
                )
                with st.spinner("Génération du résumé quotidien..."):
                    try:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                        response = client.messages.create(
                            model="claude-sonnet-4-20250514",
                            max_tokens=1500,
                            system="Tu es un analyste macro senior. On te donne toutes les analyses de la journée. Produis un résumé quotidien structuré avec : 1) SYNTHÈSE DU JOUR (3-4 phrases) 2) ÉVÉNEMENTS CLÉS (bullets) 3) ÉVOLUTION DU SENTIMENT (comment il a changé dans la journée) 4) SIGNAL DE FIN DE JOURNÉE. Réponds en français.",
                            messages=[{"role": "user", "content": f"Voici toutes les analyses de la journée :\n\n{all_analyses}"}],
                        )
                        summary = response.content[0].text
                        st.session_state.daily_summaries.append({
                            "date": date.today().isoformat(),
                            "time": now_str,
                            "text": summary,
                        })
                    except Exception as e:
                        st.error(f"Erreur Claude API : {e}")

        # ── Display latest analysis ──
        if st.session_state.analyses:
            latest = st.session_state.analyses[-1]
            st.markdown(f"**Analyse de {latest['time']}**")
            text = latest["text"]

            # Detect signal for colored box
            signal_class = "signal-neutral"
            signal_text = ""
            text_lower = text.lower()
            if "risk off" in text_lower:
                signal_class = "signal-risk-off"
                signal_text = "🔴 RISK OFF"
            elif "risk on" in text_lower:
                signal_class = "signal-risk-on"
                signal_text = "🟢 RISK ON"
            elif "neutre" in text_lower:
                signal_class = "signal-neutral"
                signal_text = "⚠️ NEUTRE"

            if signal_text:
                st.markdown(f'<div class="signal-box {signal_class}">{signal_text}</div>', unsafe_allow_html=True)

            st.markdown(text)

        # ── Display daily summaries ──
        if st.session_state.daily_summaries:
            st.divider()
            st.markdown("##### 📋 Résumés quotidiens")
            for s in reversed(st.session_state.daily_summaries):
                with st.expander(f"Résumé du {s['date']} ({s['time']})"):
                    st.markdown(s["text"])

    # ── Auto-refresh prices every 60s ──
    @st.fragment(run_every=60)
    def refresh_prices():
        st.cache_data.clear()

    refresh_prices()
