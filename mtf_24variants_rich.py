"""5-year (2021-2026) multi-timeframe dataset with 32 features and 24 trade variants per hour.

Expands the training data, feature space, and action space:
1. 5-year continuous BTCUSDT history (`2021-07` to `2026-10`, ~45,975 hourly bars
   and 15m/1m sub-hourly bars) so the model trains on 2021 bull, 2022 bear, 2023
   recovery, and 2024-2026 cycles.
2. 32 multi-timeframe features across 1h, 4h, 1d, and 7d/30d:
   - RSI(7) and RSI(14) on 1h, 4h, 1d + RSI momentum (change over 3 bars)
   - Bollinger z-score `(close - SMA) / std` on 1h(24), 4h(18), 1d(20), 1d(50)
   - Normalized ATR(14) on 1h, 4h, 1d
   - Volume surge ratios `vol / SMA(vol, 20)` on 1h, 4h, 1d
   - Range position `(close - min_low) / (max_high - min_low)` over 24h, 72h, 7d, 30d
   - Candle lower/upper wick ratios and SMA slopes (1h SMA72, 4h SMA18, 1d SMA20, 1d SMA50)
   - Multi-timeframe overbought/oversold and trend-hold flags.
3. 24 entry/exit variants per hour (12 longs `0..11`, 12 shorts `12..23`):
   - Fast 4h-SMA18 swing variants (hold 1-4 days, targets 3%/5%/none)
   - Medium 1d-SMA20 swing variants (hold 3-10 days, targets 6%/10%/none)
   - Core 1d-SMA50 trend-riding variants (structural 24h/48h/day stops).
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
from path_dataset import (
    FEE,
    HOUR_MS,
    MINUTE_MS,
    _concat,
    _iso,
    _load_bars,
    _unique_sorted,
    dataset_paths,
)
from scan import state_dir
from sma_ride import YEAR_MS, _compound, _month, _sma

warnings.filterwarnings("ignore", category=UserWarning)

DAY_MS = 86_400_000
SIX_MS = 183 * DAY_MS
STEP_15M_MS = 15 * 60_000
MAX_CAP_H = 45 * 24

VARIANT_NAMES = (
    # Fast 4h-SMA18 long swings (0..3)
    "long_lim_low_sl24_h4sma18",
    "long_lim_03_sl24_h4sma18_tp4",
    "long_lim_06_sl24_h4sma18_tp5",
    "long_lim_10_sl48_h4sma18_tp6",
    # Medium 1d-SMA20 long swings (4..7)
    "long_lim_low_sl24_day20",
    "long_lim_mid24_sl24_day20",
    "long_lim_05_sl24_day20_tp8",
    "long_lim_10_sl48_day20_tp10",
    # Core 1d-SMA50 long trend rides (8..11)
    "long_lim_low_sl24_day50",
    "long_lim_low_sl48_day50",
    "long_lim_05_sl24_day50",
    "long_lim_10_sl48_day50",
    # Fast 4h-SMA18 short swings (12..15)
    "short_lim_high_sl24_h4sma18",
    "short_lim_03_sl24_h4sma18_tp4",
    "short_lim_06_sl24_h4sma18_tp5",
    "short_lim_10_sl48_h4sma18_tp6",
    # Medium 1d-SMA20 short swings (16..19)
    "short_lim_high_sl24_day20",
    "short_lim_mid24_sl24_day20",
    "short_lim_05_sl24_day20_tp8",
    "short_lim_10_sl48_day20_tp10",
    # Core 1d-SMA50 short trend rides (20..23)
    "short_lim_high_sl24_day50",
    "short_lim_high_sl48_day50",
    "short_lim_05_sl24_day50",
    "short_lim_10_sl48_day50",
)


def _rsi(close: np.ndarray, length: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= length:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.clip(delta, 0.0, None)
    loss = np.clip(-delta, 0.0, None)
    avg_g = float(np.mean(gain[1 : length + 1]))
    avg_l = float(np.mean(loss[1 : length + 1]))
    for i in range(length, len(close)):
        if i > length:
            avg_g = (avg_g * (length - 1) + float(gain[i])) / length
            avg_l = (avg_l * (length - 1) + float(loss[i])) / length
        if avg_l == 0.0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    prev_c = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    return _sma(tr, length)


def _rolling_std(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    c1 = np.cumsum(values, dtype=np.float64)
    c2 = np.cumsum(values.astype(np.float64) ** 2)
    s1 = c1[length - 1 :] - np.concatenate([[0.0], c1[:-length]])
    s2 = c2[length - 1 :] - np.concatenate([[0.0], c2[:-length]])
    var = np.maximum(s2 / length - (s1 / length) ** 2, 1e-12)
    out[length - 1 :] = np.sqrt(var)
    return out


def _blocks_ohlcv(h_ts, h_o, h_h, h_l, h_c, h_v, hours: int, min_bars: int):
    key = h_ts // (hours * HOUR_MS)
    cuts = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], cuts])
    ends = np.concatenate([cuts, [len(h_ts)]])
    ts, op, high, low, close, vol = [], [], [], [], [], []
    for a, b in zip(starts, ends):
        if b - a < min_bars:
            continue
        ts.append(int(h_ts[b - 1]) + HOUR_MS)
        op.append(float(h_o[a]))
        high.append(float(np.max(h_h[a:b])))
        low.append(float(np.min(h_l[a:b])))
        close.append(float(h_c[b - 1]))
        vol.append(float(np.sum(h_v[a:b])))
    return (
        np.asarray(ts, dtype=np.int64),
        np.asarray(op, dtype=np.float64),
        np.asarray(high, dtype=np.float64),
        np.asarray(low, dtype=np.float64),
        np.asarray(close, dtype=np.float64),
        np.asarray(vol, dtype=np.float64),
    )


def _downsample_1m_to_15m(m1_bars: tuple[np.ndarray, ...]) -> tuple[np.ndarray, ...]:
    ts, o, h, l, c, v = m1_bars
    key = ts // STEP_15M_MS
    cuts = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], cuts])
    ends = np.concatenate([cuts, [len(ts)]])
    b_ts = ts[starts]
    b_o = o[starts]
    b_h = np.maximum.reduceat(h, starts)
    b_l = np.minimum.reduceat(l, starts)
    b_c = c[ends - 1]
    b_v = np.add.reduceat(v, starts)
    return b_ts, b_o, b_h, b_l, b_c, b_v


def load_merged_5y_btc(root: Path) -> tuple[tuple[np.ndarray, ...], tuple[np.ndarray, ...]]:
    h1_early = _load_bars(root / "btc_early_2021_2024_path_h1.npz")
    h1_late = _load_bars(root / "btc_path_h1.npz")
    h1 = _unique_sorted(_concat([h1_early, h1_late]))

    m15_early = _load_bars(root / "btc_early_2021_2024_path_15m.npz")
    m1_late = _load_bars(root / "btc_path_1m.npz")
    m15_late = _downsample_1m_to_15m(m1_late)
    sub15 = _unique_sorted(_concat([m15_early, m15_late]))
    return h1, sub15


class Rich24VariantDataset:
    """Builds or loads the 5-year 32-feature x 24-variant hourly dataset."""

    def build_or_load(self, prefix: str, h1_bars: tuple, sub_bars: tuple, step_ms: int, root: Path):
        cache = root / f"{prefix}_mtf_24variants_rich.npz"
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

        day_ts, day_o, day_h, day_l, day_c, day_v = _blocks_ohlcv(h_ts, h_o, h_h, h_l, h_c, h_v, 24, 20)
        h4_ts, _h4o, h4_h, h4_l, h4_c, h4_v = _blocks_ohlcv(h_ts, h_o, h_h, h_l, h_c, h_v, 4, 4)

        # Hourly indicators
        h_rsi14 = _rsi(h_c, 14)
        h_rsi7 = _rsi(h_c, 7)
        h_sma24 = _sma(h_c, 24)
        h_sma72 = _sma(h_c, 72)
        h_std24 = _rolling_std(h_c, 24)
        h_atr14 = _atr(h_h, h_l, h_c, 14)
        h_vol_sma = _sma(h_v, 24)

        # 4h indicators
        h4_rsi14 = _rsi(h4_c, 14)
        h4_rsi7 = _rsi(h4_c, 7)
        h4_sma18 = _sma(h4_c, 18)
        h4_std18 = _rolling_std(h4_c, 18)
        h4_atr14 = _atr(h4_h, h4_l, h4_c, 14)
        h4_vol_sma = _sma(h4_v, 18)

        # Daily indicators
        d_rsi14 = _rsi(day_c, 14)
        d_rsi7 = _rsi(day_c, 7)
        d_sma20 = _sma(day_c, 20)
        d_sma50 = _sma(day_c, 50)
        d_std20 = _rolling_std(day_c, 20)
        d_atr14 = _atr(day_h, day_l, day_c, 14)
        d_vol_sma = _sma(day_v, 20)

        j0_all = np.searchsorted(m_ts, close_ts, side="left")
        j12_all = np.searchsorted(m_ts, close_ts + 12 * HOUR_MS, side="left")
        j24_all = np.searchsorted(m_ts, close_ts + 24 * HOUR_MS, side="left")
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
            j_max = min(int(jmax_all[i]), len(m_ts))
            if j0 >= len(m_ts) or j24 <= j0 or j_max <= j24:
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

            # 32 multi-timeframe features
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
            x[i, 10] = (min(bar_o, entry_c) - bar_l) / hr_span  # lower wick ratio
            x[i, 11] = (bar_h - max(bar_o, entry_c)) / hr_span  # upper wick ratio
            x[i, 12] = entry_c / float(h_c[i - 6]) - 1.0
            x[i, 13] = entry_c / float(h_c[i - 24]) - 1.0
            x[i, 14] = (entry_c - low_24) / max(high_24 - low_24, 1e-8)
            x[i, 15] = (entry_c - low_72) / max(high_72 - low_72, 1e-8)

            # 4h features
            x[i, 16] = h4_rsi14[fi] / 100.0
            x[i, 17] = h4_rsi7[fi] / 100.0
            x[i, 18] = (h4_rsi14[fi] - h4_rsi14[fi - 2]) / 100.0
            x[i, 19] = (h4_c[fi] - h4_sma18[fi]) / h4_c[fi]
            x[i, 20] = (h4_sma18[fi] - h4_sma18[fi - 3]) / h4_c[fi]
            x[i, 21] = (h4_c[fi] - h4_sma18[fi]) / max(float(h4_std18[fi]), 1e-8)
            x[i, 22] = h4_atr14[fi] / h4_c[fi]

            # Daily & 7d/30d features
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
            sl_s24 = max(high_24, bar_h * 1.008)
            sl_s48 = max(high_48, bar_h * 1.015)
            mid_l24 = 0.5 * (bar_l + low_24)
            mid_s24 = 0.5 * (bar_h + high_24)
            t12 = int(close_ts[i + 12])
            t24 = int(close_ts[i + 24])

            long_specs = (
                # Fast 4h-SMA18 swings (0..3)
                (0, bar_l, sl_l24, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (1, entry_c * 0.997, min(low_24, entry_c * 0.985), 0.04, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (2, entry_c * 0.994, min(low_24, entry_c * 0.982), 0.05, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (3, entry_c * 0.99, min(low_48, entry_c * 0.978), 0.06, h4_ts, h4_c, h4_sma18, j_h4_end, j24, t24),
                # Medium 1d-SMA20 swings (4..7)
                (4, bar_l, sl_l24, None, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (5, mid_l24, sl_l24, None, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (6, entry_c * 0.995, min(low_24, entry_c * 0.982), 0.08, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (7, entry_c * 0.99, min(low_48, entry_c * 0.975), 0.10, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                # Core 1d-SMA50 trend rides (8..11)
                (8, bar_l, sl_l24, None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (9, bar_l, sl_l48, None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (10, entry_c * 0.995, min(low_24, entry_c * 0.982), None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (11, entry_c * 0.99, min(low_48, entry_c * 0.975), None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
            )
            for vi, lim_px, stop_px, tp_pct, tf_ts, tf_c, tf_sma, j_tf_end, j_w, t_w in long_specs:
                if lim_px >= entry_c or stop_px >= lim_px:
                    rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t_w, 0
                else:
                    r, e, f = _sim_limit_variant(
                        lim_px, stop_px, tp_pct, 1, j0, j_w, j_max, t_w, tf_ts, tf_c, tf_sma, j_tf_end, m_o, m_h, m_l, m_c, m_end_ts
                    )
                    rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

            short_specs = (
                # Fast 4h-SMA18 short swings (12..15)
                (12, bar_h, sl_s24, None, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (13, entry_c * 1.003, max(high_24, entry_c * 1.015), 0.04, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (14, entry_c * 1.006, max(high_24, entry_c * 1.018), 0.05, h4_ts, h4_c, h4_sma18, j_h4_end, j12, t12),
                (15, entry_c * 1.01, max(high_48, entry_c * 1.022), 0.06, h4_ts, h4_c, h4_sma18, j_h4_end, j24, t24),
                # Medium 1d-SMA20 short swings (16..19)
                (16, bar_h, sl_s24, None, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (17, mid_s24, sl_s24, None, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (18, entry_c * 1.005, max(high_24, entry_c * 1.018), 0.08, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                (19, entry_c * 1.01, max(high_48, entry_c * 1.025), 0.10, day_ts, day_c, d_sma20, j_day_end, j24, t24),
                # Core 1d-SMA50 short trend rides (20..23)
                (20, bar_h, sl_s24, None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (21, bar_h, sl_s48, None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (22, entry_c * 1.005, max(high_24, entry_c * 1.018), None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
                (23, entry_c * 1.01, max(high_48, entry_c * 1.025), None, day_ts, day_c, d_sma50, j_day_end, j24, t24),
            )
            for vi, lim_px, stop_px, tp_pct, tf_ts, tf_c, tf_sma, j_tf_end, j_w, t_w in short_specs:
                if lim_px <= entry_c or stop_px <= lim_px:
                    rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t_w, 0
                else:
                    r, e, f = _sim_limit_variant(
                        lim_px, stop_px, tp_pct, -1, j0, j_w, j_max, t_w, tf_ts, tf_c, tf_sma, j_tf_end, m_o, m_h, m_l, m_c, m_end_ts
                    )
                    rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

        np.savez(cache, close_ts=close_ts, x=x, rets=rets, exits=exits, filled=filled)
        print(f"built {cache.name} in {time.time() - t0:.1f}s", flush=True)
        return close_ts, x, rets, exits, filled


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    root = state_dir()
    h1, sub15 = load_merged_5y_btc(root)
    close_ts, x, rets, exits, filled = Rich24VariantDataset().build_or_load("btc_5y", h1, sub15, STEP_15M_MS, root)
    ready = np.flatnonzero(np.all(np.isfinite(x), axis=1) & np.all(np.isfinite(rets), axis=1))
    print(f"5y BTC rich 24-variant dataset ready: {len(ready)} / {len(close_ts)} hours", flush=True)
    bull_h = ready[(x[ready, 28] > 0.0) & (x[ready, 27] > 0.0)]
    bear_h = ready[(x[ready, 28] < 0.0) & (x[ready, 27] < 0.0)]
    for vi, name in enumerate(VARIANT_NAMES):
        reg = bull_h if vi < 12 else bear_h
        m = filled[reg, vi] == 1
        cnt = int(np.sum(m))
        mean_r = float(np.mean(rets[reg[m], vi])) * 100 if cnt else 0.0
        print(f"variant {vi:2d} {name:30s} regime_filled={cnt:5d} regime_mean_pct={mean_r:+.3f}%", flush=True)


if __name__ == "__main__":
    main()
