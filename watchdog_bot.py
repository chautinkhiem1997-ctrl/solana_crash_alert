import os, requests, time
from datetime import datetime
from supabase import create_client, Client
import telebot

print("🛡️ WATCHDOG BOT STARTED", flush=True)

# --- CONFIG (Pulled from GitHub Secrets) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- SETTINGS ---
DROP_THRESHOLD = 0.30       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 10, 15, 30, 60, 120, 180]

def run_watchdog():
    print(f"[{datetime.now()}] 📈 Pulling tokens from Supabase...", flush=True)
    res = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute()
    tokens = res.data
    if not tokens: return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    current_prices = {}
    headers = {"x-api-key": JUPITER_API_KEY}

    # 1. FETCH CURRENT PRICES (Jupiter V3)
    for i in range(0, len(addrs), 50): 
        batch = addrs[i:i+50]
        try:
            r = requests.get(f"https://api.jup.ag/price/v3?ids={','.join(batch)}", headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json().get("data", r.json())
                for addr, info in data.items():
                    p = info.get("usdPrice") or info.get("price")
                    if p: current_prices[addr] = float(p)
        except: pass
        time.sleep(0.2)

    # 2. SAVE NEW PRICES
    price_logs = [{"address": t['address'], "ts": now, "price": current_prices[t['address']]} 
                  for t in tokens if t['address'] in current_prices]
    if price_logs:
        supabase.table("prices").insert(price_logs).execute()

    # 3. CRASH DETECTION (Bulk Optimized)
    print("🔍 Fetching bulk history and calculating drops...", flush=True)
    max_lookback = now - 11100 

    # 🔥 FETCH ALL HISTORY AT ONCE (Prevents 1500 individual requests)
    try:
        all_history = supabase.table("prices").select("address, ts, price").gte("ts", max_lookback).execute().data
        
        # Organize data into a dictionary for instant lookup
        history_map = {}
        for h in all_history:
            addr = h['address']
            if addr not in history_map: history_map[addr] = []
            history_map[addr].append(h)
    except:
        print("❌ Failed to fetch bulk history.")
        return

    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue
        if (now - (t.get('last_alert_ts') or 0)) < COOLDOWN_SECONDS: continue 

        history = history_map.get(addr, [])
        if not history: continue

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            # Find price within a 5-minute window of the target timeframe
            valid_prices = [h for h in history if (cutoff - 300) <= h['ts'] <= (cutoff + 300)]
            
            if valid_prices:
                old_p = min(valid_prices, key=lambda x: abs(x['ts'] - cutoff))['price']
                if old_p <= 0: continue 
                
                drop = (old_p - curr_p) / old_p
                if drop >= DROP_THRESHOLD:
                    mcap_str = f"${t['mcap']/1e6:.2f}M" if t.get('mcap', 0) > 0 else "N/A"
                    msg = (f"🚨 **VERIFIED TOKEN CRASH**\n\n"
                           f"**Token:** {t['name']} ({t['symbol']})\n"
                           f"**Price:** ${curr_p:.6f}\n"
                           f"**Drop:** -{drop*100:.1f}% in {minutes} mins\n"
                           f"**MCAP:** {mcap_str}\n"
                           f"**CA:** `{addr}`\n\n"
                           f"🔗 [Dexscreener](https://dexscreener.com/solana/{addr})")
                    
                    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown')
                    except: pass
                    
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    break 

    # Cleanup: Keep database light
    try: supabase.table("prices").delete().lt("ts", now - 14400).execute()
    except: pass
    print("🏁 WATCHDOG CYCLE COMPLETE")

if __name__ == "__main__":
    run_watchdog()
