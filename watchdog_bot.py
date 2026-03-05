import os, requests, time
from datetime import datetime
from supabase import create_client, Client
import telebot

print("🛡️ WATCHDOG BOT STARTED", flush=True)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

DROP_THRESHOLD = 0.30       
COOLDOWN_SECONDS = 1800     
TIMEFRAMES = [5, 10, 15, 30, 60, 120, 180]

def run_watchdog():
    print(f"[{datetime.now()}] 📈 Pulling tokens from Supabase...", flush=True)
    tokens = supabase.table("tokens").select("address, name, symbol, mcap, last_alert_ts").execute().data
    if not tokens: return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    current_prices = {}
    headers = {"x-api-key": JUPITER_API_KEY}

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
        time.sleep(0.3)

    price_logs = [{"address": t['address'], "ts": now, "price": current_prices[t['address']]} 
                  for t in tokens if t['address'] in current_prices]
    if price_logs: supabase.table("prices").insert(price_logs).execute()

    print("🔍 Calculating price drops...", flush=True)
    max_lookback = now - 11100 

    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue
        if (now - (t.get('last_alert_ts') or 0)) < COOLDOWN_SECONDS: continue 

        try: history = supabase.table("prices").select("ts, price").eq("address", addr).gte("ts", max_lookback).execute().data
        except: history = []
        if not history: continue

        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            valid_prices = [h for h in history if (cutoff - 300) <= h['ts'] <= (cutoff + 300)]
            
            if valid_prices:
                old_p = min(valid_prices, key=lambda x: abs(x['ts'] - cutoff))['price']
                if old_p <= 0: continue 
                
                drop = (old_p - curr_p) / old_p
                if drop >= DROP_THRESHOLD:
                    mcap_str = f"${t['mcap']/1e6:.2f}M" if t.get('mcap', 0) > 0 else "N/A"
                    msg = f"🚨 **VERIFIED TOKEN CRASH**\n\n**Token:** {t['name']} ({t['symbol']})\n**Price:** ${curr_p:.6f}\n**Drop:** -{drop*100:.1f}% in {minutes} mins\n**MCAP:** {mcap_str}\n**CA:** `{addr}`\n\n🔗 [Dexscreener](https://dexscreener.com/solana/{addr})"
                    try: telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown')
                    except: pass
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    break 

    try: supabase.table("prices").delete().lt("ts", now - 14400).execute()
    except: pass
    print("🏁 WATCHDOG CYCLE COMPLETE")

if __name__ == "__main__":
    run_watchdog()
