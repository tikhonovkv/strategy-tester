"""One-time seed: fetch ~200 days of hourly bars + 8h funding for the 9 coins
into LAB/data/<sym>.json (same format as pairs_cache). Needed for live training."""
import json, time, urllib.request
from pathlib import Path

LAB = Path(__file__).resolve().parent
DATA = LAB / "data"
DATA.mkdir(exist_ok=True)
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bybit-futures"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_bars(sym, start, end):
    out = {}; cursor = start
    while cursor < end:
        url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
               f"&interval=60&limit=1000&start={cursor}&end={end}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows: break
        for r in rows:
            out[str(int(r[0]))] = [float(r[4]), float(r[6])]
        newest = max(int(r[0]) for r in rows)
        if newest <= cursor: break
        cursor = newest + 3600000; time.sleep(0.05)
    return out

def fetch_funding(sym, start, end):
    out = {}; cursor = start
    while cursor < end:
        url = (f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}"
               f"&limit=200&startTime={cursor}&endTime={end}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows: break
        for r in rows:
            out[str(int(r["fundingRateTimestamp"]))] = float(r["fundingRate"])
        newest = max(int(r["fundingRateTimestamp"]) for r in rows)
        if newest <= cursor: break
        cursor = newest + 1; time.sleep(0.05)
    return out

now = int(time.time() * 1000)
start = now - 200 * 86400000
for sym in COINS:
    bars = fetch_bars(sym, start, now)
    fund = fetch_funding(sym, start, now)
    (DATA / f"{sym}.json").write_text(json.dumps({"bars": bars, "funding": fund}), encoding="utf-8")
    print(f"{sym}: bars={len(bars)} funding={len(fund)}", flush=True)
print("bootstrap done")
