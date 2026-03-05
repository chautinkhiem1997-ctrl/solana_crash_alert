import os, requests, time
from datetime import datetime
from supabase import create_client, Client

print("🚜 SYNC BOT STARTED", flush=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def sync_all_data():
    print(f"[{datetime.now()}] 🔄 Fetching Jupiter Verified List...", flush=True)
    headers = {"x-api-key": JUPITER_API_KEY}
    
    try:
        r = requests.get("https://api.jup.ag/tokens/v2/tag?query=verified", headers=headers, timeout=15)
        all_tokens = r.json()
        discovered = []
        for t in all_tokens[:1500]:
            addr = t if isinstance(t, str) else (t.get('id') or t.get('address'))
            if addr:
                discovered.append({
                    "address": addr,
                    "name": t.get('name', 'Unknown') if isinstance(t, dict) else "Unknown",
                    "symbol": t.get('symbol', 'Unknown') if isinstance(t, dict) else "Unknown",
                })
        if discovered:
            supabase.table("tokens").upsert(discovered, on_conflict="address", ignore_duplicates=True).execute()
            print(f"✅ Synced {len(discovered)} tokens to Supabase.", flush=True)
    except Exception as e:
        print(f"❌ Jupiter Sync Error: {e}")

    print("📊 Updating Market Caps via DexScreener...", flush=True)
    tokens = supabase.table("tokens").select("address, symbol").execute().data
    
    for i in range(0, len(tokens), 30):
        batch = tokens[i:i+30]
        batch_addrs = [t['address'] for t in batch]
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch_addrs)}", timeout=15)
            if r.status_code == 200:
                pairs = r.json().get('pairs', [])
                mcap_map = {}
                for pair in pairs:
                    base_addr = pair.get('baseToken', {}).get('address')
                    mcap = pair.get('marketCap') or pair.get('fdv') or 0
                    if base_addr and mcap > mcap_map.get(base_addr, 0):
                        mcap_map[base_addr] = mcap
                for t in batch:
                    addr = t['address']
                    if addr in mcap_map and mcap_map[addr] > 0:
                        supabase.table("tokens").update({"mcap": mcap_map[addr]}).eq("address", addr).execute()
        except: pass 
        time.sleep(0.5)
        
    print("🏁 SYNC COMPLETELY FINISHED")

if __name__ == "__main__":
    sync_all_data()
