import os
import requests
import sqlite3
import time
from datetime import datetime
import telebot
from supabase import create_client, Client

# ================== CLOUD SETTINGS ==================
# These pull from your GitHub Secrets automatically
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BIRDEYE_API_KEY = os.environ.get("BIRDEYE_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Logic Filters
TOTAL_MONITOR = 5000        
MIN_MCAP = 300000           
MAX_MCAP = 1000000000       
MIN_LIQUIDITY = 15000       
MIN_VOLUME_24H = 50000      
DROP_THRESHOLD = 0.001       
TIMEFRAME_MINUTES = 120    
# ====================================================

def get_token_security(address):
    url = f"https://public-api.birdeye.so/defi/token_security?address={address}"
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", {})
            risks = []
            if data.get("mintable"): risks.append("🚫 MINTABLE")
            if data.get("freezable"): risks.append("❄️ FREEZE AUTH")
            if not data.get("ownerRenounced"): risks.append("🔑 NOT RENOUNCED")
            return " | ".join(risks) if risks else "✅ SECURE"
    except: pass
    return "❓ UNKNOWN"

def sync_tokens():
    """Daily Sync: Updates the 5,000 token list in Supabase."""
    print("Deep-scanning 5,000 tokens...")
    url = "https://public-api.birdeye.so/defi/v3/token/list"
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    
    discovered = []
    for offset in range(0, TOTAL_MONITOR, 50):
        params = {
            "sort_by": "market_cap", "sort_type": "desc",
            "min_market_cap": MIN_MCAP, "max_market_cap": MAX_MCAP,
            "min_liquidity": MIN_LIQUIDITY, "min_volume_24h_usd": MIN_VOLUME_24H,
            "offset": offset, "limit": 50 
        }
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("items", [])
                for item in items:
                    discovered.append({
                        "address": item.get("address"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "mcap": item.get("mc") or 0,
                        "unique_wallets": item.get("uniqueWallet24h") or 0
                    })
            time.sleep(0.5)
        except: break

    if discovered:
        # Update Supabase
        supabase.table("tokens").delete().neq("address", "0").execute()
        supabase.table("tokens").insert(discovered).execute()
        print(f"Sync Complete: {len(discovered)} tokens.")

def check_for_drops():
    print("Checking for crashes...")
    # Get tokens and prices
    tokens = supabase.table("tokens").select("*").execute().data
    addrs = [t['address'] for t in tokens]
    
    # Batch fetch prices from DexScreener
    current_prices = {}
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i+30]
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}")
        if r.status_code == 200:
            for pair in r.json().get("pairs", []):
                current_prices[pair.get("baseToken", {}).get("address")] = float(pair.get("priceUsd", 0))
        time.sleep(0.2)

    now = int(time.time())
    cutoff = now - (TIMEFRAME_MINUTES * 60)

    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue

        # Compare with old price in Supabase
        old_res = supabase.table("prices").select("price").eq("address", addr).order("ts", desc=False).limit(1).execute().data
        
        if old_res:
            old_p = old_res[0]['price']
            drop = (old_p - curr_p) / old_p
            if drop >= DROP_THRESHOLD:
                security = get_token_security(addr)
                send_alert(t, drop, curr_p, security)

        # Update price history in Supabase
        supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()

    # Clean old prices
    supabase.table("prices").delete().lt("ts", cutoff).execute()

def send_alert(t, drop, price, security):
    # Determine the status
    is_dip = t['unique_wallets'] >= 300  # community check
    header = "✅ **BUY THE DIP OPPORTUNITY**" if is_dip else "🚨 **CRASH ALERT**"
    risk_tag = f"🚨 **HIGH RISK: {security}**" if "✅" not in security else "🛡️ Security: Clean"
    
    # Use f-string with TRIPLE QUOTES for multi-line messages
    msg = f"""{header}

**{t['name']} ({t['symbol']})**
💰 Cap: **${t['mcap']/1_000_000:.1f}M** | 👥 Wallets: **{t['unique_wallets']:,}**
📉 Drop: **-{drop*100:.1f}%** (2h window)
💵 Price: **${price:.8f}**

📍 **CA:** `{t['address']}`
{risk_tag}

🔍 [RugCheck](https://rugcheck.xyz/tokens/{t['address']})
📈 [DexScreener](https://dexscreener.com/solana/{t['address']})"""

    try:
        # Use disable_web_page_preview to keep the message clean
        telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown', disable_web_page_preview=True)
        print(f"Alert sent for {t['symbol']}")
    except Exception as e:
        print(f"Telegram Error: {e}")
