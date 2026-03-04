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

# --- SETTINGS (Lowered to bypass API 400 limits) ---
TOTAL_MONITOR = 1000        # Birdeye usually caps offset + limit at 1000 or 2000
DROP_THRESHOLD = 0.30       
MIN_MCAP = 1000000          
MIN_LIQUIDITY = 20000       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 30, 60, 120]

def sync_tokens():
    print(f"[{datetime.now()}] 🔄 Starting Sync for {TOTAL_MONITOR} Tokens...", flush=True)
    url = "https://public-api.birdeye.so/defi/v3/token/list"
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY, 
        "x-chain": "solana",
        "accept": "application/json"
    }
    
    discovered = []
    total_processed = 0 
    
    for offset in range(0, TOTAL_MONITOR, 50):
        params = {
            "sort_by": "market_cap", 
            "sort_type": "desc",
            "min_market_cap": int(MIN_MCAP),
            "min_liquidity": int(MIN_LIQUIDITY),
            "offset": int(offset), 
            "limit": 50 
        }
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            
            # DIAGNOSTIC LOG
            print(f"DEBUG: Offset {offset} | Status {r.status_code} | Len: {len(r.text)}", flush=True)
            
            # If we hit a 400, we've reached the API's limit. STOP here and save what we have.
            if r.status_code == 400:
                print(f"🛑 API LIMIT REACHED: Offset {offset} is too high for your API tier. Stopping sync.", flush=True)
                break

            if r.status_code == 200:
                data = r.json().get("data", {})
                items = data.get("items", [])
                
                if not items:
                    print(f"⚠️ No tokens found at offset {offset}.", flush=True)
                    continue
                
                for item in items:
                    mcap_val = item.get("market_cap") or item.get("fdv") or 0
                    new_token = {
                        "address": item.get("address"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "mcap": float(mcap_val),
                        "v24h": float(item.get("v24h") or 0),
                        "liquidity": float(item.get("liquidity") or 0),
                        "last_alert_ts": 0
                    }
                    discovered.append(new_token)
                    total_processed += 1
                    # Less spammy log
                    if total_processed % 50 == 0:
                        print(f"   [+] Processed {total_processed} tokens...", flush=True)
                
                # Small batch save
                if len(discovered) >= 200:
                    supabase.table("tokens").upsert(discovered, on_conflict="address").execute()
                    discovered = [] 
            
            time.sleep(0.6) 
        except Exception as e:
            print(f"❌ ERROR at offset {offset}: {e}")
            break

    if discovered:
        supabase.table("tokens").upsert(discovered, on_conflict="address").execute()

    print(f"✅ Sync Complete. Total Tokens in DB: {total_processed}", flush=True)

# ... (rest of check_for_drops and send_alert remains the same)
