import os, requests, time
from datetime import datetime
from supabase import create_client, Client
from solana.rpc.api import Client as SolanaClient
from solders.pubkey import Pubkey
import telebot

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
solana = SolanaClient(RPC_URL)

# --- SETTINGS ---
DROP_THRESHOLD = 0.30       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 30, 60, 120]

# --- CLOUD CONFIG ---
# Make sure to add this near the top where your other keys are!
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")

def sync_tokens():
    print(f"[{datetime.now()}] 🔄 Fetching Jupiter Verified List (V2 API)...", flush=True)
    
    if not JUPITER_API_KEY:
        print("❌ ERROR: Missing JUPITER_API_KEY in GitHub Secrets!", flush=True)
        return

    # 1. Get the list of ALL verified mints
    tag_url = "https://api.jup.ag/tokens/v2/tag?query=verified"
    headers = {"x-api-key": JUPITER_API_KEY}
    
    try:
        r = requests.get(tag_url, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"❌ Jupiter API Blocked. Status: {r.status_code}", flush=True)
            return
            
        all_mints = r.json()
        print(f"✅ Found {len(all_mints)} verified mints. Fetching details for Top 1000...", flush=True)
        
        discovered = []
        top_mints = all_mints[:1000]
        
        # --- THE FIX: Check if Jupiter already gave us the full Token Data ---
        first_item = top_mints[0]
        if isinstance(first_item, dict) and ('name' in first_item or 'symbol' in first_item):
            print("⚡ Token data already included! Skipping search phase...", flush=True)
            for t in top_mints:
                discovered.append({
                    "address": t.get('id') or t.get('address'),
                    "name": t.get('name', 'Unknown'),
                    "symbol": t.get('symbol', 'Unknown'),
                    "mcap": 0,
                    "liquidity": 0,
                    "last_alert_ts": 0
                })
        else:
            # --- FALLBACK: If they only gave us dictionaries of IDs, extract the strings ---
            for i in range(0, len(top_mints), 100):
                batch = top_mints[i:i+100]
                
                # Safely pull out just the text string from the dictionary
                batch_ids = []
                for item in batch:
                    if isinstance(item, dict):
                        mint = item.get('id') or item.get('address')
                        if mint: batch_ids.append(str(mint))
                    else:
                        batch_ids.append(str(item))
                
                if not batch_ids: continue
                
                # Now the join() will work perfectly!
                search_url = f"https://api.jup.ag/tokens/v2/search?query={','.join(batch_ids)}"
                res = requests.get(search_url, headers=headers, timeout=15)
                
                if res.status_code == 200:
                    tokens_info = res.json()
                    for t in tokens_info:
                        discovered.append({
                            "address": t.get('id') or t.get('address'),
                            "name": t.get('name', 'Unknown'),
                            "symbol": t.get('symbol', 'Unknown'),
                            "mcap": 0,
                            "liquidity": 0,
                            "last_alert_ts": 0
                        })
                time.sleep(0.5) # Anti-rate limit
            
        if discovered:
            supabase.table("tokens").upsert(discovered, on_conflict="address").execute()
            print(f"✅ Sync Complete: {len(discovered)} verified tokens loaded into Supabase.", flush=True)
            
    except Exception as e:
        print(f"❌ Sync Error: {e}", flush=True)
def check_for_drops():
    print(f"\n[{datetime.now()}] 📈 Checking Prices via Jupiter...", flush=True)
    res = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute()
    tokens = res.data
    if not tokens: return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    current_prices = {}

    # 1. FETCH PRICES (Jupiter v2 API)
    for i in range(0, len(addrs), 100):
        batch = addrs[i:i+100]
        try:
            r = requests.get(f"https://api.jup.ag/price/v2?ids={','.join(batch)}", timeout=15)
            if r.status_code == 200:
                data = r.json().get("data", {})
                for addr, info in data.items():
                    if info: current_prices[addr] = float(info.get("price", 0))
        except: pass
        time.sleep(0.2)

    # 2. CALCULATE MCAP & CHECK DROPS
    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue

        # Update Market Cap if it's missing (Price * Supply)
        if t.get('mcap') == 0:
            try:
                # get_token_supply fetches real-time circulating supply
                supply_res = solana.get_token_supply(Pubkey.from_string(addr))
                supply = supply_res.value.ui_amount or 0
                t['mcap'] = curr_p * supply
                supabase.table("tokens").update({"mcap": t['mcap']}).eq("address", addr).execute()
            except: pass

        # Log price history for crash detection
        supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()

        # Crash logic (compared to history)
        last_alert = t.get('last_alert_ts') or 0
        if (now - last_alert) < COOLDOWN_SECONDS: continue

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            old_res = supabase.table("prices").select("price").eq("address", addr).gte("ts", cutoff - 400).lte("ts", cutoff + 400).order("ts", desc=False).limit(1).execute().data
            if old_res:
                old_p = old_res[0]['price']
                drop = (old_p - curr_p) / old_p
                if drop >= DROP_THRESHOLD:
                    send_alert(t, drop, curr_p)
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    break 

    # Cleanup history older than 3h
    supabase.table("prices").delete().lt("ts", now - 10800).execute()

def send_alert(t, drop, price):
    mcap_display = f"${t['mcap']/1e6:.2f}M" if t.get('mcap', 0) > 0 else "N/A"
    msg = (
        f"🚨 **VERIFIED TOKEN CRASH**\n\n"
        f"**Token:** {t['name']} ({t['symbol']})\n"
        f"**Price:** ${price:.6f}\n"
        f"**Drop:** -{drop*100:.1f}%\n"
        f"**MCAP:** {mcap_display}\n"
        f"**CA:** `{t['address']}`\n\n"
        f"🔗 [Dexscreener](https://dexscreener.com/solana/{t['address']})"
    )
    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown')
    except: pass

if __name__ == "__main__":
    sync_tokens()
    check_for_drops()
