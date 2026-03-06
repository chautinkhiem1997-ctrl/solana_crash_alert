import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import math

# --- 1. PAGE SETUP ---
st.set_page_config(page_title="Watchdog | Solana Terminal", page_icon="🛡️", layout="wide")

# --- 2. DATABASE CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(URL, KEY)

# --- 3. STYLING ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
    .stApp { 
        background-color: #050505;
        background-image: radial-gradient(circle at 50% 0%, #002200 0%, transparent 70%);
        color: #00ff41; font-family: 'Share Tech Mono', monospace;
    }
    [data-testid="stMetric"] { background-color: rgba(10, 10, 10, 0.9); border: 1px solid #00ff41; padding: 15px; border-radius: 5px; }
    h1, h2, h3, p, label { color: #00ff41 !important; font-family: 'Share Tech Mono', monospace; }
    .stTextInput input { background-color: #0a0a0a !important; color: #00ff41 !important; border: 1px solid #00ff41 !important; }
    /* Navigation Bar Styling */
    .nav-container { padding: 20px 0px; border-top: 1px solid rgba(0, 255, 65, 0.2); margin-top: 20px; }
    </style>
""", unsafe_allow_html=True)

# --- 4. DATA LOGIC ---
@st.cache_data(ttl=30)
def get_data():
    try:
        tokens = supabase.table("tokens").select("*").order("mcap", desc=True).execute().data
        latest_prices = supabase.table("prices").select("address, price, created_at").order("created_at", desc=True).limit(2000).execute().data
        price_map = {p['address']: p['price'] for p in latest_prices} if latest_prices else {}
        last_update = latest_prices[0]['created_at'] if latest_prices else None
        
        recent_ts = int(datetime.now().timestamp()) - 900 
        price_count = supabase.table("prices").select("address", count="exact").gte("ts", recent_ts).execute().count
        return tokens, price_map, (price_count if price_count else 0), last_update
    except Exception as e:
        st.error(f"🚨 Supabase Connection Error: {e}")
        return [], {}, 0, None

tokens, price_map, health_stat, last_ts = get_data()

# --- 5. INITIALIZE PAGE STATE ---
if 'current_page' not in st.session_state:
    st.session_state.current_page = 1

# --- 6. SIDEBAR ---
with st.sidebar:
    st.title("Control Panel")
    min_mcap = st.slider("Min Market Cap ($)", 0, 50000000, 1000000, step=500000, format="$%d")
    st.divider()
    st.status("Bot: Active" if health_stat > 0 else "Bot: Offline", state="complete" if health_stat > 0 else "error")
    if last_ts: st.caption(f"Last Price Update: {last_ts}")

# --- 7. MAIN INTERFACE ---
st.title("🛡️ Solana Sniper Command Center")

if tokens:
    df = pd.DataFrame(tokens)
    filtered_df = df[df['mcap'] >= min_mcap] if 'mcap' in df.columns else df

    # Search Box
    search_query = st.text_input("🔍 Search tokens...", "").strip().lower()
    if search_query:
        filtered_df = filtered_df[filtered_df['name'].str.lower().str.contains(search_query) | filtered_df['symbol'].str.lower().str.contains(search_query)]

    st.divider()

    # --- SETTINGS BEFORE TABLE ---
    batch_size = st.selectbox("Tokens per page:", [20, 50, 100, 500], index=1)
    total_rows = len(filtered_df)
    total_pages = max(math.ceil(total_rows / batch_size), 1)

    # Safety check for page bounds
    if st.session_state.current_page > total_pages:
        st.session_state.current_page = 1

    # Slice data
    start_idx = (st.session_state.current_page - 1) * batch_size
    end_idx = min(start_idx + batch_size, total_rows)
    display_df = filtered_df.iloc[start_idx:end_idx].copy()
    
    if not display_df.empty:
        # Format Data
        display_df['Price'] = display_df['address'].map(price_map).apply(lambda x: f"${x:.6f}" if pd.notnull(x) else "---")
        display_df['mcap_fmt'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")
        display_df['symbol_link'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

        # The Table
        st.dataframe(
            display_df[['symbol_link', 'name', 'Price', 'mcap_fmt', 'address']],
            column_config={
                "symbol_link": st.column_config.LinkColumn("Ticker", display_text=r"https://.*?#(.*)$", width="small"),
                "address": st.column_config.TextColumn("Contract Address", width="medium"),
            },
            width="stretch", height='content', hide_index=True
        )

        # --- 8. THE PRO NAVIGATION BAR (BOTTOM) ---
        st.markdown('<div class="nav-container"></div>', unsafe_allow_html=True)
        col_text, col_prev, col_next = st.columns([4, 1, 1])

        with col_text:
            st.write(f"Showing tokens **{start_idx + 1} - {end_idx}** of **{total_rows}**")

        with col_prev:
            if st.button("⬅️ Previous", disabled=(st.session_state.current_page == 1), use_container_width=True):
                st.session_state.current_page -= 1
                st.rerun()

        with col_next:
            if st.button("Next ➡️", disabled=(st.session_state.current_page == total_pages), use_container_width=True):
                st.session_state.current_page += 1
                st.rerun()
    else:
        st.warning("No tokens found matching your filters.")

st.divider()
if st.button('🔄 Force Manual Sync'):
    st.cache_data.clear()
    st.rerun()
