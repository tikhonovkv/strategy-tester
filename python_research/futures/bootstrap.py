"""Refresh hourly bars and 8h funding.

If a cache already exists, only the gap since the last bar is downloaded and
merged. A full 550-day pull happens when the coin has no cache yet.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

OUT = Path(os.environ.get("OUTPUT_DIR", str(Path(__file__).resolve().parent)))
LAB = Path(__file__).resolve().parent
DATA = OUT / "data"
SEED_DATA = LAB / "data"
STATE = Path.home() / ".cursor" / "bybit-top25-scan-state" / "pairs_cache"
DATA.mkdir(parents=True, exist_ok=True)
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT"]
HOUR = 3_600_000


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bybit-futures"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_bars(sym, start, end):
    out = {}
    end_cursor = end
    while end_cursor > start:
        url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
               f"&interval=60&limit=1000&start={start}&end={end_cursor}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            out[str(int(r[0]))] = [float(r[4]), float(r[6])]
        oldest = min(int(r[0]) for r in rows)
        if oldest <= start or len(rows) < 1000:
            break
        end_cursor = oldest - HOUR
        time.sleep(0.05)
    return out


def fetch_funding(sym, start, end):
    out = {}
    end_cursor = end
    while end_cursor > start:
        url = (f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}"
               f"&limit=200&startTime={start}&endTime={end_cursor}")
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            out[str(int(r["fundingRateTimestamp"]))] = float(r["fundingRate"])
        oldest = min(int(r["fundingRateTimestamp"]) for r in rows)
        if oldest <= start or len(rows) < 200:
            break
        end_cursor = oldest - 1
        time.sleep(0.05)
    return out


def load_existing(sym):
    for folder in (DATA, SEED_DATA, STATE):
        path = folder / f"{sym}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"bars": {}, "funding": {}}


def main():
    now = int(time.time() * 1000)
    horizon = now - 550 * 86400000
    for sym in COINS:
        blob = load_existing(sym)
        bars = blob.get("bars") or {}
        fund = blob.get("funding") or {}
        if bars:
            bar_start = max(horizon, max(int(k) for k in bars) - 2 * HOUR)
        else:
            bar_start = horizon
        if fund:
            fund_start = max(horizon, max(int(k) for k in fund) - 86400000)
        else:
            fund_start = horizon
        bars.update(fetch_bars(sym, bar_start, now))
        fund.update(fetch_funding(sym, fund_start, now))
        for folder in {DATA, SEED_DATA}:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f"{sym}.json.tmp"
            tmp.write_text(json.dumps({"bars": bars, "funding": fund}), encoding="utf-8")
            os.replace(tmp, folder / f"{sym}.json")
        last = max(int(k) for k in bars) if bars else 0
        print(f"{sym}: bars={len(bars)} funding={len(fund)} last={last}", flush=True)
    print("bootstrap done")


if __name__ == "__main__":
    main()
