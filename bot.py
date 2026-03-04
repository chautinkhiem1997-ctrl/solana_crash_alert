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
    print(f"[{datetime.now()}] 🔄 Starting Sync for {TOTAL_MONITOR} Tokens...", flush=True)
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
                    mcap_val = item.get("market_cap") or item.get("fdv") or 0
                    discovered.append({
                        "address": item.get("address"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "mcap": float(mcap_val),
                        "v24h": float(item.get("v24h") or 0),
                        "liquidity": float(item.get("liquidity") or 0),
                        "last_alert_ts": 0
                    })
                
                # BATCH SAVING: Save every 500 tokens so we don't lose progress
                if len(discovered) >= 500:
                    print(f"📦 Saving batch of {len(discovered)} to Supabase...", flush=True)
                    supabase.table("tokens").upsert(discovered, on_conflict="address").execute()
                    discovered = [] # Clear the list for the next batch
                    
                print(f"  > Progress: {offset + 50}/{TOTAL_MONITOR} fetched", flush=True)
            else:
                print(f"⚠️ Birdeye API Error {r.status_code}: {r.text}")
            
            time.sleep(0.5) # Anti-rate limit
        except Exception as e:
            print(f"❌ Sync Error at offset {offset}: {e}")
            break

    # Final save for any remaining tokens
    if discovered:
        print(f"📦 Saving final batch of {len(discovered)}...", flush=True)
        supabase.table("tokens").upsert(discovered, on_conflict="address").execute()

    print(f"✅ Sync Phase Complete.", flush=True)

def check_for_drops():
    print(f"\n[{datetime.now()}] 📈 Checking Prices for Drops...", flush=True)
    try:
        res = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute()
        tokens = res.data
    except Exception as e:
        print(f"❌ Supabase Fetch Error: {e}")
        return

    if not tokens:
        print("❓ No tokens found in Supabase. Did the Sync work?")
        return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    current_prices = {}

    print(f"🔍 Checking DexScreener for {len(addrs)} tokens...", flush=True)
    # Checking in batches of 30
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i+30]
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}", timeout=15)
            if r.status_code == 200:
                pairs = r.json().get("pairs")
                if pairs:
                    for pair in pairs:
                        current_prices[pair.get("baseToken", {}).get("address")] = float(pair.get("priceUsd", 0))
            
            if i % 300 == 0:
                print(f"  > Price Progress: {i}/{len(addrs)}")
        except Exception as e:
            print(f"⚠️ DexScreener Batch Error: {e}")
        
        time.sleep(0.2)

    alerts_sent = 0
    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue
        
        # Log price history
        try:
            supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()
        except: pass # Skip if price insert fails

        last_alert = t.get('last_alert_ts') or 0
        if (now - last_alert) < COOLDOWN_SECONDS: continue

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            old_res = supabase.table("prices").select("price").eq("address", addr).gte("ts", cutoff - 350).lte("ts", cutoff + 350).order("ts", desc=False).limit(1).execute().data
            if old_res:
                old_p = old_res[0]['price']
                if old_p > 0:
                    drop = (old_p - curr_p) / old_p
                    if drop >= DROP_THRESHOLD:
                        send_alert(t, drop, curr_p)
                        supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                        alerts_sent += 1
                        break 

    # Clean up history older than 3 hours
    supabase.table("prices").delete().lt("ts", now - 10800).execute()
    print(f"🏁 Cycle Finished. Alerts sent: {alerts_sent}")

def send_alert(t, drop, price):
    mcap_display = f"${t['mcap']/1e6:.2f}M" if t.get('mcap') else "Unknown"
    msg = (
        f"🚨 **CRASH ALERT**\n\n"
        f"**Token:** {t['name']} ({t['symbol']})\n"
        f"**MCAP:** {mcap_display}\n"
        f"**Drop:** -{drop*100:.1f}%\n"
        f"**Price:** ${price:.8f}\n"
        f"**CA:** `{t['address']}`\n\n"
        f"🔗 [Dexscreener](https://dexscreener.com/solana/{t['address']})"
    )
    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e: print(f"❌ Telegram Error: {e}")

if __name__ == "__main__":
    sync_tokens()
    check_for_drops()
