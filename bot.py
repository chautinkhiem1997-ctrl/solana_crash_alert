import os, requests, time, sys
from datetime import datetime
import telebot
from supabase import create_client, Client

# --- CLOUD CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BIRDEYE_API_KEY = os.environ.get("BIRDEYE_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- SETTINGS ---
TOTAL_MONITOR = 5000        
DROP_THRESHOLD = 0.30       
MIN_MCAP = 1000000          
MIN_LIQUIDITY = 20000       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 30, 60, 120]

def sync_tokens():
    print(f"[{datetime.now()}] 🔄 Syncing 5,000 Tokens with Liquidity Data...", flush=True)
    url = "https://public-api.birdeye.so/defi/v3/token/list"
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    
    discovered = []
    for offset in range(0, TOTAL_MONITOR, 50):
        params = {
            "sort_by": "market_cap", "sort_type": "desc",
            "min_market_cap": MIN_MCAP, "min_liquidity": MIN_LIQUIDITY,
            "offset": offset, "limit": 50 
        }
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("items", [])
                for item in items:
                    # FETCHING REAL MCAP AND LIQUIDITY
                    mcap_val = item.get("market_cap") or item.get("fdv") or 0
                    discovered.append({
                        "address": item.get("address"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "mcap": mcap_val,
                        "v24h": item.get("v24h") or 0,
                        "liquidity": item.get("liquidity") or 0,
                        "last_alert_ts": 0
                    })
            time.sleep(0.4) 
        except Exception as e:
            print(f"❌ Sync Error: {e}")
            break

    if discovered:
        # UPSERT handles adding the new 'liquidity' column automatically
        supabase.table("tokens").upsert(discovered, on_conflict="address").execute()
        print(f"✅ Sync Complete.", flush=True)

def check_for_drops():
    print(f"\n[{datetime.now()}] 📈 Checking Prices...", flush=True)
    tokens = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute().data
    if not tokens: return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    current_prices = {}

    for i in range(0, len(addrs), 30):
        batch = addrs[i:i+30]
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}", timeout=15)
            if r.status_code == 200:
                for pair in r.json().get("pairs", []):
                    current_prices[pair.get("baseToken", {}).get("address")] = float(pair.get("priceUsd", 0))
        except: pass
        time.sleep(0.1)

    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue
        supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()

        last_alert = t.get('last_alert_ts') or 0
        if (now - last_alert) < COOLDOWN_SECONDS: continue

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            old_res = supabase.table("prices").select("price").eq("address", addr).gte("ts", cutoff - 350).lte("ts", cutoff + 350).order("ts", desc=False).limit(1).execute().data
            if old_res:
                old_p = old_res[0]['price']
                drop = (old_p - curr_p) / old_p
                if drop >= DROP_THRESHOLD:
                    send_alert(t, drop, curr_p)
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    break 

    supabase.table("prices").delete().lt("ts", now - 10800).execute()

def send_alert(t, drop, price):
    msg = f"🚨 **CRASH ALERT**\n**Tokens:** {t['symbol']}\n**Drop:** -{drop*100:.1f}%\n**CA:** `{t['address']}`"
    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown')
    except: pass

if __name__ == "__main__":
    sync_tokens()
    check_for_drops()
