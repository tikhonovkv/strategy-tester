"""Fetch fresh bars + funding to lab/fresh (workspace-writable), last ~50 days."""
import json, time, urllib.request
from pathlib import Path

STATE = Path.home() / ".cursor" / "bybit-top25-scan-state"
OUT = Path(r"C:\Users\123\Documents\deepseek-harness\bybit\lab") / "fresh"
OUT.mkdir(parents=True, exist_ok=True)
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bybit-top25-scan"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_bars(sym, start_ms, end_ms):
    out = {}
    cursor = start_ms
    while cursor < end_ms:
        url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=60"
               f"&limit=1000&start={cursor}&end={end_ms}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            out[str(int(r[0]))] = [float(r[4]), float(r[6])]
        newest = max(int(r[0]) for r in rows)
        if newest <= cursor:
            break
        cursor = newest + 3600000
        time.sleep(0.05)
    return out

def fetch_funding(sym, start_ms, end_ms):
    out = {}
    cursor = start_ms
    while cursor < end_ms:
        url = (f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}"
               f"&limit=200&startTime={cursor}&endTime={end_ms}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            out[str(int(r["fundingRateTimestamp"]))] = float(r["fundingRate"])
        newest = max(int(r["fundingRateTimestamp"]) for r in rows)
        if newest <= cursor:
            break
        cursor = newest + 1
        time.sleep(0.05)
    return out

now = int(time.time() * 1000)
start = now - 50 * 86400000

for sym in COINS:
    old = STATE / "pairs_cache" / f"{sym}.json"
    if not old.exists():
        print(sym, "no cache"); continue
    blob = json.loads(old.read_text(encoding="utf-8"))
    last_bar = max(int(k) for k in blob.get("bars", {})) if blob.get("bars") else 0
    last_fund = max(int(k) for k in blob.get("funding", {})) if blob.get("funding") else 0
    b0 = max(start, last_bar - 86400000)
    f0 = max(start, last_fund - 86400000)
    nb = fetch_bars(sym, b0, now)
    nf = fetch_funding(sym, f0, now)
    fresh = {"bars": nb, "funding": nf}
    (OUT / f"{sym}_fresh.json").write_text(json.dumps(fresh), encoding="utf-8")
    print(f"{sym}: bars +{len(nb)} funding +{len(nf)} last_bar {time.strftime('%m-%d', time.gmtime(max(int(k) for k in nb)/1000)) if nb else '-'}", flush=True)
print("done")
