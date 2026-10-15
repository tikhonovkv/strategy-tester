"""Multi-echelon 24-variant x 32-feature multi-timeframe model trained on 5-year BTC + ETH pool.

Key upgrades for higher trade frequency and stronger generalization across both
early years (2022-2024), late years (2024-2026), and other coins (ETHUSDT):
1. Expanded training pool: 5-year BTCUSDT (`2021-07` to `2026-10`, ~44,500 hours)
   plus ETHUSDT (~26,800 hours) with 32 scale-invariant multi-timeframe features
   (1h, 4h, 1d, 7d, 30d RSI7/14, Bollinger z-scores, ATR14, volume surges, candle
   wick ratios, and SMA slopes).
2. Fixed short variant stops (variants 12..23): uses anti-squeeze stops
   `max(high_24, fill_px * 1.02..1.03)` and a 14-day cap on short holds (matching
   `dayride.npz`) instead of the 0.8% hair-trigger stop.
3. Three active variant echelons so a long-term trade does not freeze swing
   opportunities for 45 days:
   - Echelon A (Fast 4h-SMA18 swings, variants 0..3 & 12..15): 1-4 day holds
   - Echelon B (Medium 1d-SMA20 swings, variants 4..7): 3-10 day holds
   - Echelon C (Core 1d-SMA50 trend rides, variants 8..11 & 20..23).
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from mtf_10variants_uncapped_consensus import _sim_limit_variant
from mtf_24variants_rich import (
    DAY_MS,
    MAX_CAP_H,
    SIX_MS,
    STEP_15M_MS,
    VARIANT_NAMES,
    _atr,
    _blocks_ohlcv,
    _rolling_std,
    _rsi,
    load_merged_5y_btc,
)
from path_dataset import FEE, HOUR_MS, _load_bars
from scan import state_dir
from sma_ride import YEAR_MS, _compound, _month, _sma

warnings.filterwarnings("ignore", category=UserWarning)


def build_or_load_rich24_v2(prefix: str, h1_bars: tuple, sub_bars: tuple, step_ms: int, root: Path):
    cache = root / f"{prefix}_mtf_24variants_rich_v2.npz"
    if cache.exists():
        with np.load(cache) as data:
            return (
                data["close_ts"],
                data["x"],
                data["rets"],
                data["exits"],
                data["filled"],
            )
    t0 = time.time()
    h_ts, h_o, h_h, h_l, h_c, h_v = h1_bars
    m_ts, m_o, m_h, m_l, m_c, _mv = sub_bars
    h_ts = h_ts.astype(np.int64)
    m_ts = m_ts.astype(np.int64)
    m_end_ts = m_ts + step_ms
    close_ts = h_ts + HOUR_MS

    day_ts, _day_o, day_h, day_l, day_c, day_v = _blocks_ohlcv(h_ts, h_o, h_h, h_l, h_c, h_v, 24, 20)
    h4_ts, _h4o, h4_h, h4_l, h4_c, h4_v = _blocks_ohlcv(h_ts, h_o, h_h, h_l, h_c, h_v, 4, 4)

    h_rsi14 = _rsi(h_c, 14)
    h_rsi7 = _rsi(h_c, 7)
    h_sma24 = _sma(h_c, 24)
    h_sma72 = _sma(h_c, 72)
    h_std24 = _rolling_std(h_c, 24)
    h_atr14 = _atr(h_h, h_l, h_c, 14)
    h_vol_sma = _sma(h_v, 24)

    h4_rsi14 = _rsi(h4_c, 14)
    h4_rsi7 = _rsi(h4_c, 7)
    h4_sma18 = _sma(h4_c, 18)
    h4_std18 = _rolling_std(h4_c, 18)
    h4_atr14 = _atr(h4_h, h4_l, h4_c, 14)

    d_rsi14 = _rsi(day_c, 14)
    d_rsi7 = _rsi(day_c, 7)
    d_sma20 = _sma(day_c, 20)
    d_sma50 = _sma(day_c, 50)
    d_std20 = _rolling_std(day_c, 20)
    d_atr14 = _atr(day_h, day_l, day_c, 14)

    j0_all = np.searchsorted(m_ts, close_ts, side="left")
    j12_all = np.searchsorted(m_ts, close_ts + 12 * HOUR_MS, side="left")
    j24_all = np.searchsorted(m_ts, close_ts + 24 * HOUR_MS, side="left")
    j14d_all = np.searchsorted(m_ts, close_ts + 14 * 24 * HOUR_MS, side="left")
    jmax_all = np.searchsorted(m_ts, close_ts + MAX_CAP_H * HOUR_MS, side="left")
    j_day_end = np.searchsorted(m_ts, day_ts, side="left")
    j_h4_end = np.searchsorted(m_ts, h4_ts, side="left")

    n = len(h_c)
    x = np.full((n, 32), np.nan, dtype=np.float32)
    rets = np.full((n, 24), np.nan, dtype=np.float32)
    exits = np.zeros((n, 24), dtype=np.int64)
    filled = np.zeros((n, 24), dtype=np.uint8)

    for i in range(168, n - 48):
        j0 = int(j0_all[i])
        j12 = int(j12_all[i])
        j24 = int(j24_all[i])
        j14d = min(int(j14d_all[i]), len(m_ts))
        j_max = min(int(jmax_all[i]), len(m_ts))
        if j0 >= len(m_ts) or j24 <= j0 or j14d <= j24:
            continue
        t = int(close_ts[i])
        di = int(np.searchsorted(day_ts, t, side="right")) - 1
        fi = int(np.searchsorted(h4_ts, t, side="right")) - 1
        if di < 55 or fi < 32:
            continue
        if not (
            np.isfinite(h_rsi14[i])
            and np.isfinite(h_rsi7[i])
            and np.isfinite(h_sma24[i])
            and np.isfinite(h_sma72[i])
            and np.isfinite(h_std24[i])
            and np.isfinite(h_atr14[i])
            and np.isfinite(h4_rsi14[fi])
            and np.isfinite(h4_rsi7[fi])
            and np.isfinite(h4_sma18[fi])
            and np.isfinite(h4_std18[fi])
            and np.isfinite(h4_atr14[fi])
            and np.isfinite(d_rsi14[di])
            and np.isfinite(d_rsi7[di])
            and np.isfinite(d_sma20[di])
            and np.isfinite(d_sma50[di])
            and np.isfinite(d_std20[di])
            and np.isfinite(d_atr14[di])
        ):
            continue

        entry_c = float(h_c[i])
        bar_l = float(h_l[i])
        bar_h = float(h_h[i])
        bar_o = float(h_o[i])
        low_24 = float(np.min(h_l[i - 23 : i + 1]))
        high_24 = float(np.max(h_h[i - 23 : i + 1]))
        low_48 = float(np.min(h_l[i - 47 : i + 1]))
        high_48 = float(np.max(h_h[i - 47 : i + 1]))
        low_72 = float(np.min(h_l[i - 71 : i + 1]))
        high_72 = float(np.max(h_h[i - 71 : i + 1]))
        low_7d = float(np.min(h_l[i - 167 : i + 1]))
        high_7d = float(np.max(h_h[i - 167 : i + 1]))
        low_30d = float(np.min(day_l[di - 29 : di + 1]))
        high_30d = float(np.max(day_h[di - 29 : di + 1]))

        two_bull = (
            day_c[di] > d_sma50[di]
            and day_l[di] > d_sma50[di]
            and day_c[di - 1] > d_sma50[di - 1]
            and day_l[di - 1] > d_sma50[di - 1]
        )
        two_bear = (
            day_c[di] < d_sma50[di]
            and day_c[di - 1] < d_sma50[di - 1]
        )

        x[i, 0] = h_rsi14[i] / 100.0
        x[i, 1] = h_rsi7[i] / 100.0
        x[i, 2] = (h_rsi14[i] - h_rsi14[i - 3]) / 100.0
        x[i, 3] = (entry_c - h_sma24[i]) / entry_c
        x[i, 4] = (entry_c - h_sma72[i]) / entry_c
        x[i, 5] = (h_sma24[i] - h_sma72[i]) / entry_c
        x[i, 6] = (h_sma72[i] - h_sma72[i - 5]) / entry_c
        x[i, 7] = (entry_c - h_sma24[i]) / max(float(h_std24[i]), 1e-8)
        x[i, 8] = h_atr14[i] / entry_c
        x[i, 9] = float(h_v[i]) / max(float(h_vol_sma[i]), 1e-8) if np.isfinite(h_vol_sma[i]) else 1.0
        hr_span = max(bar_h - bar_l, 1e-8)
        x[i, 10] = (min(bar_o, entry_c) - bar_l) / hr_span
        x[i, 11] = (bar_h - max(bar_o, entry_c)) / hr_span
        x[i, 12] = entry_c / float(h_c[i - 6]) - 1.0
        x[i, 13] = entry_c / float(h_c[i - 24]) - 1.0
        x[i, 14] = (entry_c - low_24) / max(high_24 - low_24, 1e-8)
        x[i, 15] = (entry_c - low_72) / max(high_72 - low_72, 1e-8)

        x[i, 16] = h4_rsi14[fi] / 100.0
        x[i, 17] = h4_rsi7[fi] / 100.0
        x[i, 18] = (h4_rsi14[fi] - h4_rsi14[fi - 2]) / 100.0
        x[i, 19] = (h4_c[fi] - h4_sma18[fi]) / h4_c[fi]
        x[i, 20] = (h4_sma18[fi] - h4_sma18[fi - 3]) / h4_c[fi]
        x[i, 21] = (h4_c[fi] - h4_sma18[fi]) / max(float(h4_std18[fi]), 1e-8)
        x[i, 22] = h4_atr14[fi] / h4_c[fi]

        x[i, 23] = d_rsi14[di] / 100.0
        x[i, 24] = d_rsi7[di] / 100.0
        x[i, 25] = (d_rsi14[di] - d_rsi14[di - 3]) / 100.0
        x[i, 26] = (day_c[di] - d_sma20[di]) / day_c[di]
        x[i, 27] = (day_c[di] - d_sma50[di]) / day_c[di]
        x[i, 28] = (d_sma50[di] - d_sma50[di - 5]) / day_c[di]
        x[i, 29] = (entry_c - low_7d) / max(high_7d - low_7d, 1e-8)
        x[i, 30] = (entry_c - low_30d) / max(high_30d - low_30d, 1e-8)
        x[i, 31] = 1.0 if two_bull else (-1.0 if two_bear else 0.0)

        sl_l24 = min(low_24, bar_l * 0.992)
        sl_l48 = min(low_48, bar_l * 0.985)
        mid_l24 = 0.5 * (bar_l + low_24)
        t12 = int(close_ts[i + 12])
        t24 = int(close_ts[i + 24])

        long_specs = (
            # Fast 4h-SMA18 swings (0..3)
            (0, bar_l, sl_l24, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, j14d, t12),
            (1, bar_l, sl_l48, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, j14d, t12),
            (2, entry_c * 0.995, min(low_24, entry_c * 0.98), None, h4_ts, h4_c, h4_sma18, j_h4_end, j24, j14d, t24),
            (3, entry_c * 0.99, min(low_48, entry_c * 0.975), None, h4_ts, h4_c, h4_sma18, j_h4_end, j24, j14d, t24),
            # Medium 1d-SMA20 swings (4..7)
            (4, bar_l, sl_l24, None, day_ts, day_c, d_sma20, j_day_end, j24, j_max, t24),
            (5, mid_l24, sl_l24, None, day_ts, day_c, d_sma20, j_day_end, j24, j_max, t24),
            (6, entry_c * 0.995, min(low_24, entry_c * 0.982), 0.08, day_ts, day_c, d_sma20, j_day_end, j24, j_max, t24),
            (7, entry_c * 0.99, min(low_48, entry_c * 0.975), 0.10, day_ts, day_c, d_sma20, j_day_end, j24, j_max, t24),
            # Core 1d-SMA50 trend rides (8..11)
            (8, bar_l, sl_l24, None, day_ts, day_c, d_sma50, j_day_end, j24, j_max, t24),
            (9, bar_l, sl_l48, None, day_ts, day_c, d_sma50, j_day_end, j24, j_max, t24),
            (10, entry_c * 0.995, min(low_24, entry_c * 0.982), None, day_ts, day_c, d_sma50, j_day_end, j24, j_max, t24),
            (11, entry_c * 0.99, min(low_48, entry_c * 0.975), None, day_ts, day_c, d_sma50, j_day_end, j24, j_max, t24),
        )
        for vi, lim_px, stop_px, tp_pct, tf_ts, tf_c, tf_sma, j_tf_end, j_w, j_cap, t_w in long_specs:
            if lim_px >= entry_c or stop_px >= lim_px:
                rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t_w, 0
            else:
                r, e, f = _sim_limit_variant(
                    lim_px, stop_px, tp_pct, 1, j0, j_w, j_cap, t_w, tf_ts, tf_c, tf_sma, j_tf_end, m_o, m_h, m_l, m_c, m_end_ts
                )
                rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

        # Anti-squeeze short stops (2.0%..3.0% above limit price or 24h/48h high) and 14d max hold
        sl_s20 = max(high_24, bar_h * 1.02)
        sl_s25 = max(high_48, bar_h * 1.025)
        short_specs = (
            # Fast 4h-SMA18 short swings (12..15)
            (12, bar_h, sl_s20, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, j14d, t12),
            (13, bar_h, sl_s25, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, j14d, t12),
            (14, entry_c * 1.01, max(high_24, entry_c * 1.035), None, h4_ts, h4_c, h4_sma18, j_h4_end, j24, j14d, t24),
            (15, entry_c * 1.02, max(high_48, entry_c * 1.05), None, h4_ts, h4_c, h4_sma18, j_h4_end, j24, j14d, t24),
            # Medium 1d-SMA20 short swings (16..19)
            (16, bar_h, sl_s20, None, day_ts, day_c, d_sma20, j_day_end, j24, j14d, t24),
            (17, entry_c * 1.01, max(high_24, entry_c * 1.035), None, day_ts, day_c, d_sma20, j_day_end, j24, j14d, t24),
            (18, entry_c * 1.015, max(high_48, entry_c * 1.04), 0.08, day_ts, day_c, d_sma20, j_day_end, j24, j14d, t24),
            (19, entry_c * 1.02, max(high_48, entry_c * 1.05), 0.10, day_ts, day_c, d_sma20, j_day_end, j24, j14d, t24),
            # Core 1d-SMA50 short trend rides (20..23, matching dayride.npz 14d hold)
            (20, bar_h, sl_s20, None, day_ts, day_c, d_sma50, j_day_end, j24, j14d, t24),
            (21, bar_h, sl_s25, None, day_ts, day_c, d_sma50, j_day_end, j24, j14d, t24),
            (22, entry_c * 1.01, max(high_24, entry_c * 1.03525), None, day_ts, day_c, d_sma50, j_day_end, j24, j14d, t24),
            (23, entry_c * 1.02, max(high_24, entry_c * 1.0506), None, day_ts, day_c, d_sma50, j_day_end, j24, j14d, t24),
        )
        for vi, lim_px, stop_px, tp_pct, tf_ts, tf_c, tf_sma, j_tf_end, j_w, j_cap, t_w in short_specs:
            if lim_px <= entry_c or stop_px <= lim_px:
                rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t_w, 0
            else:
                r, e, f = _sim_limit_variant(
                    lim_px, stop_px, tp_pct, -1, j0, j_w, j_cap, t_w, tf_ts, tf_c, tf_sma, j_tf_end, m_o, m_h, m_l, m_c, m_end_ts
                )
                rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

    np.savez(cache, close_ts=close_ts, x=x, rets=rets, exits=exits, filled=filled)
    print(f"built {cache.name} in {time.time() - t0:.1f}s", flush=True)
    return close_ts, x, rets, exits, filled


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    root = state_dir()
    h1_btc, sub_btc = load_merged_5y_btc(root)
    close_ts, x, rets, exits, filled = build_or_load_rich24_v2("btc_5y", h1_btc, sub_btc, STEP_15M_MS, root)

    h1_eth = _load_bars(root / "eth_path_h1.npz")
    sub_eth = _load_bars(root / "eth_path_15m.npz")
    close_e, x_e, rets_e, exits_e, filled_e = build_or_load_rich24_v2("eth_3y", h1_eth, sub_eth, STEP_15M_MS, root)

    ready = np.flatnonzero(np.all(np.isfinite(x), axis=1) & np.all(np.isfinite(rets), axis=1))
    ready_e = np.flatnonzero(np.all(np.isfinite(x_e), axis=1) & np.all(np.isfinite(rets_e), axis=1))
    print(f"Ready hours -> BTC 5y: {len(ready)}, ETH 3y: {len(ready_e)}, Combined pool: {len(ready) + len(ready_e)}", flush=True)

    bull_h = ready[(x[ready, 28] > 0.0) & (x[ready, 27] > 0.0)]
    bear_h = ready[(x[ready, 28] < 0.0) & (x[ready, 27] < 0.0)]
    for vi, name in enumerate(VARIANT_NAMES):
        reg = bull_h if vi < 12 else bear_h
        m = filled[reg, vi] == 1
        cnt = int(np.sum(m))
        mean_r = float(np.mean(rets[reg[m], vi])) * 100 if cnt else 0.0
        print(f"BTC5Y variant {vi:2d} {name:30s} regime_filled={cnt:5d} regime_mean_pct={mean_r:+.3f}%", flush=True)


if __name__ == "__main__":
    main()
