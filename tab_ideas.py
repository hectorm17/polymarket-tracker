"""Idees de Trade tab -- catalyst scanner + Claude AI trade idea generation."""

import streamlit as st
import requests
import feedparser
import ssl
import json
from datetime import datetime, timezone

ssl._create_default_https_context = ssl._create_unverified_context

MACRO_FEEDS = {
    "Reuters": "https://news.google.com/rss/search?q=site:reuters.com+markets+economy+geopolitics&hl=en-US&gl=US&ceid=US:en",
    "Bloomberg": "https://news.google.com/rss/search?q=site:bloomberg.com+markets+economy&hl=en-US&gl=US&ceid=US:en",
    "FT": "https://www.ft.com/rss/home/uk",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "Les Echos": "https://news.google.com/rss/search?q=site:lesechos.fr+bourse+march%C3%A9&hl=fr-FR&gl=FR&ceid=FR:fr",
    "CoinDesk": "https://news.google.com/rss/search?q=site:coindesk.com+crypto+bitcoin&hl=en-US&gl=US&ceid=US:en",
}

TRIGGERS = {
    "Geopolitics": ["iran", "israel", "ukraine", "war", "attack", "sanctions", "escalation", "missile", "military"],
    "Central Banks": ["fed", "powell", "rate cut", "rate hike", "bce", "lagarde", "fomc", "monetary policy"],
    "Tariffs": ["tariff", "trade war", "trade deal", "customs", "import tax"],
    "Inflation": ["cpi", "pce", "inflation surge", "inflation drop", "deflation"],
    "Energy": ["opec", "oil surge", "oil crash", "oil price", "crude", "natural gas"],
    "Earnings": ["earnings beat", "earnings miss", "revenue miss", "profit warning", "guidance cut", "guidance raise"],
    "Crypto": ["bitcoin crash", "bitcoin surge", "btc", "ethereum", "crypto regulation", "sec crypto"],
}

SYSTEM_PROMPT = """Tu es un conseiller en placement pour un particulier europeen chez Revolut (pas d'acces au short direct mais ETF inverses OK : BX4 short CAC40, XSER short S&P500, DAPS short Nasdaq).

Pour chaque catalyseur detecte, genere MAX 1 idee de trade SI ET SEULEMENT SI le catalyseur est vraiment majeur. Sinon retourne exactement: {"signal": "SKIP"}

Format de reponse STRICT en JSON uniquement, pas de texte autour:
{
  "signal": "LONG" | "SHORT_VIA_INVERSE_ETF" | "REDUCE_POSITION" | "HEDGE" | "SKIP",
  "ticker": "string",
  "ticker_name": "string",
  "horizon": "intraday" | "court_terme" | "moyen_terme" | "long_terme",
  "confidence": 1-10,
  "entry_range": "X-Y",
  "target_1": "X (+N%)",
  "target_2": "X (+N%)",
  "stop_loss": "X (-N%)",
  "catalyst": "resume 2 phrases du catalyseur",
  "thesis": "these investissement 3 phrases",
  "risks": "principaux risques 2 phrases",
  "position_size_pct": 1-10,
  "portfolio_impact": "impact sur portefeuille actuel si pertinent",
  "revolut_compatible": true
}"""


@st.cache_data(ttl=300)
def scan_catalysts():
    """Scan RSS feeds for catalyst headlines."""
    catalysts = []
    for source, url in MACRO_FEEDS.items():
        try:
            d = feedparser.parse(url)
            for e in d.entries[:10]:
                title = e.get("title", "")
                tl = title.lower()
                pub = e.get("published_parsed")
                ts = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None

                # Check triggers
                matched_cats = []
                for cat, keywords in TRIGGERS.items():
                    if any(kw in tl for kw in keywords):
                        matched_cats.append(cat)

                if matched_cats:
                    catalysts.append({
                        "title": title,
                        "source": source,
                        "time": ts,
                        "categories": matched_cats,
                        "link": e.get("link", ""),
                    })
        except Exception:
            continue

    catalysts.sort(key=lambda x: x["time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    # Deduplicate by title similarity
    seen = set()
    unique = []
    for c in catalysts:
        key = c["title"][:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:20]


def generate_trade_idea(catalyst, portfolio_rows, market_context, api_key):
    """Call Claude API to generate a trade idea from a catalyst."""
    # Build portfolio summary
    pf_lines = []
    for r in portfolio_rows:
        pf_lines.append(f"  {r['ticker']}: {r['name']} | {r['qty']:.4g} units | avg {r['avg_price']:.2f} | current {r['cur_price']:.2f} | P&L {r['pnl']:+.2f} ({r['pnl_pct']:+.1f}%)")
    pf_text = "\n".join(pf_lines) if pf_lines else "  (empty portfolio)"

    ago = ""
    if catalyst.get("time"):
        delta = datetime.now(timezone.utc) - catalyst["time"]
        mins = int(delta.total_seconds() / 60)
        ago = f"{mins} min ago" if mins < 60 else f"{mins // 60}h ago"

    user_msg = f"""CATALYST DETECTED: {catalyst['title']}
SOURCE: {catalyst['source']}
TIMESTAMP: {ago}
CATEGORIES: {', '.join(catalyst['categories'])}

USER PORTFOLIO:
{pf_text}

MARKET CONTEXT:
{market_context}

Generate trade idea or SKIP."""

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        # Extract JSON from response
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            return json.loads(json_str)
        return {"signal": "SKIP"}
    except Exception as e:
        return {"signal": "ERROR", "error": str(e)}


def render_idea_card(idea, catalyst):
    """Render a trade idea card in PolyMonit style."""
    signal = idea.get("signal", "SKIP")
    if signal == "SKIP":
        return

    sig_colors = {
        "LONG": ("#10b981", "#0d2818"),
        "SHORT_VIA_INVERSE_ETF": ("#ef4444", "#1f0d0d"),
        "REDUCE_POSITION": ("#f59e0b", "#1f1a0d"),
        "HEDGE": ("#8b5cf6", "#1a1033"),
    }
    color, bg = sig_colors.get(signal, ("#6b7280", "#131620"))
    confidence = idea.get("confidence", 0)

    # Confidence bar
    conf_blocks = ""
    for i in range(10):
        c = color if i < confidence else "#1e2130"
        conf_blocks += f'<span style="display:inline-block; width:8px; height:8px; background:{c}; border-radius:2px; margin-right:2px;"></span>'

    ago = ""
    if catalyst.get("time"):
        delta = datetime.now(timezone.utc) - catalyst["time"]
        mins = int(delta.total_seconds() / 60)
        ago = f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago"

    st.markdown(f'''
    <div style="background:#131620; border:1px solid #1e2130; border-radius:12px; padding:20px; margin-bottom:16px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <div style="display:flex; align-items:center; gap:12px;">
                <span style="background:{bg}; color:{color}; padding:4px 12px; border-radius:6px;
                             font-weight:700; font-size:12px; border:1px solid {color}33;">{signal}</span>
                <span style="color:#4f6ef7; font-family:monospace; font-size:15px; font-weight:600;">{idea.get("ticker", "")}</span>
                <span style="color:#fff; font-size:14px;">{idea.get("ticker_name", "")}</span>
            </div>
            <div style="color:#374151; font-size:12px;">{ago} | {idea.get("horizon", "")}</div>
        </div>
        <div style="margin-bottom:12px;">
            <span style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em;">Confidence</span>
            <div style="margin-top:4px;">{conf_blocks} <span style="color:#6b7280; font-size:12px; margin-left:4px;">{confidence}/10</span></div>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px;">
            <div>
                <div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px;">Catalyst</div>
                <div style="color:#fff; font-size:13px;">{idea.get("catalyst", "")}</div>
            </div>
            <div>
                <div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px;">Thesis</div>
                <div style="color:#fff; font-size:13px;">{idea.get("thesis", "")}</div>
            </div>
        </div>
        <div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:16px;">
            <div style="background:#0d0f14; border-radius:8px; padding:12px;">
                <div style="color:#6b7280; font-size:10px; text-transform:uppercase;">Entry</div>
                <div style="color:#fff; font-size:14px; font-weight:500; margin-top:4px;">{idea.get("entry_range", "--")}</div>
            </div>
            <div style="background:#0d0f14; border-radius:8px; padding:12px;">
                <div style="color:#6b7280; font-size:10px; text-transform:uppercase;">Target 1</div>
                <div style="color:#10b981; font-size:14px; font-weight:500; margin-top:4px;">{idea.get("target_1", "--")}</div>
            </div>
            <div style="background:#0d0f14; border-radius:8px; padding:12px;">
                <div style="color:#6b7280; font-size:10px; text-transform:uppercase;">Target 2</div>
                <div style="color:#10b981; font-size:14px; font-weight:500; margin-top:4px;">{idea.get("target_2", "--")}</div>
            </div>
            <div style="background:#0d0f14; border-radius:8px; padding:12px;">
                <div style="color:#6b7280; font-size:10px; text-transform:uppercase;">Stop Loss</div>
                <div style="color:#ef4444; font-size:14px; font-weight:500; margin-top:4px;">{idea.get("stop_loss", "--")}</div>
            </div>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
            <div>
                <div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px;">Size suggestion</div>
                <div style="color:#fff; font-size:13px;">{idea.get("position_size_pct", "?")}% of portfolio</div>
            </div>
            <div>
                <div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px;">Portfolio impact</div>
                <div style="color:#fff; font-size:13px;">{idea.get("portfolio_impact", "N/A")}</div>
            </div>
        </div>
        <div style="margin-top:12px;">
            <div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px;">Risks</div>
            <div style="color:#f59e0b; font-size:13px;">{idea.get("risks", "")}</div>
        </div>
    </div>''', unsafe_allow_html=True)


def render_ideas_tab(portfolio_rows, all_prices, fg):
    if "trade_ideas" not in st.session_state:
        st.session_state.trade_ideas = []
    if "tracked_ideas" not in st.session_state:
        st.session_state.tracked_ideas = []

    has_api_key = "ANTHROPIC_API_KEY" in st.secrets

    # Header
    now_str = datetime.now().strftime("%H:%M:%S")
    h1, h2 = st.columns([6, 1])
    h1.markdown(f'<div style="display:flex; align-items:center; gap:8px;"><span style="width:7px; height:7px; background:#4f6ef7; border-radius:50%; display:inline-block;"></span><span style="color:#fff; font-weight:500;">Trade Ideas</span><span style="color:#374151; font-size:12px;">{now_str}</span></div>', unsafe_allow_html=True)
    if h2.button("Refresh", key="refresh_ideas", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # Scan catalysts
    catalysts = scan_catalysts()

    # Build market context
    btc = all_prices.get("BTC-USD", {"price": 0, "change": 0})
    spy = all_prices.get("SPY", {"price": 0, "change": 0})
    cac = all_prices.get("^FCHI", {"price": 0, "change": 0})
    vix = all_prices.get("^VIX", {"price": 0, "change": 0})
    gold = all_prices.get("GC=F", {"price": 0, "change": 0})
    dxy = all_prices.get("DX-Y.NYB", {"price": 0, "change": 0})
    mkt_ctx = f"""- BTC: ${btc['price']:,.0f} ({btc['change']:+.1f}%)
- S&P 500: ${spy['price']:,.0f} ({spy['change']:+.1f}%)
- CAC 40: {cac['price']:,.0f} ({cac['change']:+.1f}%)
- VIX: {vix['price']:.1f} ({vix['change']:+.1f}%)
- Gold: ${gold['price']:,.0f} ({gold['change']:+.1f}%)
- DXY: {dxy['price']:.2f} ({dxy['change']:+.1f}%)
- Fear & Greed: {fg['value']} -- {fg['label']}"""

    # Existing ideas
    if st.session_state.trade_ideas:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:16px 0 8px 0;">ACTIVE IDEAS</div>', unsafe_allow_html=True)
        for idea_data in reversed(st.session_state.trade_ideas[-5:]):
            render_idea_card(idea_data["idea"], idea_data["catalyst"])

    st.markdown('<div style="border-top:1px solid #1e2130; margin:20px 0;"></div>', unsafe_allow_html=True)

    # Catalyst feed
    st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:16px 0 8px 0;">DETECTED CATALYSTS</div>', unsafe_allow_html=True)

    if not catalysts:
        st.markdown('<div style="text-align:center; padding:32px; color:#374151; font-size:13px;">No catalysts detected</div>', unsafe_allow_html=True)
    else:
        for idx, cat in enumerate(catalysts[:15]):
            ago = ""
            if cat["time"]:
                delta = datetime.now(timezone.utc) - cat["time"]
                mins = int(delta.total_seconds() / 60)
                ago = f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago"

            cat_badges = ""
            cat_colors = {"Geopolitics": "#ef4444", "Central Banks": "#4f6ef7", "Tariffs": "#f59e0b",
                          "Inflation": "#8b5cf6", "Energy": "#10b981", "Earnings": "#06b6d4", "Crypto": "#f59e0b"}
            for c in cat["categories"]:
                cc = cat_colors.get(c, "#6b7280")
                cat_badges += f'<span style="background:{cc}22; color:{cc}; font-size:10px; padding:2px 6px; border-radius:3px; margin-right:4px;">{c}</span>'

            st.markdown(f'''<div style="padding:12px 16px; border-bottom:1px solid #1e2130; font-size:13px;">
                {cat_badges}<br/>
                <span style="color:#fff; margin-top:4px; display:block;">{cat["title"]}</span>
                <span style="color:#374151; font-size:11px;">{cat["source"]} -- {ago}</span>
            </div>''', unsafe_allow_html=True)

            if has_api_key:
                if st.button("Generate idea", key=f"gen_idea_{idx}", type="secondary"):
                    with st.spinner("Analyzing catalyst..."):
                        idea = generate_trade_idea(cat, portfolio_rows, mkt_ctx, st.secrets["ANTHROPIC_API_KEY"])
                    if idea.get("signal") == "SKIP":
                        st.markdown('<div style="color:#374151; font-size:12px; padding:8px 16px;">Catalyst not significant enough -- SKIP</div>', unsafe_allow_html=True)
                    elif idea.get("signal") == "ERROR":
                        st.error(f"API error: {idea.get('error', '')}")
                    else:
                        st.session_state.trade_ideas.append({"idea": idea, "catalyst": cat, "time": datetime.now().isoformat()})
                        st.rerun()

    if not has_api_key:
        st.markdown('<div style="background:#131620; border:1px solid #1e2130; border-radius:12px; padding:20px; text-align:center; color:#6b7280; font-size:13px; margin-top:16px;">Add ANTHROPIC_API_KEY to Streamlit secrets to enable AI trade ideas</div>', unsafe_allow_html=True)

    # Idea history
    if st.session_state.trade_ideas:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:24px 0 8px 0;">IDEA HISTORY</div>', unsafe_allow_html=True)
        for i, td in enumerate(reversed(st.session_state.trade_ideas)):
            idea = td["idea"]
            sig = idea.get("signal", "?")
            sc = "#10b981" if sig == "LONG" else "#ef4444" if "SHORT" in sig else "#f59e0b"
            st.markdown(f'''<div style="display:flex; align-items:center; gap:12px; padding:8px 16px; border-bottom:1px solid #131620; font-size:13px;">
                <span style="color:{sc}; font-weight:600; min-width:40px;">{sig[:5]}</span>
                <span style="color:#4f6ef7; font-family:monospace;">{idea.get("ticker","?")}</span>
                <span style="color:#fff; flex:1;">{idea.get("ticker_name","")}</span>
                <span style="color:#6b7280;">{idea.get("confidence","?")}/10</span>
                <span style="color:#374151; font-size:11px;">{td.get("time","")[:16]}</span>
            </div>''', unsafe_allow_html=True)
