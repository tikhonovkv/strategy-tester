"""Marty Schwartz ('Pitbull') reactive trading engine — strictly causal (zero look-ahead).

Key principles encoded directly from Schwartz's methodology:
1. 10-day EMA Traffic Light:
   - Price > 10d EMA and 10d EMA rising -> ONLY LONG allowed.
   - Price < 10d EMA and 10d EMA falling -> ONLY SHORT allowed.
2. React, Don't Predict:
   - Enter on confirmed breakout / momentum in the direction of the 10d EMA,
     never fading the move.
3. Cut Losses Instantly (No arguing with the market):
   - Hourly check after entry:
     * Time-stop / instant bailout: if after 1-2 hours the trade fails to move
       in our favor or drops below -stop_atr * ATR_24h, exit immediately.
     * Hard hourly stop: if price crosses 10d EMA against us OR moves -stop_atr * ATR_24h.
     * Trailing stop on winners: once unrealized gain >= trail_arm * ATR_24h,
       trail stop at peak - trail_dist * ATR_24h.
4. Anti-Martingale / Loss-Streak Discipline:
   - After a losing trade on a coin, cut position size / require stronger confirmation;
     never average down or double up after losses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from causal import COINS, DAY, HOUR, FEE, load_coin

TRADE_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT"]
FOUR_SYMS = ["ETHUSDT", "SOLUSDT", "ADAUSDT", "SUIUSDT"]


def build_hourly_series(sym: str):
    """Build strictly causal hourly arrays indexed by close_time = open_time + HOUR."""
    bars, vols, fund = load_coin(sym)
    open_ts = np.array(sorted(bars), dtype=np.int64)
    close_ts = open_ts + HOUR  # time when bar close is actually known!
    px = np.array([bars[int(t)] for t in open_ts], dtype=np.float64)
    vol = np.array([vols[int(t)] for t in open_ts], dtype=np.float64)

    n = len(px)
    # 10-day EMA on hourly closes (240 hours)
    ema240 = np.zeros(n, dtype=np.float64)
    alpha240 = 2.0 / (240.0 + 1.0)
    ema240[0] = px[0]
    for i in range(1, n):
        ema240[i] = alpha240 * px[i] + (1.0 - alpha240) * ema240[i - 1]

    # Fast 1-day EMA (24 hours) and 3-day EMA (72 hours)
    ema24 = np.zeros(n, dtype=np.float64)
    ema72 = np.zeros(n, dtype=np.float64)
    a24 = 2.0 / 25.0
    a72 = 2.0 / 73.0
    ema24[0] = px[0]
    ema72[0] = px[0]
    for i in range(1, n):
        ema24[i] = a24 * px[i] + (1.0 - a24) * ema24[i - 1]
        ema72[i] = a72 * px[i] + (1.0 - a72) * ema72[i - 1]

    # Hourly absolute return as causal ATR proxy (24h rolling mean of |ret_1h|)
    ret1 = np.zeros(n, dtype=np.float64)
    ret1[1:] = np.diff(px) / np.maximum(px[:-1], 1e-12)
    abs_ret1 = np.abs(ret1)
    atr1h = np.zeros(n, dtype=np.float64)
    csum = np.cumsum(abs_ret1)
    for i in range(24, n):
        atr1h[i] = (csum[i] - csum[i - 24]) / 24.0

    # Funding aligned to close_ts
    f_ts = np.array(sorted(fund), dtype=np.int64)
    f_rate = np.array([fund[int(t)] for t in f_ts], dtype=np.float64)

    return {
        "sym": sym,
        "close_ts": close_ts,
        "px": px,
        "vol": vol,
        "ret1": ret1,
        "atr1h": atr1h,
        "ema24": ema24,
        "ema72": ema72,
        "ema240": ema240,
        "f_ts": f_ts,
        "f_rate": f_rate,
    }


def simulate_schwartz_trade(
    s: dict,
    idx: int,
    side: int,
    stop_atr: float = 1.8,
    bail_hours: int = 2,
    bail_atr: float = -0.3,
    trail_arm_atr: float = 2.5,
    trail_dist_atr: float = 1.2,
    max_hold_h: int = 24,
) -> dict | None:
    """Simulate one trade starting at close_ts[idx] with hourly reactive management."""
    px = s["px"]
    close_ts = s["close_ts"]
    ema240 = s["ema240"]
    f_ts = s["f_ts"]
    f_rate = s["f_rate"]

    n = len(px)
    if idx + max_hold_h >= n or idx < 260:
        return None
    entry_px = float(px[idx])
    entry_ts = int(close_ts[idx])
    atr = max(float(s["atr1h"][idx]), 0.002)

    peak_move = 0.0
    exit_idx = idx + max_hold_h
    exit_reason = "time"
    min_move = 0.0

    for h in range(1, max_hold_h + 1):
        j = idx + h
        if int(close_ts[j]) != entry_ts + h * HOUR:
            exit_idx = j - 1
            exit_reason = "gap"
            break
        cur_px = float(px[j])
        move = side * (cur_px / entry_px - 1.0)
        min_move = min(min_move, move)
        peak_move = max(peak_move, move)

        # 1. Hard stop: never let a loss exceed stop_atr * atr
        if move <= -stop_atr * atr:
            exit_idx = j
            exit_reason = "hard_stop"
            break

        # 2. 10-day EMA violation: price closed on the wrong side of 10d EMA
        if (side == 1 and cur_px < float(ema240[j])) or (side == -1 and cur_px > float(ema240[j])):
            exit_idx = j
            exit_reason = "ema10_break"
            break

        # 3. Schwartz fast bailout: if after `bail_hours` the trade is going against us, cut it
        if h == bail_hours and move <= bail_atr * atr:
            exit_idx = j
            exit_reason = "fast_bail"
            break

        # 4. Trailing stop once in solid profit
        if peak_move >= trail_arm_atr * atr and (peak_move - move) >= trail_dist_atr * atr:
            exit_idx = j
            exit_reason = "trail_profit"
            break

    exit_px = float(px[exit_idx])
    exit_ts = int(close_ts[exit_idx])
    # Funding settlements in (entry_ts, exit_ts]
    lo = int(np.searchsorted(f_ts, entry_ts, side="right"))
    hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0

    raw_move = side * (exit_px / entry_px - 1.0)
    funding_leg = -paid if side == 1 else paid
    net = raw_move + funding_leg - FEE
    return {
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "hold_h": int((exit_ts - entry_ts) // HOUR),
        "side": side,
        "net": float(net),
        "raw_move": float(raw_move),
        "min_move": float(min_move),
        "peak_move": float(peak_move),
        "atr": float(atr),
        "reason": exit_reason,
    }
