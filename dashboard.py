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
        # Fetch deep history (12k rows) for the 180m window
        raw_prices = supabase.table("prices").select("address, price, ts").order("ts", desc=True).limit(12000).execute().data
        
        current_prices = {p['address']: p['price'] for p in raw_prices[:1500]}
        history_map = {}
        for p in raw_prices:
            addr = p['address']
            if addr not in history_map: history_map[addr] = []
            history_map[addr].append(p)
            
        recent_ts = int(datetime.now().timestamp()) - 420
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
    
    # --- 4. THE SELECTOR ---
    selected_tf = st.select_slider(
        "Analysis Window (Minutes):",
        options=[5, 10, 30, 60, 120, 180],
        value=5
    )

    # --- 5. CALCULATION ---
    def get_move_pct(row, mins):
        addr = row['address']
        curr_p = current_price_map.get(addr, 0)
        token_history = history_map.get(addr, [])
        if not token_history or curr_p == 0: return 0.0
        
        target_ts = int(datetime.now().timestamp()) - (mins * 60)
        past_entry = next((p for p in token_history if p['ts'] <= target_ts), token_history[-1])
        past_p = past_entry['price']
        
        if past_p == 0: return 0.0
        return ((curr_p - past_p) / past_p) * 100

    df['Move %'] = df.apply(lambda x: get_move_pct(x, selected_tf), axis=1)

    # Search & Pagination
    search = st.text_input("🔍 Search tokens...", "").lower()
    if search:
        df = df[df['name'].str.lower().str.contains(search) | df['symbol'].str.lower().str.contains(search)]

    if 'page' not in st.session_state: st.session_state.page = 1
    batch_size = st.selectbox("Rows per page:", [50, 100, 500], index=0)
    total_pages = max(math.ceil(len(df) / batch_size), 1)
    
    start_idx = (st.session_state.page - 1) * batch_size
    display_df = df.iloc[start_idx : start_idx + batch_size].copy()

    # Formatting
    display_df['Price'] = display_df['address'].map(current_price_map).apply(lambda x: f"${x:.6f}" if x else "---")
    display_df['mcap_fmt'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")
    display_df['Ticker'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

    # --- 6. THE COLORED TABLE ---
    st.dataframe(
        display_df[['Ticker', 'name', 'Price', 'mcap_fmt', 'Move %']],
        column_config={
            "Ticker": st.column_config.LinkColumn("Ticker", display_text=r"https://.*?#(.*)$"),
            # 🔥 RED FOR DOWN, GREEN FOR UP 🔥
            "Move %": st.column_config.NumberColumn(
                f"{selected_tf}m Move", 
                format="%.2f%%"
            ),
        },
        width="stretch", 
        height='content', 
        hide_index=True
    )

    # --- 7. BOTTOM NAV ---
    st.markdown("---")
    c1, c2, c3 = st.columns([4, 1, 1])
    c1.write(f"Showing **{start_idx + 1} - {min(start_idx + batch_size, len(df))}** of **{len(df)}**")
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
