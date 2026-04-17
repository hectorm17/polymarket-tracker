import streamlit as st
import requests
import time
from datetime import date, datetime
from supabase import create_client

# ── Supabase setup ──
@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )

db = get_supabase()

# ── Polymarket API helpers ──
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
    """No cache — used by the alert poller to get fresh trades."""
    r = requests.get(f"{DATA_API}/trades", params={"user": address.lower(), "limit": 5}, timeout=15)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=60)
def fetch_pnl(address):
    r = requests.get(f"{LB_API}/profit", params={"window": "all", "address": address.lower()}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data and len(data) > 0:
        return float(data[0].get("amount", 0))
    return 0.0

# ── DB helpers ──
def load_wallets():
    res = db.table("wallets").select("*").order("created_at").execute()
    return res.data

def add_wallet(address, label):
    db.table("wallets").insert({"address": address.lower(), "label": label}).execute()

def remove_wallet(address):
    db.table("wallets").delete().eq("address", address.lower()).execute()

# ── Session state init ──
if "alerts" not in st.session_state:
    st.session_state.alerts = []
if "last_trade_ts" not in st.session_state:
    st.session_state.last_trade_ts = {}
if "alert_threshold" not in st.session_state:
    st.session_state.alert_threshold = 100.0

# ── Page config ──
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
</style>
""", unsafe_allow_html=True)

st.title("📊 Polymarket Wallet Tracker")
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

        # ── Header row ──
        hcol1, hcol2 = st.columns([6, 1])
        hcol1.subheader(f"{lbl}")
        hcol1.caption(f"`{short}`")
        if hcol2.button("🗑️ Remove", key=f"rm_{addr}", use_container_width=True):
            remove_wallet(addr)
            st.cache_data.clear()
            st.rerun()

        # ── Fetch data ──
        try:
            positions = fetch_positions(addr)
            trades = fetch_trades(addr)
            total_pnl = fetch_pnl(addr)
        except Exception as e:
            st.error(f"Failed to fetch data for {lbl}: {e}")
            continue

        # ── Filter active vs closed positions ──
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

        # ── Win rate on closed positions only ──
        wins = [p for p in closed_positions if float(p.get("cashPnl", 0)) + float(p.get("realizedPnl", 0)) > 0]
        win_rate = (len(wins) / len(closed_positions) * 100) if closed_positions else 0

        # ── Stats ──
        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Total P&L", f"${total_pnl:+,.2f}")
        mcol2.metric("Win Rate", f"{win_rate:.1f}%")
        mcol3.metric("Open Positions", len(active_positions))

        # ── Tabs ──
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
                st.dataframe(
                    rows,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Size": st.column_config.NumberColumn(format="%.2f"),
                        "Avg Price": st.column_config.NumberColumn(format="%.3f"),
                        "Cur Price": st.column_config.NumberColumn(format="%.3f"),
                        "P&L ($)": st.column_config.NumberColumn(format="%+.2f"),
                    },
                )

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
                st.dataframe(
                    rows,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Size": st.column_config.NumberColumn(format="%.2f"),
                        "Price": st.column_config.NumberColumn(format="%.3f"),
                    },
                )

# ── Alert poller (runs as a fragment, re-executes every 60s) ──
@st.fragment(run_every=60)
def poll_alerts():
    wlist = load_wallets()
    if not wlist:
        return
    threshold = st.session_state.alert_threshold
    new_alerts = []
    for w in wlist:
        addr = w["address"]
        lbl = w["label"]
        try:
            recent = fetch_recent_trades(addr)
        except Exception:
            continue
        if not recent:
            continue
        last_known = st.session_state.last_trade_ts.get(addr, 0)
        # On first load, just record the latest timestamp without alerting
        if last_known == 0:
            st.session_state.last_trade_ts[addr] = recent[0].get("timestamp", 0)
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
            msg = f'🚨 **{lbl}** vient d\'ouvrir une position sur "{market}" — {side} {size:.1f} shares à {price*100:.0f}¢'
            new_alerts.append({
                "msg": msg,
                "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "ts": ts,
            })
            st.toast(msg, icon="🚨")
        # Update last known timestamp
        latest_ts = recent[0].get("timestamp", 0)
        if latest_ts > last_known:
            st.session_state.last_trade_ts[addr] = latest_ts
    if new_alerts:
        st.session_state.alerts = (new_alerts + st.session_state.alerts)[:20]

poll_alerts()

# ── Refresh button ──
if wallets:
    st.divider()
    if st.button("🔄 Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
