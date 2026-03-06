import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import math

# --- 1. SETUP ---
st.set_page_config(page_title="Watchdog | Solana Terminal", page_icon="🛡️", layout="wide")
URL, KEY = st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
supabase = create_client(URL, KEY)

# --- 2. DATA LOGIC (MULTI-TIMEFRAME) ---
@st.cache_data(ttl=30)
def get_data():
    try:
        # Get tokens and the most recent 3 hours of prices to cover all timeframes
        tokens = supabase.table("tokens").select("*").order("mcap", desc=True).execute().data
        latest_prices = supabase.table("prices").select("address, price, mcap, created_at, ts").order("created_at", desc=True).limit(5000).execute().data
        
        price_map = {p['address']: p['price'] for p in latest_prices[:2000]} # Current prices
        
        # Calculate health (Active if price updated in last 5 mins)
        recent_ts = int(datetime.now().timestamp()) - 300
        health_check = any(p['ts'] >= recent_ts for p in latest_prices)
        
        return tokens, price_map, health_check, latest_prices
    except Exception as e:
        st.error(f"🚨 Supabase Connection Error: {e}")
        return [], {}, False, []

tokens, price_map, is_active, all_prices = get_data()

# --- 3. MAIN INTERFACE ---
st.title("🛡️ Solana Sniper Command Center")

with st.sidebar:
    st.title("Control Panel")
    # 🔥 TIMEFRAME SELECTOR (Matches Watchdog Logic)
    selected_tf = st.selectbox(
        "Select Monitoring Window (Mins):", 
        [5, 10, 15, 30, 60, 120, 180], 
        index=0
    )
    st.divider()
    st.status("Bot: Active" if is_active else "Bot: Offline", state="complete" if is_active else "error")

if tokens:
    df = pd.DataFrame(tokens)
    
    # --- DYNAMIC CALCULATION BASED ON SELECTED TIMEFRAME ---
    def calc_tf_move(row, timeframe_mins):
        addr = row['address']
        current_mcap = row['mcap']
        
        # Find the price point from X minutes ago
        target_ts = int(datetime.now().timestamp()) - (timeframe_mins * 60)
        # Filter all_prices for this address and find the one closest to the target_ts
        past_data = [p for p in all_prices if p['address'] == addr and p['ts'] <= target_ts]
        
        if not past_data: return 0.0
        past_mcap = past_data[0]['mcap'] # The mcap at that specific timeframe
        
        if past_mcap == 0: return 0.0
        return ((current_mcap - past_mcap) / past_mcap) * 100

    # Calculate the move for the column
    df['Move %'] = df.apply(lambda x: calc_tf_move(x, selected_tf), axis=1)

    # --- SEARCH & PAGINATION ---
    search = st.text_input("🔍 Search tokens...", "").lower()
    if search:
        df = df[df['name'].str.lower().str.contains(search) | df['symbol'].str.lower().str.contains(search)]

    batch_size = st.selectbox("Rows:", [20, 50, 100], index=1)
    total_pages = max(math.ceil(len(df) / batch_size), 1)
    
    if 'page' not in st.session_state: st.session_state.page = 1
    display_df = df.iloc[(st.session_state.page-1)*batch_size : st.session_state.page*batch_size].copy()

    # Formatting
    display_df['Price'] = display_df['address'].map(price_map).apply(lambda x: f"${x:.6f}" if x else "---")
    display_df['mcap_fmt'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")
    display_df['Ticker'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

    # --- THE TABLE ---
    st.dataframe(
        display_df[['Ticker', 'name', 'Price', 'mcap_fmt', 'Move %']],
        column_config={
            "Ticker": st.column_config.LinkColumn("Ticker", display_text=r"https://.*?#(.*)$"),
            "Move %": st.column_config.NumberColumn(f"{selected_tf}m Move", format="%.2f%%"),
        },
        width="stretch", height='content', hide_index=True
    )

    # --- FOOTER NAV ---
    col_text, col_prev, col_next = st.columns([4, 1, 1])
    col_text.write(f"Displaying {len(display_df)} tokens on {selected_tf}m window")
    
    if col_prev.button("⬅️ Prev", disabled=(st.session_state.page == 1)):
        st.session_state.page -= 1
        st.rerun()
    if col_next.button("Next ➡️", disabled=(st.session_state.page == total_pages)):
        st.session_state.page += 1
        st.rerun()

if st.button('🔄 Force Manual Sync'):
    st.cache_data.clear()
    st.rerun()
