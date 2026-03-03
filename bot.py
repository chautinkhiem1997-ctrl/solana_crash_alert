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

# --- PRODUCTION SETTINGS ---
TOTAL_MONITOR = 5000        
DROP_THRESHOLD = 0.40       # 40% Crash
MIN_MCAP = 1000000          # $1M Marketcap
MIN_LIQUIDITY = 20000       
COOLDOWN_SECONDS = 1800     # 30 Minutes Lockout
TIMEFRAMES = [5, 30, 60, 120]

def sync_tokens():
    """Fetches the top 5,000 tokens and saves them to the 'Brain'."""
    print(f"[{datetime.now()}] 🔄 Syncing 5,000 Tokens (MCAP > $1M)...", flush=True)
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
                    discovered.append({
                        "address": item.get("address"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "mcap": item.get("mc") or 0,
                        "last_alert_ts": 0
                    })
            time.sleep(0.5) # Respect API limits
        except: break

    if discovered:
        supabase.table("tokens").delete().neq("address", "0").execute()
        supabase.table("tokens").insert(discovered).execute()
        print(f"✅ Sync Complete: {len(discovered)} tokens saved.", flush=True)

def check_for_drops():
    """Main logic: Compares current prices against history."""
    print(f"[{datetime.now()}] 📈 Running Multi-Timeframe Check...", flush=True)
    tokens = supabase.table("tokens").select("*").execute().data
    if not tokens: return

    addrs = [t['address'] for t in tokens]
    now = int(time.time())
    
    current_prices = {}
    # Fetch current prices from DexScreener in batches of 30
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i+30]
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}", timeout=15)
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                for pair in pairs:
                    base_addr = pair.get("baseToken", {}).get("address")
                    current_prices[base_addr] = float(pair.get("priceUsd", 0))
        except: pass
        time.sleep(0.1)

    alerts_sent = 0
    for t in tokens:
        addr = t['address']
        curr_p = current_prices.get(addr)
        if not curr_p: continue

        # 1. Save new price to history
        supabase.table("prices").insert({"address": addr, "ts": now, "price": curr_p}).execute()

        # 2. Check 30-min Cooldown
        last_alert = t.get('last_alert_ts') or 0
        if (now - last_alert) < COOLDOWN_SECONDS:
            continue

        # 3. Check each Timeframe (5, 30, 60, 120)
        for minutes in TIMEFRAMES:
            cutoff = now - (minutes * 60)
            # Find a price snapshot within 5-min range of the cutoff
            old_res = supabase.table("prices").select("price").eq("address", addr).gte("ts", cutoff - 300).lte("ts", cutoff + 300).order("ts", desc=False).limit(1).execute().data
            
            if old_res:
                old_p = old_res[0]['price']
                if old_p <= 0: continue
                drop = (old_p - curr_p) / old_p
                
                if drop >= DROP_THRESHOLD:
                    send_alert(t, drop, curr_p)
                    # Update cooldown in Supabase
                    supabase.table("tokens").update({"last_alert_ts": now}).eq("address", addr).execute()
                    alerts_sent += 1
                    break 

    # Cleanup: Delete data older than 3 hours to stay in Supabase free tier
    supabase.table("prices").delete().lt("ts", now - 10800).execute()
    print(f"🏁 Cycle Finished. Alerts sent: {alerts_sent}", flush=True)

def send_alert(t, drop, price):
    """The exact Telegram format you requested."""
    msg = (
        f"🚨 **CRASH ALERT**\n\n"
        f"**Tokens:** {t['name']} ({t['symbol']})\n"
        f"**Marketcap:** ${t['mcap']/1e6:.2f}M\n"
        f"**Drop:** -{drop*100:.1f}%\n"
        f"**Price:** ${price:.8f}\n"
        f"**Contract address:** `{t['address']}`\n\n"
        f"🔗 [Dexscreener](https://dexscreener.com/solana/{t['address']})"
    )
    try:
        telebot.TeleBot(TELEGRAM_TOKEN).send_message(CHAT_ID, msg, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        print(f"Telegram Error: {e}")

if __name__ == "__main__":
    print("🚀 BOOTING UP...", flush=True)
    try:
        # Check if we have data; sync once a day or if database is empty
        res = supabase.table("tokens").select("count", count="exact").execute()
        if res.count < 100 or int(time.time()) % 86400 < 600:
            sync_tokens()
        
        check_for_drops()
    except Exception as e:
        print(f"🛑 Error: {e}", flush=True)
