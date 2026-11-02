"""Refresh the last hourly bars for every sleeve and Schwartz coin.

load_coin() overlays LAB/fresh/{symbol}_fresh.json on the historical cache.
This module keeps that overlay current for all 25 majors.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from causal import LAB

DEST = LAB / "fresh"
COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    "LTCUSDT", "BCHUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT",
    "UNIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT",
    "TRXUSDT", "XLMUSDT", "ETCUSDT", "AAVEUSDT", "TIAUSDT",
]
HOUR = 3_600_000


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bybit-top25-scan"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bars(sym, start_ms, end_ms):
    out = {}
    cursor = int(end_ms)
    guard = 0
    while cursor > start_ms and guard < 8:
        guard += 1
        url = (
            "https://api.bybit.com/v5/market/kline?category=linear&symbol="
            + sym + "&interval=60&limit=1000&end=" + str(cursor)
        )
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        oldest = min(int(row[0]) for row in rows)
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts <= end_ms:
                out[str(ts)] = [float(row[4]), float(row[6])]
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.04)
    return out


def fetch_funding(sym, start_ms, end_ms):
    out = {}
    cursor = int(end_ms)
    guard = 0
    while cursor > start_ms and guard < 12:
        guard += 1
        url = (
            "https://api.bybit.com/v5/market/funding/history?category=linear&symbol="
            + sym + "&limit=200&endTime=" + str(cursor)
        )
        rows = (get(url).get("result") or {}).get("list") or []
        if not rows:
            break
        oldest = min(int(row["fundingRateTimestamp"]) for row in rows)
        for row in rows:
            ts = int(row["fundingRateTimestamp"])
            if start_ms <= ts <= end_ms:
                out[str(ts)] = float(row["fundingRate"])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.04)
    return out


def _load(path: Path) -> dict:
    if not path.exists():
        return {"bars": {}, "funding": {}}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"bars": {}, "funding": {}}
    blob.setdefault("bars", {})
    blob.setdefault("funding", {})
    return blob


def ensure_fresh(max_lag_ms: int = 2 * HOUR) -> None:
    """Download only the hours missing from the fresh overlay."""
    DEST.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    for sym in COINS:
        path = DEST / (sym + "_fresh.json")
        blob = _load(path)
        bars = blob["bars"]
        funding = blob["funding"]
        last_bar = max((int(key) for key in bars), default=0)
        last_fund = max((int(key) for key in funding), default=0)
        if last_bar and now - (last_bar + HOUR) <= max_lag_ms:
            continue
        bar_start = (last_bar + HOUR) if last_bar else now - 60 * 24 * HOUR
        fund_start = (last_fund + 1) if last_fund else bar_start
        added_bars = fetch_bars(sym, bar_start, now)
        added_funds = fetch_funding(sym, fund_start, now)
        bars.update(added_bars)
        funding.update(added_funds)
        path.write_text(json.dumps({"bars": bars, "funding": funding}), encoding="utf-8")
        print(sym, "fresh bars", len(added_bars), "funding", len(added_funds), flush=True)


if __name__ == "__main__":
    ensure_fresh()
    print("done", flush=True)
