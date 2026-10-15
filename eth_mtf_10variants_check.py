"""Out-of-coin verification of `mtf_10variants_no_overbought_handoff` on ETHUSDT.

Downloads 3-year ETHUSDT spot 1h and 15m bars covering the exact same time span
as BTC (`2023-09-04` to `2026-10-03`), builds the three 10-variant datasets for
ETHUSDT (`eth_mtf_10variants_exact_retest.npz`, `eth_mtf_10variants_dayride.npz`,
and `eth_mtf_10variants_uncapped.npz`), and runs the exact same 3-sleeve
walk-forward ensemble (`mtf_10variants_no_overbought_handoff`) with zero
parameter changes. No live orders.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

from mtf_10variants_dayride import (
    DAY_MS,
    MAX_HOLD_H,
    SIX_MS,
    VARIANT_NAMES as DAYRIDE_NAMES,
    VariantEnsemble as DayrideEnsemble,
    _blocks,
    _halves,
    _ride_higher_tf,
    _rsi,
    _run_limit_higher_tf,
    _sma,
)
from mtf_10variants_exact_retest import (
    VARIANT_NAMES as EXACT_NAMES,
    ExactRetestEnsemble,
    _advance_bull_reset,
    _sim_exact_long_variant,
    _sim_exact_short_variant,
)
from mtf_10variants_no_overbought_handoff import (
    _pick_bear_dayride_thr,
    _pick_no_overbought_bull_thr,
    _run_bear_dayride_book,
    _run_no_overbought_bull_book,
)
from mtf_10variants_uncapped_consensus import (
    MAX_CAP_H,
    VARIANT_NAMES as UNCAPPED_NAMES,
    _sim_limit_variant,
)
from mtf_10variants_uncapped_zrank import ZRankVariantEnsemble
from path_dataset import (
    HOUR_MS,
    _concat,
    _iso,
    _load_bars,
    _page_arrays,
    _public_kline,
    _save_bars,
    _unique_sorted,
    dataset_paths,
)
from rest import BybitClient
from scan import state_dir
from sma_ride import YEAR_MS, _compound, _month

warnings.filterwarnings("ignore", category=UserWarning)

STEP_15M_MS = 15 * 60_000


def fetch_symbol_spot(
    client: BybitClient,
    symbol: str,
    interval: str,
    step_ms: int,
    start_ms: int,
    end_ms: int,
    cache: Path,
    max_pages: int = 300,
) -> tuple[np.ndarray, ...]:
    if cache.exists():
        cached = _unique_sorted(_load_bars(cache))
        if len(cached[0]) and int(cached[0][0]) <= start_ms + 2 * step_ms and int(cached[0][-1]) >= end_ms - 2 * step_ms:
            print(f"cache {cache.name} bars={len(cached[0])} {_iso(int(cached[0][0]))} -> {_iso(int(cached[0][-1]))}", flush=True)
            return cached
    parts: list[tuple[np.ndarray, ...]] = []
    cursor = end_ms
    pages = 0
    t0 = time.time()
    while pages < max_pages and cursor >= start_ms:
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": interval,
            "limit": "1000",
            "end": str(cursor),
        }
        data = _public_kline(client, params)
        rows = (data.get("result") or {}).get("list") or []
        pages += 1
        if not rows:
            break
        chunk = _page_arrays(rows)
        if parts and int(chunk[0][-1]) >= int(parts[0][0][0]):
            keep = chunk[0] < int(parts[0][0][0])
            if not np.any(keep):
                break
            chunk = tuple(col[keep] for col in chunk)
        parts.insert(0, chunk)
        oldest = int(chunk[0][0])
        if oldest <= start_ms or len(rows) < 1000:
            break
        cursor = oldest - 1
        if pages % 25 == 0:
            print(f"{symbol} interval={interval} pages={pages} oldest={_iso(oldest)} {time.time()-t0:.1f}s", flush=True)
        time.sleep(0.03)
    bars = _unique_sorted(_concat(parts))
    keep_span = (bars[0] >= start_ms) & (bars[0] <= end_ms)
    bars = tuple(col[keep_span] for col in bars)
    _save_bars(cache, bars)
    print(f"fetched {cache.name} bars={len(bars[0])} pages={pages} in {time.time()-t0:.1f}s", flush=True)
    return bars


def build_symbol_exact_retest(symbol_prefix: str, h1_bars: tuple, sub_bars: tuple, root: Path):
    cache = root / f"{symbol_prefix}_mtf_10variants_exact_retest.npz"
    if cache.exists():
        with np.load(cache) as data:
            return (
                data["d_ts"],
                data["x"],
                data["rets"],
                data["exits"],
                data["filled"],
                data["min_hit"],
                data["bull_sig"],
                data["bear_sig"],
            )
    t0 = time.time()
    h_ts, _ho, h_h, h_l, h_c, _hv = h1_bars
    m_ts, m_o, m_h, m_l, _mc, _mv = sub_bars
    h_ts = h_ts.astype(np.int64)
    m_ts = m_ts.astype(np.int64)
    d_ts = h_ts + HOUR_MS

    day_ts, day_h, day_l, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    h4_ts, h4_h, h4_l, h4_c = _blocks(h_ts, h_h, h_l, h_c, 4, 4)
    hour_rsi = _rsi(h_c, 14)
    fast_sma = _sma(h_c, 24)
    slow_sma = _sma(h_c, 72)
    h4_rsi = _rsi(h4_c, 14)
    h4_sma = _sma(h4_c, 18)
    day_rsi = _rsi(day_c, 14)
    day_sma50 = _sma(day_c, 50)
    day_sma30 = _sma(day_c, 30)

    n = len(h_c)
    x = np.full((n, 16), np.nan, dtype=np.float32)
    rets = np.zeros((n, 10), dtype=np.float32)
    exits = np.zeros((n, 10), dtype=np.int64)
    filled = np.zeros((n, 10), dtype=np.uint8)
    min_hit = np.zeros((n, 10), dtype=np.uint8)
    bull_sig = np.zeros(n, dtype=bool)
    bear_sig = np.zeros(n, dtype=bool)

    for k in range(72, n - 24):
        t = int(d_ts[k])
        di = int(np.searchsorted(day_ts, t, side="right")) - 1
        fi = int(np.searchsorted(h4_ts, t, side="right")) - 1
        if di < 55 or fi < 32 or int(day_ts[di]) != t:
            continue
        if not (
            np.isfinite(hour_rsi[k])
            and np.isfinite(fast_sma[k])
            and np.isfinite(slow_sma[k])
            and np.isfinite(slow_sma[k - 5])
            and np.isfinite(h4_rsi[fi])
            and np.isfinite(h4_sma[fi])
            and np.isfinite(day_rsi[di])
            and np.isfinite(day_sma50[di])
            and np.isfinite(day_sma50[di - 1])
            and np.isfinite(day_sma50[di - 5])
            and np.isfinite(day_sma30[di])
        ):
            continue
        entry = float(h_c[k])
        x[k, 0] = hour_rsi[k] / 100.0
        x[k, 1] = (entry - fast_sma[k]) / entry
        x[k, 2] = (entry - slow_sma[k]) / entry
        x[k, 3] = (fast_sma[k] - slow_sma[k]) / entry
        x[k, 4] = (slow_sma[k] - slow_sma[k - 5]) / entry
        x[k, 5] = entry / float(h_c[k - 6]) - 1.0
        x[k, 6] = entry / float(h_c[k - 24]) - 1.0
        x[k, 7] = (float(h_h[k]) - float(h_l[k])) / entry
        x[k, 8] = h4_rsi[fi] / 100.0
        x[k, 9] = (float(h4_c[fi]) - float(h4_sma[fi])) / float(h4_c[fi])
        x[k, 10] = (float(h4_h[fi]) - float(h4_l[fi])) / float(h4_c[fi])
        x[k, 11] = day_rsi[di] / 100.0
        x[k, 12] = (float(day_c[di]) - float(day_sma50[di])) / float(day_c[di])
        x[k, 13] = (float(day_sma50[di]) - float(day_sma50[di - 5])) / float(day_c[di])
        x[k, 14] = (float(day_h[di]) - float(day_l[di])) / float(day_c[di])
        x[k, 15] = 1.0 if (day_rsi[di] > 70.0 or h4_rsi[fi] > 70.0 or hour_rsi[k] > 70.0) else 0.0

        d_low = float(day_l[di])
        d_high = float(day_h[di])
        d_close = float(day_c[di])
        sma_now = float(day_sma50[di])
        prev_sma = float(day_sma50[di - 1])
        prev_low = float(day_l[di - 1])
        prev_high = float(day_h[di - 1])
        prev_close = float(day_c[di - 1])
        prior_low = float(day_l[di - 2])
        prior_high = float(day_h[di - 2])
        hr_low = float(h_l[k])
        hr_high = float(h_h[k])

        if (
            sma_now > float(day_sma50[di - 5])
            and d_close > sma_now
            and float(fast_sma[k]) > float(slow_sma[k])
            and sma_now < entry
            and d_low > sma_now
            and prev_low > prev_sma
            and prev_close > prev_sma
            and d_low < prev_low
            and prev_low >= prior_low
            and d_low < hr_low
            and hr_low > prev_low
        ):
            bull_sig[k] = True

        if (
            sma_now < float(day_sma50[di - 5])
            and d_close < sma_now
            and float(fast_sma[k]) < float(slow_sma[k])
            and sma_now > entry
            and d_high < sma_now
            and prev_high < prev_sma
            and prev_close < prev_sma
            and d_high > prev_high
            and prev_high <= prior_high
            and d_high > hr_high
            and hr_high < prev_high
        ):
            bear_sig[k] = True

        if d_close > sma_now and float(fast_sma[k]) > float(slow_sma[k]) and d_low < hr_low:
            mid_l = 0.5 * (hr_low + d_low)
            two_d_low = min(d_low, prev_low)
            b_specs = (
                (0, hr_low, d_low, None, day_ts, day_c, day_sma50),
                (1, mid_l, d_low, None, day_ts, day_c, day_sma50),
                (2, hr_low, two_d_low, None, day_ts, day_c, day_sma50),
                (3, hr_low, d_low, None, day_ts, day_c, day_sma30),
                (4, hr_low, d_low, 0.12, day_ts, day_c, day_sma50),
            )
            for vi, l_px, s_px, tp_v, tf_ts, tf_c, tf_sma in b_specs:
                r, ex, fl, mh = _sim_exact_long_variant(
                    k, l_px, s_px, tp_v, d_ts, fast_sma, slow_sma, day_ts, day_c, day_sma50, tf_ts, tf_c, tf_sma, m_ts, m_o, m_h, m_l
                )
                rets[k, vi], exits[k, vi], filled[k, vi], min_hit[k, vi] = r, ex, fl, mh

        if d_close < sma_now and float(fast_sma[k]) < float(slow_sma[k]) and d_high > hr_high:
            mid_h = 0.5 * (hr_high + d_high)
            two_d_high = max(d_high, prev_high)
            s_specs = (
                (5, hr_high, d_high, None, day_ts, day_c, day_sma50),
                (6, mid_h, d_high, None, day_ts, day_c, day_sma50),
                (7, hr_high, d_high, None, h4_ts, h4_c, h4_sma),
                (8, hr_high, d_high, 0.06, h4_ts, h4_c, h4_sma),
                (9, hr_high, two_d_high, 0.08, h4_ts, h4_c, h4_sma),
            )
            for vi, l_px, s_px, tp_v, tf_ts, tf_c, tf_sma in s_specs:
                r, ex, fl, mh = _sim_exact_short_variant(
                    k, l_px, s_px, tp_v, d_ts, fast_sma, slow_sma, day_ts, day_c, day_sma50, tf_ts, tf_c, tf_sma, m_ts, m_o, m_h, m_l
                )
                rets[k, vi], exits[k, vi], filled[k, vi], min_hit[k, vi] = r, ex, fl, mh

    np.savez(cache, d_ts=d_ts, x=x, rets=rets, exits=exits, filled=filled, min_hit=min_hit, bull_sig=bull_sig, bear_sig=bear_sig)
    print(f"built {cache.name} in {time.time() - t0:.1f}s", flush=True)
    return d_ts, x, rets, exits, filled, min_hit, bull_sig, bear_sig


def build_symbol_dayride(symbol_prefix: str, h1_bars: tuple, sub_bars: tuple, step_ms: int, root: Path):
    cache = root / f"{symbol_prefix}_mtf_10variants_dayride.npz"
    if cache.exists():
        with np.load(cache) as data:
            return data["close_ts"], data["x"], data["rets"], data["exits"], data["filled"]
    t0 = time.time()
    h_ts, _ho, h_h, h_l, h_c, _hv = h1_bars
    m_ts, m_o, m_h, m_l, m_c, _mv = sub_bars
    h_ts = h_ts.astype(np.int64)
    m_ts = m_ts.astype(np.int64)
    m_end_ts = m_ts + step_ms
    close_ts = h_ts + HOUR_MS

    day_ts, day_h, day_l, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    h4_ts, h4_h, h4_l, h4_c = _blocks(h_ts, h_h, h_l, h_c, 4, 4)
    hour_rsi = _rsi(h_c, 14)
    hour_sma24 = _sma(h_c, 24)
    hour_sma72 = _sma(h_c, 72)
    h4_rsi = _rsi(h4_c, 14)
    h4_sma = _sma(h4_c, 18)
    day_rsi = _rsi(day_c, 14)
    day_sma = _sma(day_c, 50)

    j0_all = np.searchsorted(m_ts, close_ts, side="left")
    j12_all = np.searchsorted(m_ts, close_ts + 12 * HOUR_MS, side="left")
    j24_all = np.searchsorted(m_ts, close_ts + 24 * HOUR_MS, side="left")
    jmax_all = np.searchsorted(m_ts, close_ts + MAX_HOLD_H * HOUR_MS, side="left")
    j_day_end = np.searchsorted(m_ts, day_ts, side="left")
    j_h4_end = np.searchsorted(m_ts, h4_ts, side="left")

    n = len(h_c)
    x = np.full((n, 16), np.nan, dtype=np.float32)
    rets = np.full((n, 10), np.nan, dtype=np.float32)
    exits = np.zeros((n, 10), dtype=np.int64)
    filled = np.zeros((n, 10), dtype=np.uint8)

    for i in range(72, n - MAX_HOLD_H - 1):
        j0 = int(j0_all[i])
        j12 = int(j12_all[i])
        j24 = int(j24_all[i])
        j_max = int(jmax_all[i])
        if j0 >= len(m_ts) or j_max <= j0:
            continue
        t = int(close_ts[i])
        di = int(np.searchsorted(day_ts, t, side="right")) - 1
        fi = int(np.searchsorted(h4_ts, t, side="right")) - 1
        if di < 55 or fi < 32:
            continue
        if min(h_c[i], h_c[i - 6], h_c[i - 24], day_c[di], h4_c[fi]) <= 0:
            continue
        if not (
            np.isfinite(hour_rsi[i])
            and np.isfinite(hour_sma24[i])
            and np.isfinite(hour_sma72[i])
            and np.isfinite(h4_rsi[fi])
            and np.isfinite(h4_sma[fi])
            and np.isfinite(day_rsi[di])
            and np.isfinite(day_sma[di])
            and np.isfinite(day_sma[di - 5])
        ):
            continue
        x[i, 0] = hour_rsi[i] / 100.0
        x[i, 1] = (h_c[i] - hour_sma24[i]) / h_c[i]
        x[i, 2] = (h_c[i] - hour_sma72[i]) / h_c[i]
        x[i, 3] = (hour_sma24[i] - hour_sma72[i]) / h_c[i]
        x[i, 4] = h_c[i] / h_c[i - 6] - 1.0
        x[i, 5] = h_c[i] / h_c[i - 24] - 1.0
        x[i, 6] = (h_h[i] - h_l[i]) / h_c[i]
        x[i, 7] = h4_rsi[fi] / 100.0
        x[i, 8] = (h4_c[fi] - h4_sma[fi]) / h4_c[fi]
        x[i, 9] = (h4_h[fi] - h4_l[fi]) / h4_c[fi]
        x[i, 10] = day_rsi[di] / 100.0
        x[i, 11] = (day_c[di] - day_sma[di]) / day_c[di]
        x[i, 12] = (day_sma[di] - day_sma[di - 5]) / day_c[di]
        x[i, 13] = (day_h[di] - day_l[di]) / day_c[di]
        x[i, 14] = 1.0 if (day_rsi[di] > 70.0 or h4_rsi[fi] > 70.0 or hour_rsi[i] > 70.0) else 0.0
        x[i, 15] = 1.0 if (day_rsi[di] < 30.0 or h4_rsi[fi] < 30.0 or hour_rsi[i] < 30.0) else 0.0

        entry_c = float(h_c[i])
        low_24 = float(np.min(h_l[i - 23 : i + 1]))
        high_24 = float(np.max(h_h[i - 23 : i + 1]))
        stop_l = min(low_24, entry_c * 0.98)
        stop_s = max(high_24, entry_c * 1.02)
        t12 = int(close_ts[i + 12])
        t24 = int(close_ts[i + 24])
        lim_l = float(h_l[i])
        lim_h = float(h_h[i])

        r, e = _ride_higher_tf(entry_c, t, j0, j_max, stop_l, 1, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 0], exits[i, 0], filled[i, 0] = r, e, 1
        if 0 < lim_l < entry_c:
            r, e, f = _run_limit_higher_tf(lim_l, lambda fp: min(low_24, fp * 0.98), 1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
            rets[i, 1], exits[i, 1], filled[i, 1] = r, e, f
        else:
            rets[i, 1], exits[i, 1], filled[i, 1] = 0.0, t24, 0
        r, e, f = _run_limit_higher_tf(entry_c * 0.99, lambda fp: fp * 0.975, 1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 2], exits[i, 2], filled[i, 2] = r, e, f
        r, e, f = _run_limit_higher_tf(entry_c * 0.98, lambda fp: fp * 0.97, 1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 3], exits[i, 3], filled[i, 3] = r, e, f
        if 0 < lim_l < entry_c:
            r, e, f = _run_limit_higher_tf(lim_l, lambda fp: min(low_24, fp * 0.98), 1, j0, j12, j_max, t12, h4_ts, h4_c, h4_sma, j_h4_end, m_o, m_h, m_l, m_c, m_end_ts)
            rets[i, 4], exits[i, 4], filled[i, 4] = r, e, f
        else:
            rets[i, 4], exits[i, 4], filled[i, 4] = 0.0, t12, 0

        r, e = _ride_higher_tf(entry_c, t, j0, j_max, stop_s, -1, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 5], exits[i, 5], filled[i, 5] = r, e, 1
        if lim_h > entry_c:
            r, e, f = _run_limit_higher_tf(lim_h, lambda fp: max(high_24, fp * 1.02), -1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
            rets[i, 6], exits[i, 6], filled[i, 6] = r, e, f
        else:
            rets[i, 6], exits[i, 6], filled[i, 6] = 0.0, t24, 0
        r, e, f = _run_limit_higher_tf(entry_c * 1.01, lambda fp: fp * 1.025, -1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 7], exits[i, 7], filled[i, 7] = r, e, f
        r, e, f = _run_limit_higher_tf(entry_c * 1.02, lambda fp: fp * 1.03, -1, j0, j24, j_max, t24, day_ts, day_c, day_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts)
        rets[i, 8], exits[i, 8], filled[i, 8] = r, e, f
        if lim_h > entry_c:
            r, e, f = _run_limit_higher_tf(lim_h, lambda fp: max(high_24, fp * 1.02), -1, j0, j12, j_max, t12, h4_ts, h4_c, h4_sma, j_h4_end, m_o, m_h, m_l, m_c, m_end_ts)
            rets[i, 9], exits[i, 9], filled[i, 9] = r, e, f
        else:
            rets[i, 9], exits[i, 9], filled[i, 9] = 0.0, t12, 0

    np.savez(cache, close_ts=close_ts, x=x, rets=rets, exits=exits, filled=filled)
    print(f"built {cache.name} in {time.time() - t0:.1f}s", flush=True)
    return close_ts, x, rets, exits, filled


def build_symbol_uncapped(symbol_prefix: str, h1_bars: tuple, sub_bars: tuple, step_ms: int, root: Path):
    cache = root / f"{symbol_prefix}_mtf_10variants_uncapped.npz"
    if cache.exists():
        with np.load(cache) as data:
            return data["close_ts"], data["x"], data["rets"], data["exits"], data["filled"]
    t0 = time.time()
    h_ts, _ho, h_h, h_l, h_c, _hv = h1_bars
    m_ts, m_o, m_h, m_l, m_c, _mv = sub_bars
    h_ts = h_ts.astype(np.int64)
    m_ts = m_ts.astype(np.int64)
    m_end_ts = m_ts + step_ms
    close_ts = h_ts + HOUR_MS

    day_ts, day_h, day_l, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    h4_ts, h4_h, h4_l, h4_c = _blocks(h_ts, h_h, h_l, h_c, 4, 4)
    hour_rsi = _rsi(h_c, 14)
    hour_sma24 = _sma(h_c, 24)
    hour_sma72 = _sma(h_c, 72)
    h4_rsi = _rsi(h4_c, 14)
    h4_sma = _sma(h4_c, 18)
    day_rsi = _rsi(day_c, 14)
    day_sma50 = _sma(day_c, 50)
    day_sma20 = _sma(day_c, 20)

    j0_all = np.searchsorted(m_ts, close_ts, side="left")
    j24_all = np.searchsorted(m_ts, close_ts + 24 * HOUR_MS, side="left")
    jmax_all = np.searchsorted(m_ts, close_ts + MAX_CAP_H * HOUR_MS, side="left")
    j_day_end = np.searchsorted(m_ts, day_ts, side="left")

    n = len(h_c)
    x = np.full((n, 18), np.nan, dtype=np.float32)
    rets = np.full((n, 10), np.nan, dtype=np.float32)
    exits = np.zeros((n, 10), dtype=np.int64)
    filled = np.zeros((n, 10), dtype=np.uint8)

    for i in range(72, n - 48):
        j0 = int(j0_all[i])
        j24 = int(j24_all[i])
        j_max = min(int(jmax_all[i]), len(m_ts))
        if j0 >= len(m_ts) or j24 <= j0 or j_max <= j24:
            continue
        t = int(close_ts[i])
        di = int(np.searchsorted(day_ts, t, side="right")) - 1
        fi = int(np.searchsorted(h4_ts, t, side="right")) - 1
        if di < 55 or fi < 32:
            continue
        if min(h_c[i], h_c[i - 6], h_c[i - 24], day_c[di], h4_c[fi]) <= 0:
            continue
        if not (
            np.isfinite(hour_rsi[i])
            and np.isfinite(hour_sma24[i])
            and np.isfinite(hour_sma72[i])
            and np.isfinite(h4_rsi[fi])
            and np.isfinite(h4_sma[fi])
            and np.isfinite(day_rsi[di])
            and np.isfinite(day_sma50[di])
            and np.isfinite(day_sma50[di - 1])
            and np.isfinite(day_sma50[di - 5])
            and np.isfinite(day_sma20[di])
        ):
            continue
        two_bull = (
            day_c[di] > day_sma50[di]
            and day_l[di] > day_sma50[di]
            and day_c[di - 1] > day_sma50[di - 1]
            and day_l[di - 1] > day_sma50[di - 1]
        )
        two_bear = (
            day_c[di] < day_sma50[di]
            and day_h[di] < day_sma50[di]
            and day_c[di - 1] < day_sma50[di - 1]
            and day_h[di - 1] < day_sma50[di - 1]
        )
        x[i, 0] = hour_rsi[i] / 100.0
        x[i, 1] = (h_c[i] - hour_sma24[i]) / h_c[i]
        x[i, 2] = (h_c[i] - hour_sma72[i]) / h_c[i]
        x[i, 3] = (hour_sma24[i] - hour_sma72[i]) / h_c[i]
        x[i, 4] = h_c[i] / h_c[i - 6] - 1.0
        x[i, 5] = h_c[i] / h_c[i - 24] - 1.0
        x[i, 6] = (h_h[i] - h_l[i]) / h_c[i]
        x[i, 7] = h4_rsi[fi] / 100.0
        x[i, 8] = (h4_c[fi] - h4_sma[fi]) / h4_c[fi]
        x[i, 9] = (h4_h[fi] - h4_l[fi]) / h4_c[fi]
        x[i, 10] = day_rsi[di] / 100.0
        x[i, 11] = (day_c[di] - day_sma50[di]) / day_c[di]
        x[i, 12] = (day_sma50[di] - day_sma50[di - 5]) / day_c[di]
        x[i, 13] = (day_h[di] - day_l[di]) / day_c[di]
        x[i, 14] = 1.0 if (day_rsi[di] > 70.0 or h4_rsi[fi] > 70.0 or hour_rsi[i] > 70.0) else 0.0
        x[i, 15] = 1.0 if (day_rsi[di] < 30.0 or h4_rsi[fi] < 30.0 or hour_rsi[i] < 30.0) else 0.0
        x[i, 16] = 1.0 if two_bull else 0.0
        x[i, 17] = 1.0 if two_bear else 0.0

        entry_c = float(h_c[i])
        bar_l = float(h_l[i])
        bar_h = float(h_h[i])
        low_24 = float(np.min(h_l[i - 23 : i + 1]))
        low_48 = float(np.min(h_l[i - 47 : i + 1]))
        high_24 = float(np.max(h_h[i - 23 : i + 1]))
        high_48 = float(np.max(h_h[i - 47 : i + 1]))
        sl_l24 = min(low_24, bar_l * 0.992)
        sl_l48 = min(low_48, bar_l * 0.985)
        sl_s24 = max(high_24, bar_h * 1.008)
        sl_s48 = max(high_48, bar_h * 1.015)
        t24 = int(close_ts[i + 24])

        long_specs = (
            (0, bar_l, sl_l24, None, day_sma50),
            (1, bar_l, sl_l48, None, day_sma50),
            (2, entry_c * 0.995, min(low_24, entry_c * 0.982), None, day_sma50),
            (3, entry_c * 0.99, min(low_48, entry_c * 0.975), None, day_sma50),
            (4, bar_l, sl_l24, 0.10, day_sma20),
        )
        for vi, lim_px, stop_px, tp_pct, d_sma in long_specs:
            if lim_px >= entry_c:
                rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t24, 0
            else:
                r, e, f = _sim_limit_variant(
                    lim_px, stop_px, tp_pct, 1, j0, j24, j_max, t24, day_ts, day_c, d_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts
                )
                rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

        short_specs = (
            (5, bar_h, sl_s24, None, day_sma50),
            (6, bar_h, sl_s48, None, day_sma50),
            (7, entry_c * 1.005, max(high_24, entry_c * 1.018), None, day_sma50),
            (8, entry_c * 1.01, max(high_48, entry_c * 1.025), None, day_sma50),
            (9, bar_h, sl_s24, 0.10, day_sma20),
        )
        for vi, lim_px, stop_px, tp_pct, d_sma in short_specs:
            if lim_px <= entry_c:
                rets[i, vi], exits[i, vi], filled[i, vi] = 0.0, t24, 0
            else:
                r, e, f = _sim_limit_variant(
                    lim_px, stop_px, tp_pct, -1, j0, j24, j_max, t24, day_ts, day_c, d_sma, j_day_end, m_o, m_h, m_l, m_c, m_end_ts
                )
                rets[i, vi], exits[i, vi], filled[i, vi] = r, e, f

    np.savez(cache, close_ts=close_ts, x=x, rets=rets, exits=exits, filled=filled)
    print(f"built {cache.name} in {time.time() - t0:.1f}s", flush=True)
    return close_ts, x, rets, exits, filled


def run_symbol_verification(symbol: str, symbol_prefix: str) -> None:
    root = state_dir()
    btc_paths = dataset_paths()
    btc_ts, *_ = _load_bars(btc_paths["h1"])
    start_ms = int(btc_ts[0])
    end_ms = int(btc_ts[-1])

    client = BybitClient()
    h1_bars = fetch_symbol_spot(
        client, symbol, "60", HOUR_MS, start_ms, end_ms, root / f"{symbol_prefix}_path_h1.npz", max_pages=40
    )
    sub_bars = fetch_symbol_spot(
        client, symbol, "15", STEP_15M_MS, start_ms, end_ms + HOUR_MS, root / f"{symbol_prefix}_path_15m.npz", max_pages=130
    )

    h_ts, _ho, h_h, h_l, h_c, _hv = h1_bars
    h_ts = h_ts.astype(np.int64)
    day_ts, _dh, _dl, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    fast_sma = _sma(h_c, 24)
    slow_sma = _sma(h_c, 72)
    day_sma50 = _sma(day_c, 50)

    # 1. Primary exact day-ending bull retest sleeve
    d_ts, x_d, rets_d, exits_d, filled_d, min_hit_d, bull_sig_d, _bear_sig_d = build_symbol_exact_retest(
        symbol_prefix, h1_bars, sub_bars, root
    )
    ready_d = np.flatnonzero(np.all(np.isfinite(x_d), axis=1))
    cut = int(d_ts[168]) + YEAR_MS

    next_bull_k = 0
    primary_bull_trades: list[dict] = []
    bull_busy_windows: list[tuple[int, int]] = []
    for k in ready_d:
        k = int(k)
        if not bool(bull_sig_d[k]) or k < next_bull_k:
            continue
        t = int(d_ts[k])
        ens_d = ExactRetestEnsemble()
        ens_d.fit(ready_d, x_d, rets_d, exits_d, filled_d, t - HOUR_MS)
        preds_d = ens_d.predict(x_d[k])
        valid = [v for v in (0, 1, 2, 3, 4) if preds_d[v] > 0.0 and ens_d._base[v] > 0.0]
        best_v = max(valid, key=lambda v: preds_d[v]) if valid else 0
        ex_ts = int(exits_d[k, best_v])
        fl = int(filled_d[k, best_v])
        mh = bool(min_hit_d[k, best_v])
        next_bull_k = _advance_bull_reset(k, ex_ts, mh, d_ts, fast_sma, slow_sma, day_ts, day_c, day_sma50)
        reset_ts = int(d_ts[min(next_bull_k, len(d_ts) - 1)])
        if fl == 1:
            if float(rets_d[k, best_v]) > 0.0:
                bull_busy_windows.append((t, reset_ts))
            else:
                bull_busy_windows.append((t, ex_ts + 18 * DAY_MS))
        if fl == 1 and t >= cut:
            primary_bull_trades.append(
                {
                    "entry_ts": t,
                    "exit_ts": ex_ts,
                    "ret": float(rets_d[k, best_v]),
                    "variant": EXACT_NAMES[best_v],
                    "side": 1,
                }
            )

    # 2. Bear regime sleeve
    close_dr, x_dr, rets_dr, exits_dr, filled_dr = build_symbol_dayride(
        symbol_prefix, h1_bars, sub_bars, STEP_15M_MS, root
    )
    ready_dr = np.flatnonzero(np.all(np.isfinite(x_dr), axis=1) & np.all(np.isfinite(rets_dr), axis=1))
    max_exit_dr = np.max(exits_dr[ready_dr], axis=1)
    test_dr = ready_dr[max_exit_dr >= cut]
    months_dr: list[str] = []
    for i in test_dr:
        m = str(_month(int(close_dr[i])))
        if not months_dr or months_dr[-1] != m:
            months_dr.append(m)

    bear_trades: list[dict] = []
    free_bear = -1
    for label in months_dr[1:]:
        prev = months_dr[months_dr.index(label) - 1]
        val_rows = ready_dr[np.array([str(_month(int(close_dr[i]))) == prev for i in ready_dr])]
        if not len(val_rows):
            continue
        val_start = int(close_dr[val_rows[0]]) - HOUR_MS
        train_rows = ready_dr[max_exit_dr <= val_start]
        live_rows = ready_dr[np.array([str(_month(int(close_dr[i]))) == label for i in ready_dr])]
        if len(train_rows) < 400 or not len(live_rows):
            continue
        ens_dr = DayrideEnsemble()
        ens_dr.fit(x_dr[train_rows], rets_dr[train_rows], filled_dr[train_rows])
        val_p = ens_dr.predict(x_dr[val_rows])
        thr_b = _pick_bear_dayride_thr(val_rows, close_dr, x_dr, val_p, rets_dr, exits_dr, filled_dr)
        live_p = ens_dr.predict(x_dr[live_rows])
        m_trades, free_bear = _run_bear_dayride_book(
            live_rows, close_dr, x_dr, live_p, rets_dr, exits_dr, filled_dr, thr_b, free_bear
        )
        bear_trades.extend(m_trades)

    # 3. Secondary bull z-rank sleeve with daily RSI <= 70 overbought guard
    close_u, x_u, rets_u, exits_u, filled_u = build_symbol_uncapped(
        symbol_prefix, h1_bars, sub_bars, STEP_15M_MS, root
    )
    ready_u = np.flatnonzero(np.all(np.isfinite(x_u), axis=1) & np.all(np.isfinite(rets_u), axis=1))
    test_u = ready_u[close_u[ready_u] >= cut]
    months_u: list[str] = []
    for i in test_u:
        m = str(_month(int(close_u[i])))
        if not months_u or months_u[-1] != m:
            months_u.append(m)

    secondary_bull_trades: list[dict] = []
    free_u = -1
    for label in months_u:
        live_rows = ready_u[np.array([str(_month(int(close_u[i]))) == label for i in ready_u])]
        if not len(live_rows):
            continue
        live_start = int(close_u[live_rows[0]]) - HOUR_MS
        val_start = live_start - YEAR_MS
        val_rows = ready_u[(close_u[ready_u] >= val_start) & (close_u[ready_u] <= live_start)]
        if len(val_rows) < 100:
            continue
        ens_u = ZRankVariantEnsemble()
        ens_u.fit(ready_u, x_u, rets_u, exits_u, filled_u, val_start)
        val_raw, val_z = ens_u.predict(x_u[val_rows])
        thr_l = _pick_no_overbought_bull_thr(
            val_rows, close_u, x_u, val_raw, val_z, rets_u, exits_u, filled_u, day_ts, day_c, day_sma50
        )
        live_raw, live_z = ens_u.predict(x_u[live_rows])
        m_tr, free_u = _run_no_overbought_bull_book(
            live_rows, close_u, x_u, live_raw, live_z, rets_u, exits_u, filled_u, day_ts, day_c, day_sma50, thr_l, free_u
        )
        for tr in m_tr:
            if not any(w0 <= tr["entry_ts"] <= w1 for w0, w1 in bull_busy_windows):
                secondary_bull_trades.append(tr)

    # 4. Merge all three non-overlapping streams chronologically
    all_candidates = sorted(
        primary_bull_trades + bear_trades + secondary_bull_trades,
        key=lambda t: t["entry_ts"],
    )
    merged: list[dict] = []
    free_ts = -1
    for tr in all_candidates:
        if tr["entry_ts"] < cut or tr["entry_ts"] < free_ts:
            continue
        merged.append(tr)
        free_ts = tr["exit_ts"]

    by_month: dict[str, list[float]] = {}
    for tr in merged:
        m = str(_month(tr["exit_ts"]))
        by_month.setdefault(m, []).append(tr["ret"])
    print(f"\n=== {symbol} RESULTS ===", flush=True)
    for m in sorted(by_month):
        pct = _compound(by_month[m]) * 100
        print(f"{symbol} {m} rule={pct:.2f} trades={len(by_month[m])}", flush=True)

    counts: dict[str, int] = {}
    for tr in merged:
        counts[tr["variant"]] = counts.get(tr["variant"], 0) + 1

    halves = _halves(merged, cut, int(d_ts[-1]))
    for row in halves:
        print(f"{symbol} half {row['from']} {row['pct']} trades={row['trades']}", flush=True)
    above = sum(1 for row in halves if row["pct"] > 0)
    summary = {
        "name": f"{symbol_prefix}_mtf_10variants_no_overbought_handoff",
        "symbol": symbol,
        "variant_counts": counts,
        "rule_total_pct": round(_compound([t["ret"] for t in merged]) * 100, 2) if merged else 0.0,
        "half_years": halves,
        "halves_above_zero": above,
        "stable_halves": bool(halves) and all(row["pct"] > 0 for row in halves),
        "months": {k: round(_compound(v) * 100, 2) for k, v in sorted(by_month.items())},
    }
    print("SUMMARY", summary, flush=True)
    out = root / f"{symbol_prefix}_mtf_10variants_no_overbought_handoff.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    run_symbol_verification("ETHUSDT", "eth")


if __name__ == "__main__":
    main()
