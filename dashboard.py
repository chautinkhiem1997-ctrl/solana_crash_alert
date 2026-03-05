import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import math

# --- 1. PAGE SETUP ---
st.set_page_config(page_title="Watchdog | Solana Terminal", page_icon="🛡️", layout="wide")

# --- 2. SECURE DATABASE CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(URL, KEY)

# --- 3. HIGH-TECH TERMINAL STYLING ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
    .stApp { 
        background-color: #050505;
        background-image: 
            radial-gradient(circle at 50% 0%, #002200 0%, transparent 70%),
            linear-gradient(rgba(0, 255, 65, 0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 255, 65, 0.03) 1px, transparent 1px);
        background-size: 100% 100%, 30px 30px, 30px 30px;
        color: #00ff41; font-family: 'Share Tech Mono', monospace;
    }
    [data-testid="stMetric"] { background-color: rgba(10, 10, 10, 0.9); border: 1px solid #00ff41; padding: 15px; border-radius: 5px; }
    h1, h2, h3, p, label { color: #00ff41 !important; font-family: 'Share Tech Mono', monospace; }
    [data-testid="stSidebar"] { background-color: rgba(5, 5, 5, 0.95); border-right: 1px solid #00ff41; }
    /* Style for the search box to match the theme */
    .stTextInput input { background-color: #0a0a0a !important; color: #00ff41 !important; border: 1px solid #00ff41 !important; }
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

# --- 5. SIDEBAR ---
with st.sidebar:
    st.image("https://cryptologos.cc/logos/solana-sol-logo.png", width=50)
    st.title("Control Panel")
    min_mcap = st.slider("Min Market Cap ($)", 0, 50000000, 1000000, step=500000, format="$%d")
    st.divider()
    st.write("### System Health")
    st.status("Bot: Active" if health_stat > 0 else "Bot: Offline", state="complete" if health_stat > 0 else "error")
    if last_ts:
        st.caption(f"Last Price Update: {last_ts}")

# --- 6. MAIN INTERFACE ---
st.title("🛡️ Solana Sniper Command Center")

if tokens:
    df = pd.DataFrame(tokens)
    
    # Apply M-Cap Filter
    filtered_df = df[df['mcap'] >= min_mcap] if 'mcap' in df.columns else df

    # --- 🔍 SEARCH BOX LOGIC ---
    search_query = st.text_input("🔍 Search by Token Name, Symbol, or Address:", "").strip().lower()
    if search_query:
        filtered_df = filtered_df[
            filtered_df['name'].str.lower().str.contains(search_query) | 
            filtered_df['symbol'].str.lower().str.contains(search_query) |
            filtered_df['address'].str.lower().str.contains(search_query)
        ]

    # Metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Tokens", f"{len(df)}")
    m2.metric("Filtered/Search", f"{len(filtered_df)}")
    avg_mcap = filtered_df['mcap'].mean() if not filtered_df.empty else 0
    m3.metric("Avg M-Cap", f"${avg_mcap/1e6:.1f}M")

    st.divider()
    
    # --- NAVIGATION CONTROLS ---
    nav1, nav2, nav3 = st.columns([2, 2, 4])
    with nav1:
        batch_size_option = st.selectbox("Show rows:", [20, 50, 100, 500, "All"], index=1)
    
    total_rows = len(filtered_df)
    if batch_size_option == "All":
        batch_size = total_rows if total_rows > 0 else 1
        total_pages = 1
    else:
        batch_size = int(batch_size_option)
        total_pages = math.ceil(total_rows / batch_size) if total_rows > 0 else 1

    with nav2:
        current_page = st.number_input(f"Page (of {total_pages})", min_value=1, max_value=total_pages, step=1)

    # Slice data
    start_idx = (current_page - 1) * batch_size
    end_idx = start_idx + batch_size
    display_df = filtered_df.iloc[start_idx:end_idx].copy()
    
    if not display_df.empty:
        # Format Data
        display_df['Price'] = display_df['address'].map(price_map).apply(lambda x: f"${x:.6f}" if pd.notnull(x) else "---")
        display_df['mcap_fmt'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")
        display_df['symbol_link'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

        # Table stretches to fit rows exactly
        st.dataframe(
            display_df[['symbol_link', 'name', 'Price', 'mcap_fmt', 'address']],
            column_config={
                "symbol_link": st.column_config.LinkColumn("Ticker", display_text=r"https://.*?#(.*)$", width="small"),
                "name": "Token Name",
                "Price": "Current Price",
                "mcap_fmt": "Market Cap",
                "address": st.column_config.TextColumn("Contract Address", width="medium"),
            },
            width="stretch", 
            height='content', 
            hide_index=True
        )
        st.write(f"📊 Displaying {start_idx + 1} to {min(end_idx, total_rows)} of {total_rows} results")
    else:
        st.warning("No tokens found matching your search or filters.")

if st.button('🔄 Force Manual Sync'):
    st.cache_data.clear()
    st.rerun()
