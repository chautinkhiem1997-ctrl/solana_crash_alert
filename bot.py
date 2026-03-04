import os, requests, time
from datetime import datetime
from supabase import create_client, Client
from solana.rpc.api import Client as SolanaClient
from solders.pubkey import Pubkey
import telebot

print("🚀 BOT SCRIPT STARTED", flush=True)

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
solana = SolanaClient(RPC_URL)

# --- SETTINGS ---
DROP_THRESHOLD = 0.30       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 10, 30, 60, 120, 180]

def sync_tokens():
    print(f"[{datetime.now()}] 🔄 Fetching Jupiter Verified List (V2 API)...", flush=True)
    
    if not JUPITER_API_KEY:
        print("❌ ERROR: Missing JUPITER_API_KEY in GitHub Secrets!", flush=True)
        return

    tag_url = "https://api.jup.ag/tokens/v2/tag?query=verified"
    headers = {"x-api-key": JUPITER_API_KEY}
    
    try:
        r = requests.get(tag_url, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"❌ Jupiter API Blocked. Status: {r.status_code}", flush=True)
            return
            
        all_tokens = r.json()
        print(f"✅ Found {len(all_tokens)} verified tokens. Saving Top 1500...", flush=True)
        
        discovered = []
        for t in all_tokens[:1500]:
            if isinstance(t, str):
                address = t
                name = "Unknown"
                symbol = "Unknown"
            else:
                address = t.get('id') or t.get('address')
                name = t.get('name', 'Unknown')
                symbol = t.get('symbol', 'Unknown')

            if not address:
                continue
                
            discovered.append({
                "address": address,
                "name": name,
                "symbol": symbol,
                "mcap": 0,
                "liquidity": 0,
                "last_alert_ts": 0
            })
            
        if discovered:
            supabase.table("tokens").upsert(discovered, on_conflict="address").execute()
            print(f"✅ Sync Complete: {len(discovered)} verified tokens loaded into Supabase.", flush=True)
            
    except Exception as e:
        print(f"❌ Sync Error: {e}", flush=True)

def check_for_drops():
    print(f"\n[{datetime.now()}] 📈 Checking Prices via Jupiter V3...", flush=True)
    res = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute()
    tokens = res.data
    
    if not tokens: 
        print("⚠️ No tokens found in database.", flush=True)
        return

    addrs = [t['address'] for t in tokens if t.get('address')]
    now = int(time.time())
    current_prices = {}

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "x-api-key": JUPITER_API_KEY
    }

   # 1. FETCH PRICES (Jupiter V3)
    for i in range(0, len(addrs), 50): 
        batch = addrs[i:i+50]
        try:
            r = requests.get(f"https://api.jup.ag/price/v3?ids={','.join(batch)}", headers=headers, timeout=15)
            
            if r.status_code == 200:
                resp_json = r.json()
                data = resp_json.get("data", resp_json)
                for addr, info in data.items():
                    price_val = info.get("usdPrice") or info.get("price")
                    if price_val:
                        current_prices[addr] = float(price_val)
            else:
                print(f"⚠️ Jupiter V3 Error: {r.status_code}", flush=True)
                
        except Exception as e:
            print(f"❌ Price Request Failed: {e}", flush=True)
        
        time.sleep(0.3)

    # 2. LOG PRICES & CALCULATE MCAP
    price_logs = []
    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue

        # Market Cap Calculation
        if t.get('mcap') == 0 or t.get('mcap') is None:
            try:
                supply_res = solana.get_token_supply(Pubkey.from_string(addr))
                if hasattr(supply_res, 'value') and supply_res.value:
                    supply = supply_res.value.ui_amount or 0
                    t['mcap'] = curr_p * supply
                    supabase.table("tokens").update({"mcap": t['mcap']}).eq("address", addr).execute()
                time.sleep(0.1) 
            except: pass

        # Prepare bulk insert for price table
        price_logs.append({"address": addr, "ts": now, "price": curr_p})

    # Save to prices table
    if price_logs:
        try:
            supabase.table("prices").insert(price_logs).execute()
            print(f"💾 Successfully saved {len(price_logs)} new entries to 'prices' table.", flush=True)
        except Exception as e:
            print(f"❌ Supabase Insert Error: {e}", flush=True)

    # 3. CRASH DETECTION (Compares current price to history)
    print("🔍 Calculating price drops...", flush=True)
    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue

        # Check if we are still in the 30-minute cooldown window
        last_alert = t.get('last_alert_ts') or 0
        if (now - last_alert) < COOLDOWN_SECONDS: 
            continue 

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            
            # Find a price from exactly 'minutes' ago (gives a 5-minute grace window)
            old_res = supabase.table("prices").select("price").eq("address", addr).gte("ts", cutoff - 300).lte("ts", cutoff + 300).order("ts", desc=False).limit(1).execute().data
            
            if old_res:
                old_p = old_res[0]['price']
                if old_p <= 0: continue # Prevent division by zero
                
                # Math: (Old - New) / Old
                drop = (old_p - curr_p) / old_p
                
                if drop >= DROP_THRESHOLD:
                    send_alert(t, drop, curr_p, minutes)
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    print(f"🚨 ALERT FIRED: {t['symbol']} dropped {drop*100:.1f}% in {minutes}m", flush=True)
                    break # Stop checking other timeframes to avoid spamming multiple alerts

    # Cleanup history older than 4 hours (14400 sec) so we don't delete the 180m data!
    supabase.table("prices").delete().lt("ts", now - 14400).execute()
    print("🏁 Price check cycle complete!", flush=True)

def send_alert(t, drop, price, minutes):
    mcap_display = f"${t['mcap']/1e6:.2f}M" if t.get('mcap', 0) > 0 else "N/A"
    msg = (
        f"🚨 **VERIFIED TOKEN CRASH**\n\n"
        f"**Token:** {t['name']} ({t['symbol']})\n"
        f"**Price:** ${price:.6f}\n"
        f"**Drop:** -{drop*100:.1f}% in {minutes} mins\n"
        f"**MCAP:** {mcap_display}\n"
        f"**CA:** `{t['address']}`\n\n"
        f"🔗 [Dexscreener](https://dexscreener.com/solana/{t['address']})"
    )
    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown')
    except: pass

# --- THE IGNITION SWITCH (Do not delete this!) ---
if __name__ == "__main__":
    print("⚙️ EXECUTING FUNCTIONS...", flush=True)
    sync_tokens()
    check_for_drops()
    print("🏁 SCRIPT FINISHED COMPLETELY", flush=True)
