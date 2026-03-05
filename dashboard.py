import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime

# --- 1. PAGE SETUP ---
st.set_page_config(page_title="Watchdog | Solana Terminal", page_icon="🛡️", layout="wide")

# --- 2. SECURE DATABASE CONNECTION ---
# These pull from Streamlit's "Advanced Settings > Secrets" vault
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(URL, KEY)

# --- 3. PRO UI STYLING ---
st.markdown("""
    <style>
    .stApp { background-color: #0b0e11; color: #ffffff; }
    [data-testid="stMetric"] { background-color: #161b22; border: 1px solid #30363d; padding: 15px; border-radius: 12px; }
    [data-testid="stDataFrame"] { border: 1px solid #30363d; border-radius: 10px; }
    </style>
""", unsafe_allow_html=True)

# --- 4. DATA LOGIC ---
@st.cache_data(ttl=30)
def get_data():
    try:
        tokens = supabase.table("tokens").select("*").order("mcap", desc=True).execute().data
        hour_ago = int(datetime.now().timestamp()) - 3600
        price_count = supabase.table("prices").select("address", count="exact").gte("ts", hour_ago).execute().count
        
        # Sometimes count returns None if empty, so we ensure it's a number
        if price_count is None: 
            price_count = 0
            
        return tokens, price_count
    except Exception as e:
        # 🚨 THIS will print the actual hidden error to your dashboard!
        st.error(f"🚨 Supabase Connection Error: {e}")
        return [], 0

tokens, health_stat = get_data()

# --- 5. SIDEBAR ---
with st.sidebar:
    st.image("https://cryptologos.cc/logos/solana-sol-logo.png", width=50)
    st.title("Control Panel")
    min_mcap = st.slider("Min Market Cap ($)", 0, 50000000, 1000000, step=500000)
    st.divider()
    st.write("### System Health")
    st.status("Bot: Active" if health_stat > 0 else "Bot: Offline", state="complete" if health_stat > 0 else "error")
    st.write(f"Signals/hr: {health_stat}")

# --- 6. MAIN INTERFACE ---
st.title("🛡️ Solana Watchdog: Jupiter Command Center")

if tokens:
    df = pd.DataFrame(tokens)
    filtered_df = df[df['mcap'] >= min_mcap] if 'mcap' in df.columns else df

    m1, m2, m3 = st.columns(3)
    m1.metric("Tracked Tokens", f"{len(df)}")
    m2.metric("Filtered View", f"{len(filtered_df)}")
    avg_mcap = filtered_df['mcap'].mean() if not filtered_df.empty else 0
    m3.metric("Avg Market Cap", f"${avg_mcap/1e6:.1f}M")

    st.divider()
    tabs = st.tabs(["📋 Token List", "📉 Recent Crashes"])
    
    with tabs[0]:
        st.dataframe(
            filtered_df[['symbol', 'name', 'mcap', 'address']],
            column_config={
                "symbol": st.column_config.TextColumn("Ticker", width="small"),
                "name": "Token Name",
                "mcap": st.column_config.NumberColumn("Market Cap", format="$%d"),
                "address": st.column_config.TextColumn("Contract Address", width="medium"),
            },
            use_container_width=True, hide_index=True
        )

    with tabs[1]:
        st.subheader("🚨 Recent Token Crashes")
        crashed_tokens = supabase.table("tokens").select("address, symbol, name, last_alert_ts").gt("last_alert_ts", 0).order("last_alert_ts", desc=True).execute().data

        if not crashed_tokens:
            st.info("✅ No crashes detected yet!")
        else:
            token_options = {f"{t['symbol']} - {t['name']}": t['address'] for t in crashed_tokens}
            selected_label = st.selectbox("Select a crashed token:", list(token_options.keys()))
            selected_address = token_options[selected_label]
            
            prices_data = supabase.table("prices").select("price, created_at").eq("address", selected_address).order("created_at", desc=False).execute().data
            
            if prices_data:
                price_df = pd.DataFrame(prices_data)
                price_df['created_at'] = pd.to_datetime(price_df['created_at'])
                price_df.set_index('created_at', inplace=True)
                st.line_chart(price_df['price'], color="#ff4b4b") 
            else:
                st.warning("No price history available.")
else:
    st.error("No data. Run the sync_bot first.")

if st.button('🔄 Sync Data Now'):
    st.cache_data.clear()
    st.rerun()
