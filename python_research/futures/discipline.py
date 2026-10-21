"""Schwartz exit: leave when price closes on the wrong side of the 10-day average.

The average uses only daily closes already known at that hour. The managed
outcome is what the model is trained to predict, so a trade that had to be
cut becomes a short loss in the lesson instead of a 24h argument with the market.
"""
from __future__ import annotations

import numpy as np

from causal import COINS, DAY, HOUR, completed_index, load_coin


def _series(sym: str):
    bars, _vols, fund = load_coin(sym)
    bar_ts = np.array(sorted(bars), dtype=np.int64)
    bar_px = np.array([bars[int(t)] for t in bar_ts], dtype=np.float64)
    known = bar_ts + HOUR
    mask = (known % DAY) == 0
    d_ts = known[mask]
    d_px = bar_px[mask]
    f_ts = np.array(sorted(fund), dtype=np.int64)
    f_rate = np.array([fund[int(t)] for t in f_ts], dtype=np.float64)
    return bar_ts, bar_px, d_ts, d_px, f_ts, f_rate


def _ma(d_ts, d_px, t: int):
    k = int(np.searchsorted(d_ts, t, side="right"))
    if k < 10:
        return None
    return float(d_px[k - 10:k].mean())


def _px(bar_ts, bar_px, t: int):
    j = completed_index(bar_ts, t)
    if j < 0 or int(bar_ts[j]) != t - HOUR:
        return None
    return float(bar_px[j])


def _paid(f_ts, f_rate, t: int, exit_t: int) -> float:
    lo = int(np.searchsorted(f_ts, t, side="right"))
    hi = int(np.searchsorted(f_ts, exit_t, side="right"))
    if hi <= lo:
        return 0.0
    return float(f_rate[lo:hi].sum())


def _walk(bar_ts, bar_px, d_ts, d_px, f_ts, f_rate, t: int, side: int):
    entry = _px(bar_ts, bar_px, t)
    if entry is None or entry <= 0:
        return None
    exit_t = t + DAY
    exit_px = entry
    lo = entry
    hi = entry
    for step in range(1, 25):
        ht = t + step * HOUR
        px = _px(bar_ts, bar_px, ht)
        if px is None:
            break
        lo = min(lo, px)
        hi = max(hi, px)
        ma = _ma(d_ts, d_px, ht)
        wrong = ma is not None and ((side == 1 and px < ma) or (side == -1 and px > ma))
        exit_t = ht
        exit_px = px
        if wrong or step == 24:
            break
    return {
        "ret": exit_px / entry - 1.0,
        "paid": _paid(f_ts, f_rate, t, exit_t),
        "min": lo / entry - 1.0,
        "max": hi / entry - 1.0,
        "hold": exit_t - t,
    }


def apply_schwartz(rows: list[dict]) -> dict:
    by = {}
    for row in rows:
        by.setdefault(row["ci"], []).append(row)
    early = 0
    total = 0
    for ci, group in by.items():
        bar_ts, bar_px, d_ts, d_px, f_ts, f_rate = _series(COINS[ci])
        for row in group:
            t = int(row["t"])
            long = _walk(bar_ts, bar_px, d_ts, d_px, f_ts, f_rate, t, 1)
            short = _walk(bar_ts, bar_px, d_ts, d_px, f_ts, f_rate, t, -1)
            if long is None or short is None:
                continue
            row["ret_l"] = long["ret"]
            row["paid_l"] = long["paid"]
            row["min_l"] = long["min"]
            row["max_l"] = long["max"]
            row["hold_l"] = long["hold"]
            row["ret_s"] = short["ret"]
            row["paid_s"] = short["paid"]
            row["min_s"] = short["min"]
            row["max_s"] = short["max"]
            row["hold_s"] = short["hold"]
            total += 2
            early += int(long["hold"] < DAY) + int(short["hold"] < DAY)
    return {"legs": total, "early_exit_pct": round(100.0 * early / total, 1) if total else 0.0}
