"""Futures dataset: funding + price features -> 24h net PnL, per coin.

Bars (hourly close+vol) and funding (8h) come from pairs_cache. One row per
funding timestamp per coin: features = 8 funding values + z-score + delta +
6 price returns; targets = 24h forward return and the funding paid over the
next three 8h periods. Net PnL of a long = ret - paid - FEE; short = -ret + paid - FEE.
"""
from __future__ import annotations

import json, sys, time
from pathlib import Path
import numpy as np

STATE = Path.home() / ".cursor" / "bybit-top25-scan-state"
LAB = Path(__file__).resolve().parent
HOUR = 3_600_000
DAY = 24 * HOUR
FEE = 0.0015

COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]


def load_coin(sym):
    bars, vols, fund = {}, {}, {}
    # 1) bootstrap cache (fetched in-container)  2) local state pairs_cache
    for base in (LAB / "data", STATE / "pairs_cache"):
        bp = base / f"{sym}.json"
        if bp.exists():
            blob = json.loads(bp.read_text(encoding="utf-8"))
            for k, v in (blob.get("bars") or {}).items():
                bars[int(k)] = float(v[0]); vols[int(k)] = float(v[1])
            fund.update({int(k): float(v) for k, v in (blob.get("funding") or {}).items()})
    # 3) fresh overlay so the backtest reaches today
    fp = LAB / "fresh" / f"{sym}_fresh.json"
    if fp.exists():
        fr = json.loads(fp.read_text(encoding="utf-8"))
        for k, v in fr.get("bars", {}).items():
            bars[int(k)] = float(v[0]); vols[int(k)] = float(v[1])
        fund.update({int(k): float(v) for k, v in fr.get("funding", {}).items()})
    return bars, vols, fund


def _load_oi(sym):
    p = LAB / f"{sym.lower().replace('usdt','')}_oi_4h.npz"
    if not p.exists():
        return None, None
    with np.load(p) as d:
        return d["ts"].astype(np.int64), d["oi"].astype(np.float64)


def build_rows(sym):
    bars, vols, fund = load_coin(sym)
    bar_ts = np.array(sorted(bars), dtype=np.int64)
    bar_px = np.array([bars[int(t)] for t in bar_ts])
    bar_vol = np.array([vols[int(t)] for t in bar_ts])
    f_ts = np.array(sorted(fund), dtype=np.int64)
    f_rate = np.array([fund[int(t)] for t in f_ts])
    oi_ts, oi = _load_oi(sym)
    def asof(t):
        k = int(np.searchsorted(bar_ts, t, side="right")) - 1
        return float(bar_px[k]) if k >= 0 else np.nan
    def asof_idx(t):
        return int(np.searchsorted(bar_ts, t, side="right")) - 1
    def _asof_oi(ts_a, v_a, t):
        if ts_a is None:
            return np.nan
        k = int(np.searchsorted(ts_a, t, side="right")) - 1
        return float(v_a[k]) if k >= 0 else np.nan
    rows = []
    for i in range(90, len(f_ts) - 3):
        t = int(f_ts[i])
        if int(f_ts[i+3]) - t > 30 * HOUR:
            continue
        px = asof(t); px1 = asof(t + DAY)
        if not np.isfinite(px) or not np.isfinite(px1) or px <= 0:
            continue
        hist = f_rate[i-89:i+1]
        past = [asof(t - k*8*HOUR) for k in (1, 3, 9, 21, 42, 90)]
        if not all(np.isfinite(p) and p > 0 for p in past):
            continue
        z = float(hist[-1] - hist.mean()) / (float(hist.std()) + 1e-8)
        oi_now = _asof_oi(oi_ts, oi, t)
        oi_1d = _asof_oi(oi_ts, oi, t - DAY)
        oi_7d = _asof_oi(oi_ts, oi, t - 7 * DAY)
        oi_f = [np.nan, np.nan]
        if oi_now > 0 and oi_1d > 0 and oi_7d > 0:
            oi_f = [oi_now / oi_1d - 1.0, oi_now / oi_7d - 1.0]
        # NEW: price/vol features (close-only) so the model can find non-funding edge
        bi = asof_idx(t)
        if bi < 30:
            continue
        closes = bar_px[bi-14:bi+1]
        diff = np.diff(closes)
        gain = float(np.maximum(diff, 0).mean()); loss = float(np.maximum(-diff, 0).mean())
        rsi = 100.0 - 100.0 / (1.0 + gain / (loss + 1e-12))
        logr = np.diff(np.log(bar_px[bi-24:bi+1]))
        vol24 = float(logr.std()) if len(logr) >= 20 else 0.0
        r1 = float(bar_px[bi] / bar_px[bi-1] - 1.0)
        r4 = float(bar_px[bi] / bar_px[bi-4] - 1.0)
        r12 = float(bar_px[bi] / bar_px[bi-12] - 1.0)
        feat = np.array([
            *f_rate[i-8:i+1],            # 8 funding values
            z,                            # funding z-score
            float(f_rate[i] - f_rate[i-1]),  # funding delta
            *[px/p - 1.0 for p in past],  # 6 price returns
            oi_f[0], oi_f[1],             # 2 open-interest changes
            rsi, vol24, r1, r4, r12,      # 5 price/vol features
        ], dtype=np.float64)
        ret = px1/px - 1.0
        paid = float(f_rate[i+1] + f_rate[i+2] + f_rate[i+3])
        rows.append((t, feat, ret, paid, z))
    return rows


def main():
    import sys as _sys
    no_oi = "--no-oi" in _sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    out = {}
    total = 0
    for sym in COINS:
        t0 = time.time()
        rows = build_rows(sym)
        if no_oi:
            rows = [(a, np.concatenate([b[:17], b[19:]]), c, d, e) for a, b, c, d, e in rows]  # drop OI at 17,18
        total += len(rows)
        out[sym] = [[int(t), [round(float(v), 8) for v in f], round(float(r), 6), round(float(p), 8), round(float(z), 6)]
                    for t, f, r, p, z in rows]
        print(f"{sym}: rows={len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"TOTAL rows={total}", flush=True)
    (LAB / "futures_dataset.json").write_text(json.dumps(out), encoding="utf-8")
    print("saved", LAB / "futures_dataset.json", flush=True)


if __name__ == "__main__":
    main()
