"""Mon Portefeuille tab — live portfolio tracking with P&L, allocation, alerts."""

import streamlit as st
import requests
import json
import plotly.graph_objects as go
from datetime import datetime
from pathlib import Path

DATA_API = "https://data-api.polymarket.com"

DEFAULT_PORTFOLIO = [
    {"ticker": "CSPX.L",   "name": "Amundi S&P 500 ETF",      "qty": 1.55,   "avg_price": 120.60,  "cat": "ETF",         "currency": "EUR"},
    {"ticker": "C40.PA",   "name": "Amundi CAC 40 ESG",        "qty": 0.75,   "avg_price": 144.70,  "cat": "ETF",         "currency": "EUR"},
    {"ticker": "SEMI.AS",  "name": "iShares Semiconductors",   "qty": 7.91,   "avg_price": 13.14,   "cat": "ETF",         "currency": "EUR"},
    {"ticker": "EUNK.DE",  "name": "iShares MSCI Europe",      "qty": 0.99,   "avg_price": 99.84,   "cat": "ETF",         "currency": "EUR"},
    {"ticker": "EHF1.DE",  "name": "Amundi High Dividend",     "qty": 0.41,   "avg_price": 233.45,  "cat": "ETF",         "currency": "EUR"},
    {"ticker": "RMS.PA",   "name": "Hermes International",     "qty": 0.03,   "avg_price": 1736,    "cat": "Stock",       "currency": "EUR"},
    {"ticker": "AAPL",     "name": "Apple",                    "qty": 0.17,   "avg_price": 270.63,  "cat": "Stock",       "currency": "USD"},
    {"ticker": "IONQ",     "name": "IonQ",                     "qty": 1.01,   "avg_price": 45.63,   "cat": "Stock",       "currency": "USD"},
    {"ticker": "GC=F",     "name": "Gold",                     "qty": 0.0204, "avg_price": 4115.63, "cat": "Commodities", "currency": "EUR"},
    {"ticker": "BTC-EUR",  "name": "Bitcoin",                  "qty": 0.0023, "avg_price": 65200,   "cat": "Crypto",      "currency": "EUR"},
]


@st.cache_data(ttl=60)
def fetch_portfolio_prices(tickers):
    import yfinance as yf
    results = {}
    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = info.get("lastPrice", 0) or info.get("last_price", 0)
            prev = info.get("previousClose", 0) or info.get("previous_close", 0)
            chg = ((price - prev) / prev * 100) if prev else 0
            results[sym] = {"price": price, "change": chg}
        except Exception:
            results[sym] = {"price": 0, "change": 0}
    return results


def render_portfolio_tab():
    if "portfolio" not in st.session_state:
        st.session_state.portfolio = DEFAULT_PORTFOLIO.copy()

    portfolio = st.session_state.portfolio

    # Fetch prices
    tickers = [p["ticker"] for p in portfolio if p["ticker"] != "ROBO_ADVISOR"]
    prices = fetch_portfolio_prices(tuple(tickers))

    # Compute per-position data
    rows = []
    total_value = 0
    total_cost = 0
    cat_values = {}
    ccy_values = {"EUR": 0, "USD": 0}
    alerts = []

    for pos in portfolio:
        ticker = pos["ticker"]
        if ticker == "ROBO_ADVISOR":
            # Fixed value, no live price
            value = pos["avg_price"] * pos["qty"]
            cost = value
            pnl = 0
            pnl_pct = 0
            cur_price = pos["avg_price"]
            day_chg = 0
        else:
            p = prices.get(ticker, {"price": 0, "change": 0})
            cur_price = p["price"]
            day_chg = p["change"]
            value = cur_price * pos["qty"]
            cost = pos["avg_price"] * pos["qty"]
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost else 0

        total_value += value
        total_cost += cost
        cat = pos["cat"]
        cat_values[cat] = cat_values.get(cat, 0) + value
        ccy = pos["currency"]
        ccy_values[ccy] = ccy_values.get(ccy, 0) + value

        rows.append({
            "ticker": ticker, "name": pos["name"], "cat": cat, "currency": ccy,
            "qty": pos["qty"], "avg_price": pos["avg_price"],
            "cur_price": cur_price, "day_chg": day_chg,
            "value": value, "cost": cost, "pnl": pnl, "pnl_pct": pnl_pct,
        })

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0

    # Generate alerts
    for r in rows:
        weight = (r["value"] / total_value * 100) if total_value else 0
        if weight > 20:
            alerts.append({"type": "concentration", "msg": f'{r["name"]} represents {weight:.0f}% of portfolio -- consider rebalancing'})
        if r["pnl_pct"] > 20:
            alerts.append({"type": "take_profit", "msg": f'{r["name"]} at +{r["pnl_pct"]:.1f}% -- consider taking partial profit'})
        if r["pnl_pct"] < -8:
            alerts.append({"type": "stop_loss", "msg": f'{r["name"]} at {r["pnl_pct"]:.1f}% -- review stop loss'})

    # ── Header ──
    now_str = datetime.now().strftime("%H:%M:%S")
    h1, h2 = st.columns([6, 1])
    h1.markdown(f'<div style="display:flex; align-items:center; gap:8px;"><span style="width:7px; height:7px; background:#10b981; border-radius:50%; display:inline-block;"></span><span style="color:#fff; font-weight:500;">Portfolio</span><span style="color:#374151; font-size:12px;">{now_str}</span></div>', unsafe_allow_html=True)
    if h2.button("Refresh", key="refresh_pf", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # ── Metrics ──
    pnl_color = "#10b981" if total_pnl >= 0 else "#ef4444"
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Value", f"${total_value:,.2f}")
    m2.metric("Total P&L", f"${total_pnl:+,.2f}", f"{total_pnl_pct:+.1f}%")
    m3.metric("Positions", len(portfolio))
    m4.metric("EUR / USD", f"{ccy_values.get('EUR',0)/total_value*100:.0f}% / {ccy_values.get('USD',0)/total_value*100:.0f}%" if total_value else "N/A")

    # ── Alerts ──
    if alerts:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">PORTFOLIO ALERTS</div>', unsafe_allow_html=True)
        for a in alerts:
            colors = {"concentration": "#f59e0b", "take_profit": "#10b981", "stop_loss": "#ef4444"}
            c = colors.get(a["type"], "#6b7280")
            st.markdown(f'<div style="padding:10px 16px; border-left:3px solid {c}; background:#131620; border-radius:0 8px 8px 0; margin-bottom:4px; font-size:13px; color:#fff;">{a["msg"]}</div>', unsafe_allow_html=True)

    # ── Charts row ──
    ch1, ch2 = st.columns(2)

    with ch1:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">ALLOCATION BY CATEGORY</div>', unsafe_allow_html=True)
        cat_colors = {"ETF": "#4f6ef7", "Stock": "#10b981", "Crypto": "#f59e0b", "Commodities": "#ef4444", "Managed": "#8b5cf6"}
        fig = go.Figure(go.Pie(
            labels=list(cat_values.keys()),
            values=list(cat_values.values()),
            hole=0.55,
            marker=dict(colors=[cat_colors.get(c, "#6b7280") for c in cat_values.keys()]),
            textinfo="label+percent",
            textfont=dict(size=12, color="#fff"),
        ))
        fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(color="#fff", family="Inter"), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with ch2:
        st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">CURRENCY EXPOSURE</div>', unsafe_allow_html=True)
        fig2 = go.Figure(go.Pie(
            labels=list(ccy_values.keys()),
            values=list(ccy_values.values()),
            hole=0.55,
            marker=dict(colors=["#4f6ef7", "#10b981"]),
            textinfo="label+percent",
            textfont=dict(size=12, color="#fff"),
        ))
        fig2.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=10),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           font=dict(color="#fff", family="Inter"), showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

    # ── Positions table ──
    st.markdown('<div style="color:#6b7280; font-size:11px; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:20px 0 8px 0;">POSITIONS</div>', unsafe_allow_html=True)

    # Header
    st.markdown('''<div style="display:grid; grid-template-columns:1.5fr 2fr 80px 90px 90px 90px 90px 70px;
        padding:8px 16px; color:#374151; font-size:10px; text-transform:uppercase; letter-spacing:0.08em; font-weight:600;">
        <div>TICKER</div><div>NAME</div><div style="text-align:right;">QTY</div>
        <div style="text-align:right;">AVG</div><div style="text-align:right;">CURRENT</div>
        <div style="text-align:right;">VALUE</div><div style="text-align:right;">P&L</div>
        <div style="text-align:right;">DAY</div>
    </div>''', unsafe_allow_html=True)

    for r in sorted(rows, key=lambda x: -x["value"]):
        pc = "#10b981" if r["pnl"] >= 0 else "#ef4444"
        dc = "#10b981" if r["day_chg"] >= 0 else "#ef4444"
        sign = "+" if r["pnl"] >= 0 else ""
        weight = (r["value"] / total_value * 100) if total_value else 0
        st.markdown(f'''<div style="display:grid; grid-template-columns:1.5fr 2fr 80px 90px 90px 90px 90px 70px;
            align-items:center; padding:12px 16px; border-bottom:1px solid #131620; font-size:13px;"
            onmouseover="this.style.background='#131620'" onmouseout="this.style.background='transparent'">
            <div><span style="color:#4f6ef7; font-family:monospace;">{r["ticker"]}</span>
                <span style="color:#374151; font-size:11px; margin-left:4px;">{r["cat"]}</span></div>
            <div style="color:#fff;">{r["name"]}</div>
            <div style="color:#6b7280; text-align:right; font-family:monospace;">{r["qty"]:.4g}</div>
            <div style="color:#6b7280; text-align:right;">{r["avg_price"]:,.2f}</div>
            <div style="color:#fff; text-align:right; font-weight:500;">{r["cur_price"]:,.2f}</div>
            <div style="color:#fff; text-align:right;">{r["currency"]} {r["value"]:,.0f}<br/>
                <span style="color:#374151; font-size:10px;">{weight:.1f}%</span></div>
            <div style="color:{pc}; text-align:right; font-weight:500;">{sign}{r["pnl"]:,.2f}
                <br/><span style="font-size:10px;">{sign}{r["pnl_pct"]:.1f}%</span></div>
            <div style="color:{dc}; text-align:right; font-size:12px;">{r["day_chg"]:+.1f}%</div>
        </div>''', unsafe_allow_html=True)

    # ── Add position ──
    with st.expander("Add / Remove Position"):
        with st.form("add_pos", clear_on_submit=True):
            ac = st.columns([2, 3, 1, 1, 1, 1])
            new_ticker = ac[0].text_input("Ticker", placeholder="AAPL", label_visibility="collapsed")
            new_name = ac[1].text_input("Name", placeholder="Apple Inc", label_visibility="collapsed")
            new_qty = ac[2].number_input("Qty", min_value=0.0, value=1.0, step=0.1, label_visibility="collapsed")
            new_avg = ac[3].number_input("Avg Price", min_value=0.0, value=100.0, step=1.0, label_visibility="collapsed")
            new_cat = ac[4].selectbox("Cat", ["ETF", "Stock", "Crypto", "Commodities", "Managed"], label_visibility="collapsed")
            add_btn = ac[5].form_submit_button("Add", use_container_width=True)
        if add_btn and new_ticker.strip():
            st.session_state.portfolio.append({
                "ticker": new_ticker.strip().upper(), "name": new_name.strip() or new_ticker.strip().upper(),
                "qty": new_qty, "avg_price": new_avg, "cat": new_cat, "currency": "EUR"})
            st.rerun()

        for i, p in enumerate(st.session_state.portfolio):
            rc1, rc2, rc3 = st.columns([4, 2, 1])
            rc1.markdown(f'<span style="color:#4f6ef7; font-family:monospace;">{p["ticker"]}</span> <span style="color:#6b7280;">{p["name"]}</span>', unsafe_allow_html=True)
            rc2.markdown(f'<span style="color:#6b7280;">{p["qty"]:.4g} @ {p["avg_price"]:,.2f}</span>', unsafe_allow_html=True)
            if rc3.button("Del", key=f"del_pos_{i}", use_container_width=True):
                st.session_state.portfolio.pop(i)
                st.rerun()

    return rows, alerts, total_value, total_pnl
