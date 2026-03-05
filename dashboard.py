import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime

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
        color: #00ff41; 
        font-family: 'Share Tech Mono', monospace;
    }

    [data-testid="stMetric"] { 
        background-color: rgba(10, 10, 10, 0.9); 
        border: 1px solid #00ff41; 
        padding: 15px; 
        border-radius: 5px; 
        box-shadow: 0px 0px 10px rgba(0, 255, 65, 0.15);
    }
    
    [data-testid="stMetricValue"] {
        color: #00ff41 !important;
        text-shadow: 0px 0px 8px rgba(0, 255, 65, 0.5);
    }

    h1, h2, h3, p, label {
        color: #00ff41 !important;
        font-family: 'Share Tech Mono', monospace;
    }
    
    [data-testid="stSidebar"] {
        background-color: rgba(5, 5, 5, 0.95);
        border-right: 1px solid #00ff41;
    }
    </style>
""", unsafe_allow_html=True)

# --- 4. DATA LOGIC ---
@st.cache_data(ttl=30)
def get_data():
    try:
        tokens = supabase.table("tokens").select("*").order("mcap", desc=True).execute().data
        hour_ago = int(datetime.now().timestamp()) - 3600
        price_count = supabase.table("prices").select("address", count="exact").gte("ts", hour_ago).execute().count
        
        if price_count is None: 
            price_count = 0
            
        return tokens, price_count
    except Exception as e:
        st.error(f"🚨 Supabase Connection Error: {e}")
        return [], 0

tokens, health_stat = get_data()

# --- 5. SIDEBAR ---
with st.sidebar:
    st.image("https://cryptologos.cc/logos/solana-sol-logo.png", width=50)
    st.title("Control Panel")
    min_mcap = st.slider("Min Market Cap ($)", 0, 50000000, 1000000, step=500000, format="$%d")
    st.divider()
    st.write("### System Health")
    st.status("Bot: Active" if health_stat > 0 else "Bot: Offline", state="complete" if health_stat > 0 else "error")
    st.write(f"Prices grabbed last hr: {health_stat}")

# --- 6. MAIN INTERFACE ---
st.title("🛡️ Solana Sniper Command Center")

if tokens:
    df = pd.DataFrame(tokens)
    filtered_df = df[df['mcap'] >= min_mcap] if 'mcap' in df.columns else df

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Verified Tokens", f"{len(df)}")
    m2.metric("Tokens in Filter", f"{len(filtered_df)}")
    avg_mcap = filtered_df['mcap'].mean() if not filtered_df.empty else 0
    m3.metric("Avg Market Cap", f"${avg_mcap/1e6:.1f}M")

    st.divider()
    tabs = st.tabs(["📋 Token Tracker", "📉 Crash Alerts"])
    
    with tabs[0]:
        row_limit = st.selectbox("Tokens to display:", [10, 20, 50, 100, 1000], index=1)
        display_df = filtered_df.head(row_limit)[['symbol', 'name', 'mcap', 'address']].copy()
        display_df['mcap'] = display_df['mcap'].apply(lambda x: f"${int(x):,}")

        # 🔥 THE URL HACK: Turn symbol into a DexScreener link, but hide the symbol after a '#'
        display_df['symbol'] = "https://dexscreener.com/solana/" + display_df['address'] + "#" + display_df['symbol']

        st.dataframe(
            display_df,
            column_config={
                "symbol": st.column_config.LinkColumn(
                    "Ticker", 
                    display_text=r"https://.*?#(.*)$", # Extracts ONLY the symbol after the # to display it
                    width="small"
                ),
                "name": "Token Name",
                "mcap": st.column_config.TextColumn("Market Cap"), 
                "address": st.column_config.TextColumn("Contract Address", width="medium"),
            },
            width="stretch", 
            height=800,
            hide_index=True
        )

    with tabs[1]:
        st.subheader("🚨 Recent Token Crashes")
        crashed_tokens = supabase.table("tokens").select("address, symbol, name, last_alert_ts").gt("last_alert_ts", 0).order("last_alert_ts", desc=True).execute().data

        if not crashed_tokens:
            st.info("✅ No crashes detected in the current timeframe!")
        else:
            token_options = {f"{t['symbol']} - {t['name']}": t['address'] for t in crashed_tokens}
            selected_label = st.selectbox("Select a crashed token:", list(token_options.keys()))
            selected_address = token_options[selected_label]
            
            prices_data = supabase.table("prices").select("price, created_at").eq("address", selected_address).order("created_at", desc=False).execute().data
            
            if prices_data:
                price_df = pd.DataFrame(prices_data)
                price_df['created_at'] = pd.to_datetime(price_df['created_at'])
                price_df.set_index('created_at', inplace=True)
                st.line_chart(price_df['price'], color="#00ff41") 
            else:
                st.warning("No price history available.")
else:
    st.error("No data available. Waiting for Sync Bot to populate Supabase.")

if st.button('🔄 Force Manual Sync'):
    st.cache_data.clear()
    st.rerun()
