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

# --- DEBUG SETTINGS ---
TOTAL_MONITOR = 1000        # Dropped to 1000 for testing - let's make sure it works first!
DROP_THRESHOLD = 0.01       # 1% for testing as you requested
MIN_VOLUME_24H = 50000

def sync_tokens():
    print(f"[{datetime.now()}] 🔄 Syncing tokens from Birdeye...", flush=True)
    url = "https://public-api.birdeye.so/defi/v3/token/list"
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    
    discovered = []
    # Fetching in larger chunks (limit=50) to speed up
    for offset in range(0, TOTAL_MONITOR, 50):
        params = {
            "sort_by": "market_cap", "sort_type": "desc",
            "min_market_cap": 500000, "min_liquidity": 15000,
            "min_volume_24h_usd": MIN_VOLUME_24H,
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
            print(f"  > Offset {offset} reached...", flush=True)
            time.sleep(0.5)
        except Exception as e:
            print(f"Sync Error: {e}", flush=True)
            break

    if discovered:
        print(f"Pushing {len(discovered)} tokens to Supabase...", flush=True)
        supabase.table("tokens").delete().neq("address", "0").execute()
        supabase.table("tokens").insert(discovered).execute()
        print("✅ Sync Complete.", flush=True)

def check_for_drops():
    print(f"[{datetime.now()}] 📈 Price Check Started...", flush=True)
    tokens = supabase.table("tokens").select("*").execute().data
    if not tokens:
        print("❌ No tokens found in database. Syncing now...", flush=True)
        sync_tokens()
        return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    
    # We only check first 100 for this test to ensure it finishes
    for t in tokens[:100]:
        addr = t['address']
        # Fetch current price from DexScreener
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=10)
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if not pairs: continue
                curr_p = float(pairs[0].get("priceUsd", 0))
                
                # Check history
                old_res = supabase.table("prices").select("price").eq("address", addr).order("ts", desc=False).limit(1).execute().data
                if old_res:
                    old_p = old_res[0]['price']
                    change = (old_p - curr_p) / old_p
                    if change >= DROP_THRESHOLD:
                        send_test_alert(t, change, curr_p)

                # Save new price
                supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()
        except Exception as e:
            print(f"Err for {t['symbol']}: {e}", flush=True)

def send_test_alert(t, drop, price):
    msg = f"🔔 TEST ALERT: {t['symbol']} changed {drop*100:.1f}%\nPrice: ${price:.8f}"
    try:
        telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg)
        print(f"✅ Alert sent for {t['symbol']}", flush=True)
    except: pass

if __name__ == "__main__":
    print("🚀 BOOTING UP...", flush=True)
    try:
        # Check database connection immediately
        res = supabase.table("tokens").select("count", count="exact").execute()
        print(f"Connected. DB has {res.count} tokens.", flush=True)
        check_for_drops()
    except Exception as e:
        print(f"🛑 CONNECTION ERROR: {e}", flush=True)
