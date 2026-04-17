import streamlit as st
import requests
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

@st.cache_data(ttl=60)
def fetch_value(address):
    r = requests.get(f"{DATA_API}/value", params={"user": address.lower()}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data and len(data) > 0:
        return float(data[0].get("value", 0))
    return 0.0

# ── DB helpers ──
def load_wallets():
    res = db.table("wallets").select("*").order("created_at").execute()
    return res.data

def add_wallet(address, label):
    db.table("wallets").insert({"address": address.lower(), "label": label}).execute()

def remove_wallet(address):
    db.table("wallets").delete().eq("address", address.lower()).execute()

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
</style>
""", unsafe_allow_html=True)

st.title("📊 Polymarket Wallet Tracker")
st.caption("Track positions, trades & P&L across proxy wallets")

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
            total_pnl = fetch_value(addr)
        except Exception as e:
            st.error(f"Failed to fetch data for {lbl}: {e}")
            continue

        # ── Stats ──
        wins = [t for t in trades if (t.get("side", "").upper() == "BUY" and float(t.get("price", 1)) < 0.5) or (t.get("side", "").upper() == "SELL" and float(t.get("price", 0)) > 0.5)]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0

        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Total P&L", f"${total_pnl:+.2f}")
        mcol2.metric("Win Rate", f"{win_rate:.1f}%")
        mcol3.metric("Open Positions", len(positions))

        # ── Tabs ──
        tab_pos, tab_trades = st.tabs([f"📈 Positions ({len(positions)})", f"🔄 Recent Trades ({len(trades)})"])

        with tab_pos:
            if not positions:
                st.info("No open positions")
            else:
                rows = []
                for p in positions:
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
                    from datetime import datetime
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

# ── Refresh button ──
if wallets:
    st.divider()
    if st.button("🔄 Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
