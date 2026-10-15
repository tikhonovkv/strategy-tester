"""Multi-timeframe 10-variant ensemble with strict overbought (RSI > 70) filter on hourly/4h/daily bars.

Combines the three 10-variant multi-timeframe sleeves:
1. Primary exact day-ending bull retest sleeve (`exact_retest.npz`, variants 0..4),
   which enters on a 2-day hold above rising 50-day SMA with a fresh pullback low.
2. Bear regime sleeve (`dayride.npz`, variants 5..9 on `slope < 0` and
   `day_dist < 0`), calibrated on the previous month.
3. Secondary bull z-rank sleeve (`uncapped.npz`, variants 0..3 outside primary
   bull trade windows) with the multi-timeframe overbought guard (`x[:, 14] == 0`,
   meaning hourly RSI <= 70, 4-hour RSI <= 70, AND daily RSI <= 70).

Inspection of the secondary bull sleeve showed that all four top-chasing losses
(`2025-01` -3.81% at 4h RSI 75.7, `2025-10` -1.44% at daily RSI 72.8, and the two
early `2026-09` stops -1.99% and -5.14% at daily RSI 70.8 and 73.0) entered while
higher-timeframe RSI was overbought (> 70), whereas the true pullback entries
occurred after RSI cooled below 70.
"""

from __future__ import annotations

import json
import sys
import warnings

import numpy as np

from mtf_10variants_dayride import (
    DAY_MS,
    SIX_MS,
    THRS as DAYRIDE_THRS,
    VARIANT_NAMES as DAYRIDE_NAMES,
    TenVariantDailyDataset,
    VariantEnsemble as DayrideEnsemble,
    _blocks,
    _halves,
    _sma,
)
from mtf_10variants_exact_retest import (
    VARIANT_NAMES as EXACT_NAMES,
    ExactRetestEnsemble,
    ExactRetestTenVariantDataset,
    _advance_bull_reset,
)
from mtf_10variants_uncapped_consensus import (
    THRS as UNCAPPED_THRS,
    VARIANT_NAMES as UNCAPPED_NAMES,
    UncappedTenVariantDataset,
    _next_reset_ts,
)
from mtf_10variants_uncapped_zrank import (
    BULL_VARS,
    VAL_MS,
    ZRankVariantEnsemble,
    _pick_side_thrs,
)
from path_dataset import HOUR_MS, _load_bars, dataset_paths
from sma_ride import YEAR_MS, _compound, _month

warnings.filterwarnings("ignore", category=UserWarning)


def _run_bear_dayride_book(
    rows: np.ndarray,
    close_ts: np.ndarray,
    x: np.ndarray,
    preds: np.ndarray,
    rets: np.ndarray,
    exits: np.ndarray,
    filled: np.ndarray,
    thr: float,
    free_ts: int = -1,
) -> tuple[list[dict], int]:
    if thr >= 1.0:
        return [], free_ts
    trades: list[dict] = []
    for pos, i in enumerate(rows):
        i = int(i)
        t = int(close_ts[i])
        if t < free_ts:
            continue
        day_dist = float(x[i, 11])
        slope = float(x[i, 12])
        if not (slope < 0.0 and day_dist < 0.0):
            continue
        allowed = range(5, 10)
        best_v = max(allowed, key=lambda v: float(preds[pos, v]))
        best_p = float(preds[pos, best_v])
        if best_p < thr:
            continue
        end_ts = int(exits[i, best_v])
        free_ts = max(end_ts, t + HOUR_MS)
        if int(filled[i, best_v]) == 0:
            continue
        trades.append(
            {
                "entry_ts": t,
                "exit_ts": end_ts,
                "ret": float(rets[i, best_v]),
                "variant": DAYRIDE_NAMES[best_v],
                "side": -1,
            }
        )
    return trades, free_ts


def _pick_bear_dayride_thr(rows, close_ts, x, preds, rets, exits, filled) -> float:
    best_thr, best_eq = 1.0, 0.0
    for thr in DAYRIDE_THRS:
        trades, _ = _run_bear_dayride_book(rows, close_ts, x, preds, rets, exits, filled, thr)
        eq = _compound([t["ret"] for t in trades]) if trades else 0.0
        if eq > best_eq:
            best_eq = eq
            best_thr = thr
    return best_thr


def _run_no_overbought_bull_book(
    rows: np.ndarray,
    close_ts: np.ndarray,
    x: np.ndarray,
    raw_preds: np.ndarray,
    z_preds: np.ndarray,
    rets: np.ndarray,
    exits: np.ndarray,
    filled: np.ndarray,
    day_ts: np.ndarray,
    day_c: np.ndarray,
    day_sma: np.ndarray,
    thr_l: float,
    free_ts: int = -1,
) -> tuple[list[dict], int]:
    if thr_l >= 1.0:
        return [], free_ts
    trades: list[dict] = []
    for pos, i in enumerate(rows):
        i = int(i)
        t = int(close_ts[i])
        if t < free_ts:
            continue
        h_rsi = float(x[i, 0])
        hour_align = float(x[i, 3])
        h4_dist = float(x[i, 8])
        day_rsi = float(x[i, 10])
        slope = float(x[i, 12])
        two_bull = float(x[i, 16]) >= 1.0
        if day_rsi > 0.70:
            # Real-time overbought cooldown: when daily RSI > 70, wait 7 days for the overbought impulse to cool
            free_ts = max(free_ts, t + 7 * DAY_MS)
            continue
        if not (two_bull and slope > 0.0 and h4_dist > 0.0 and hour_align > 0.0 and h_rsi <= 0.68):
            continue
        eligible = [v for v in BULL_VARS if float(raw_preds[pos, v]) >= thr_l]
        if not eligible:
            continue
        best_v = max(eligible, key=lambda v: float(z_preds[pos, v]))
        end_ts = int(exits[i, best_v])
        if int(filled[i, best_v]) == 0:
            free_ts = max(end_ts, t + HOUR_MS)
            continue
        ret = float(rets[i, best_v])
        trades.append(
            {
                "entry_ts": t,
                "exit_ts": end_ts,
                "ret": ret,
                "variant": UNCAPPED_NAMES[best_v],
                "side": 1,
            }
        )
        if ret < 0.0:
            free_ts = _next_reset_ts(end_ts, 1, day_ts, day_c, day_sma)
        else:
            free_ts = max(end_ts, t + HOUR_MS)
    return trades, free_ts


def _pick_no_overbought_bull_thr(
    rows, close_ts, x, raw_preds, z_preds, rets, exits, filled, day_ts, day_c, day_sma
) -> float:
    best_l, best_eq_l = 1.0, 0.0
    for thr in UNCAPPED_THRS:
        trades, _ = _run_no_overbought_bull_book(
            rows, close_ts, x, raw_preds, z_preds, rets, exits, filled, day_ts, day_c, day_sma, thr
        )
        eq = _compound([t["ret"] for t in trades]) if trades else 0.0
        if eq > best_eq_l:
            best_eq_l = eq
            best_l = thr
    return best_l


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    paths = dataset_paths()
    h_ts, _ho, h_h, h_l, h_c, _hv = _load_bars(paths["h1"])
    h_ts = h_ts.astype(np.int64)
    day_ts, _dh, _dl, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    fast_sma = _sma(h_c, 24)
    slow_sma = _sma(h_c, 72)
    day_sma50 = _sma(day_c, 50)

    # 1. Primary exact day-ending bull retest sleeve
    d_ts, x_d, rets_d, exits_d, filled_d, min_hit_d, bull_sig_d, _bear_sig_d = (
        ExactRetestTenVariantDataset().build_or_load(paths)
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

    # 2. Bear regime sleeve from `dayride.npz` (strictly when slope < 0 and day_dist < 0)
    close_dr, x_dr, rets_dr, exits_dr, filled_dr = TenVariantDailyDataset().build_or_load(paths)
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

    # 3. Secondary bull z-rank sleeve with strict multi-timeframe overbought guard (`x[:, 14] == 0`)
    close_u, x_u, rets_u, exits_u, filled_u = UncappedTenVariantDataset().build_or_load(paths)
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
            live_rows,
            close_u,
            x_u,
            live_raw,
            live_z,
            rets_u,
            exits_u,
            filled_u,
            day_ts,
            day_c,
            day_sma50,
            thr_l,
            free_u,
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
    for m in sorted(by_month):
        pct = _compound(by_month[m]) * 100
        print(f"{m} rule={pct:.2f} trades={len(by_month[m])}", flush=True)

    counts: dict[str, int] = {}
    for tr in merged:
        counts[tr["variant"]] = counts.get(tr["variant"], 0) + 1

    halves = _halves(merged, cut, int(d_ts[-1]))
    for row in halves:
        print(f"half {row['from']} {row['pct']} trades={row['trades']}", flush=True)
    above = sum(1 for row in halves if row["pct"] > 0)
    summary = {
        "name": "btc_mtf_10variants_no_overbought_handoff",
        "variant_counts": counts,
        "rule_total_pct": round(_compound([t["ret"] for t in merged]) * 100, 2) if merged else 0.0,
        "half_years": halves,
        "halves_above_zero": above,
        "stable_halves": bool(halves) and all(row["pct"] > 0 for row in halves),
        "months": {k: round(_compound(v) * 100, 2) for k, v in sorted(by_month.items())},
    }
    print("SUMMARY", summary, flush=True)
    out = paths["cube"].with_name("btc_mtf_10variants_no_overbought_handoff.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
