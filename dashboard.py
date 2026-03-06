import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import math

# --- 1. SETUP ---
st.set_page_config(page_title="Watchdog | Solana Terminal", page_icon="🛡️", layout="wide")
URL, KEY = st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
supabase = create_client(URL, KEY)

# --- 2. DATA LOGIC ---
@st.cache_data(ttl=30)
def get_data():
    try:
        tokens = supabase.table("tokens").select("*").order("mcap", desc=True).execute().data
        raw_prices = supabase.table("prices").select("address, price, ts").order("ts", desc=True).limit(15000).execute().data
        
        history_map = {}
        for p in raw_prices:
            addr = p['address']
            if addr not in history_map: history_map[addr] = []
            history_map[addr].append(p)
            
        current_prices = {addr: hist[0]['price'] for addr, hist in history_map.items()}
        recent_ts = int(datetime.now().timestamp()) - 600
        is_active = any(p['ts'] >= recent_ts for p in raw_prices) if raw_prices else False
        
        return tokens, current_prices, history_map, is_active
    except Exception as e:
        st.error(f"🚨 Supabase Error: {e}")
        return [], {}, {}, False

tokens, current_price_map, history_map, is_active = get_data()

# --- 3. MAIN INTERFACE ---
st.title("🛡️ Solana Sniper Command Center")

if tokens:
    df = pd.DataFrame(tokens)
    
    # --- 4. CLEAN CONTROL ROW (No more "Rows:" text above) ---
    # We use 3 columns to put everything on one line
    col_search, col_mcap, col_batch = st.columns([2, 1, 1])
    
    with col_search:
        search = st.text_input("🔍 Search tokens...", placeholder="Ticker or Name", label_visibility="collapsed")
    
    with col_mcap:
        mcap_filter = st.selectbox(
            "M-Cap Filter",
            options=["All", "> $1M", "> $10M", "> $50M", "< $1M"],
            label_visibility="collapsed"
        )

    with col_batch:
        # Changed "Rows:" to a clean dropdown with label_visibility="collapsed"
        batch_option = st.selectbox(
            "Tokens per page",
            options=[10, 20, 50, 100, 500, 1000, "All"],
            index=2, # Default to 50
            label_visibility="collapsed"
        )

    # --- 5. LOGIC FILTERS ---
    if search:
        df = df[df['name'].str.lower().str.contains(search) | df['symbol'].str.lower().str.contains(search)]
    
    if mcap_filter == "> $1M": df = df[df['mcap'] >= 1000000]
    elif mcap_filter == "> $10M": df = df[df['mcap'] >= 10000000]
    elif mcap_filter == "> $50M": df = df[df['mcap'] >= 50000000]
    elif mcap_filter == "< $1M": df = df[df['mcap'] < 1000000]

    # Timeframe Slider
    selected_tf = st.select_slider("Select Watchdog Window:", options=[5, 10, 30, 60, 120, 180], value=5)

    # Calculation Engine
    def get_move_pct(row, mins):
        addr = row['address']
        curr_p = current_price_map.get(addr, 0)
        token_history = history_map.get(addr, [])
        if len(token_history) < 2 or curr_p == 0: return 0.0
        target_ts = int(datetime.now().timestamp()) - (mins * 60)
        past_entry = next((p for p in token_history if p['ts'] <= target_ts), token_history[-1])
        past_p = past_entry['price']
        if past_p == 0: return 0.0
        return ((curr_p - past_p) / past_p) * 100

    df['Move %'] = df.apply(lambda x: get_move_pct(x, selected_tf), axis=1)

    # Pagination Setup
    total_rows = len(df)
    batch_size = total_rows if batch_option == "All" else int(batch_option)
    total_pages = max(math.ceil(total_rows / batch_size), 1)
    
    if 'page' not in st.session_state: st.session_state.page = 1
    if st.session_state.page > total_pages: st.session_state.page = 1
    
    start_idx = (st.session_state.page - 1) * batch_size
    display_df = df.iloc[start_idx : start_idx + batch_size].copy()

    # Format
    display_df['Price'] = display_df['address'].map(current_price_map).apply(lambda x: f"${x:.6f}" if x else "---")
    display_df['mcap_fmt'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")
    display_df['Ticker'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

    # --- 6. THE TABLE ---
    st.dataframe(
        display_df[['Ticker', 'name', 'Price', 'mcap_fmt', 'Move %']],
        column_config={
            "Ticker": st.column_config.LinkColumn("Ticker", display_text=r"https://.*?#(.*)$"),
            "Move %": st.column_config.NumberColumn(f"{selected_tf}m Move", format="%.2f%%"),
        },
        width="stretch", height='content', hide_index=True
    )

    # --- 7. BOTTOM NAV (Style: image_f71a88.png) ---
    st.markdown("---")
    c1, c2, c3 = st.columns([4, 1, 1])
    c1.write(f"Showing **{start_idx + 1} - {min(start_idx + batch_size, total_rows)}** of **{total_rows}** tokens")
    
    if c2.button("⬅️ Previous", disabled=(st.session_state.page == 1), use_container_width=True):
        st.session_state.page -= 1
        st.rerun()
    if c3.button("Next ➡️", disabled=(st.session_state.page == total_pages), use_container_width=True):
        st.session_state.page += 1
        st.rerun()

st.sidebar.status("Bot: Active" if is_active else "Bot: Offline", state="complete" if is_active else "error")
if st.button('🔄 Force Manual Sync'):
    st.cache_data.clear()
    st.rerun()
