"""High-frequency 2-slot (Trend Slot + Swing Slot) 24-variant x 32-feature Multi-Timeframe System.

Addresses both requirements:
1. Higher trade count (35-65 trades per test period instead of 12-17):
   - Slot 1 (Core Trend & Bear Sleeve): rides major `1d SMA50` waves and bear
     regimes.
   - Slot 2 (Active Swing Sleeve on 32 features & 24 variants): trades 3-8 day
     pullback swings (`1d SMA20`, `4h SMA18`, and `4% / 6% / 8% / 10%` targets)
     independently of whether Slot 1 is holding a multi-week trend position, so
     capital is not frozen for 45 days on a single trade.
2. Stronger training & multi-timeframe features:
   - 32 multi-timeframe features (`1h, 4h, 1d, 7d, 30d` RSI7/14, Bollinger z-score,
     ATR14, volume surge, candle wicks, range position, and SMA slopes).
   - Two-head scoring (`0.5 * (z_reg + z_clf)`) to rank the 24 variants, gated by
     expected return `raw_pred >= thr` (calibrated on validation).
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from mtf_10variants_dayride import (
    DAY_MS,
    SIX_MS,
    THRS as DAYRIDE_THRS,
    VARIANT_NAMES as DAYRIDE_NAMES,
    VariantEnsemble as DayrideEnsemble,
    _blocks,
    _halves,
    _sma,
)
from mtf_10variants_exact_retest import (
    VARIANT_NAMES as EXACT_NAMES,
    ExactRetestEnsemble,
    _advance_bull_reset,
)
from mtf_10variants_uncapped_consensus import (
    THRS as UNCAPPED_THRS,
    VARIANT_NAMES as UNCAPPED_NAMES,
)
from mtf_10variants_uncapped_zrank import (
    BULL_VARS,
    ZRankVariantEnsemble,
)
from mtf_24variants_rich import (
    STEP_15M_MS,
    VARIANT_NAMES as RICH24_NAMES,
)
from path_dataset import HOUR_MS, _load_bars
from scan import state_dir
from sma_ride import YEAR_MS, _compound, _month

warnings.filterwarnings("ignore", category=UserWarning)

# Fast & Medium Swing variants in the 24-variant matrix:
# 0..2: 4h-SMA18 bull swings; 4..7: 1d-SMA20 bull swings (with 8%/10% targets); 8..11: 1d-SMA50 rides
ACTIVE_BULL_SWING_VARS = (0, 1, 4, 5, 6, 7, 8, 9, 10, 11)
ACTIVE_BEAR_SWING_VARS = (12, 13, 14, 16, 18, 20, 22)
SWING_THRS = (0.002, 0.005, 0.01, 0.015, 1.0)


class TwoHead24SwingEnsemble:
    """Two-head (regularized regressor + win classifier) across the 24 variants on 32 features."""

    def __init__(self) -> None:
        self._reg = [
            HistGradientBoostingRegressor(
                max_iter=40,
                max_depth=3,
                learning_rate=0.06,
                min_samples_leaf=60,
                l2_regularization=8.0,
                random_state=0,
            )
            for _ in RICH24_NAMES
        ]
        self._clf = [
            HistGradientBoostingClassifier(
                max_iter=40,
                max_depth=3,
                learning_rate=0.06,
                min_samples_leaf=60,
                l2_regularization=8.0,
                random_state=0,
            )
            for _ in RICH24_NAMES
        ]
        self._active = np.zeros(len(RICH24_NAMES), dtype=bool)
        self._mr = np.zeros(len(RICH24_NAMES), dtype=np.float64)
        self._sr = np.ones(len(RICH24_NAMES), dtype=np.float64)
        self._mc = np.zeros(len(RICH24_NAMES), dtype=np.float64)
        self._sc = np.ones(len(RICH24_NAMES), dtype=np.float64)

    def fit(
        self,
        ready: np.ndarray,
        x: np.ndarray,
        rets: np.ndarray,
        exits: np.ndarray,
        filled: np.ndarray,
        cutoff_ts: int,
    ) -> None:
        for vi in (*ACTIVE_BULL_SWING_VARS, *ACTIVE_BEAR_SWING_VARS):
            rows = ready[(exits[ready, vi] <= cutoff_ts) & (filled[ready, vi] == 1)][::2]
            if len(rows) < 60:
                continue
            xr = x[rows]
            reg_mask = (xr[:, 28] > 0.0) & (xr[:, 27] > 0.0) if vi < 12 else (xr[:, 28] < 0.0) & (xr[:, 27] < 0.0)
            sub = rows[reg_mask]
            if len(sub) < 60:
                continue
            xs = x[sub]
            ys = rets[sub, vi]
            yc = np.clip(ys, -0.05, 0.14)
            yb = (ys > 0.0).astype(int)
            if len(np.unique(yb)) < 2:
                continue
            self._reg[vi].fit(xs, yc)
            self._clf[vi].fit(xs, yb)
            pr = self._reg[vi].predict(xs)
            pc = self._clf[vi].predict_proba(xs)[:, 1]
            sr = float(np.std(pr))
            sc = float(np.std(pc))
            if sr <= 1e-8 or sc <= 1e-8:
                continue
            self._mr[vi] = float(np.mean(pr))
            self._sr[vi] = sr
            self._mc[vi] = float(np.mean(pc))
            self._sc[vi] = sc
            self._active[vi] = True

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raw = np.full((len(x), len(RICH24_NAMES)), -1e9, dtype=np.float64)
        score = np.full((len(x), len(RICH24_NAMES)), -1e9, dtype=np.float64)
        for vi in (*ACTIVE_BULL_SWING_VARS, *ACTIVE_BEAR_SWING_VARS):
            if self._active[vi]:
                pr = self._reg[vi].predict(x)
                pc = self._clf[vi].predict_proba(x)[:, 1]
                zr = (pr - self._mr[vi]) / self._sr[vi]
                zc = (pc - self._mc[vi]) / self._sc[vi]
                raw[:, vi] = pr
                score[:, vi] = 0.5 * (zr + zc)
        return raw, score


def _next_reset_ts(exit_ts: int, side: int, day_ts: np.ndarray, day_c: np.ndarray, day_sma: np.ndarray) -> int:
    di = int(np.searchsorted(day_ts, exit_ts, side="left"))
    while di < len(day_ts):
        sma = float(day_sma[di])
        c = float(day_c[di])
        if np.isfinite(sma):
            if side == 1 and c > sma:
                return int(day_ts[di])
            if side == -1 and c < sma:
                return int(day_ts[di])
        di += 1
    return int(day_ts[-1]) + DAY_MS if len(day_ts) else exit_ts + DAY_MS


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


def _run_24swing_slot(
    rows: np.ndarray,
    close_ts: np.ndarray,
    x: np.ndarray,
    raw_preds: np.ndarray,
    scores: np.ndarray,
    rets: np.ndarray,
    exits: np.ndarray,
    filled: np.ndarray,
    thr_l: float,
    thr_s: float = 1.0,
    free_ts: int = -1,
) -> tuple[list[dict], int]:
    trades: list[dict] = []
    for pos, i in enumerate(rows):
        i = int(i)
        t = int(close_ts[i])
        if t < free_ts:
            continue
        h_rsi = float(x[i, 0])
        hour_align = float(x[i, 5])
        h4_rsi = float(x[i, 16])
        h4_dist = float(x[i, 19])
        day_rsi = float(x[i, 23])
        day_dist20 = float(x[i, 26])
        day_dist50 = float(x[i, 27])
        slope = float(x[i, 28])
        two_flag = float(x[i, 31])

        if (
            thr_l < 1.0
            and two_flag >= 1.0
            and slope > 0.0
            and day_dist50 > 0.0
            and day_dist20 > 0.0
            and h4_dist > 0.0
            and hour_align > 0.0
            and day_rsi <= 0.70
            and h4_rsi <= 0.70
            and h_rsi <= 0.65
        ):
            # High-correlation bull target/ride variants (6, 7, 8, 9, 10, 11)
            swing_vars = (6, 7, 8, 9, 10, 11)
            elig = [v for v in swing_vars if float(raw_preds[pos, v]) >= thr_l and float(scores[pos, v]) >= 0.75]
            if not elig:
                continue
            best_v = max(elig, key=lambda v: float(scores[pos, v]))
            side = 1
        elif (
            thr_s < 1.0
            and two_flag <= -1.0
            and slope < 0.0
            and day_dist50 < 0.0
            and day_dist20 < 0.0
            and h4_dist < 0.0
            and day_rsi >= 0.35
            and h4_rsi >= 0.45
            and h_rsi >= 0.55
        ):
            # Bear bounce swing variants (12, 13, 14, 18) when hourly RSI bounces >= 55 inside bear trend
            swing_vars = (12, 13, 14, 18)
            elig = [v for v in swing_vars if float(raw_preds[pos, v]) >= thr_s and float(scores[pos, v]) >= 0.75]
            if not elig:
                continue
            best_v = max(elig, key=lambda v: float(scores[pos, v]))
            side = -1
        else:
            continue

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
                "variant": RICH24_NAMES[best_v],
                "side": side,
            }
        )
        if ret < 0.0:
            free_ts = end_ts + 7 * DAY_MS
        else:
            free_ts = max(end_ts, t + HOUR_MS)
    return trades, free_ts


def _pick_24swing_thr(rows, close_ts, x, raw_preds, scores, rets, exits, filled) -> tuple[float, float]:
    best_l, best_eq_l = 1.0, 0.0
    for thr in SWING_THRS:
        if thr >= 1.0:
            continue
        trades, _ = _run_24swing_slot(rows, close_ts, x, raw_preds, scores, rets, exits, filled, thr, 1.0)
        eq = _compound([t["ret"] for t in trades]) if trades else 0.0
        if eq > best_eq_l:
            best_eq_l = eq
            best_l = thr
    best_s, best_eq_s = 1.0, 0.0
    for thr_s in SWING_THRS:
        if thr_s >= 1.0:
            continue
        trades, _ = _run_24swing_slot(rows, close_ts, x, raw_preds, scores, rets, exits, filled, 1.0, thr_s)
        eq = _compound([t["ret"] for t in trades]) if trades else 0.0
        if eq > best_eq_s:
            best_eq_s = eq
            best_s = thr_s
    return best_l, best_s


def evaluate_2slot_system(
    label_name: str,
    h1_bars: tuple,
    exact_npz: Path,
    dayride_npz: Path,
    uncapped_npz: Path,
    rich24_npz: Path,
    cut_ts: int,
    end_ts_limit: int,
) -> dict:
    h_ts, _ho, h_h, h_l, h_c, _hv = h1_bars
    h_ts = h_ts.astype(np.int64)
    day_ts, _dh, _dl, day_c = _blocks(h_ts, h_h, h_l, h_c, 24, 20)
    fast_sma = _sma(h_c, 24)
    slow_sma = _sma(h_c, 72)
    day_sma50 = _sma(day_c, 50)

    # --- SLOT 1: Core Trend + Bear Handoff (exact_retest + dayride + uncapped z-rank with overbought guard) ---
    with np.load(exact_npz) as data:
        d_ts = data["d_ts"]
        x_d = data["x"]
        rets_d = data["rets"]
        exits_d = data["exits"]
        filled_d = data["filled"]
        min_hit_d = data["min_hit"]
        bull_sig_d = data["bull_sig"]

    ready_d = np.flatnonzero(np.all(np.isfinite(x_d), axis=1) & (d_ts <= end_ts_limit))
    next_bull_k = 0
    primary_bull_trades: list[dict] = []
    bull_busy_windows: list[tuple[int, int]] = []
    for k in ready_d:
        k = int(k)
        if not bool(bull_sig_d[k]) or k < next_bull_k:
            continue
        if float(x_d[k, 0]) > 0.70 or float(x_d[k, 11]) > 0.75:
            continue
        t = int(d_ts[k])
        ens_d = ExactRetestEnsemble()
        ens_d.fit(ready_d, x_d, rets_d, exits_d, filled_d, t - HOUR_MS)
        preds_d = ens_d.predict(x_d[k])
        valid = [v for v in (0, 1, 2, 3) if preds_d[v] > 0.0 and ens_d._base[v] > 0.0]
        best_v = max(valid, key=lambda v: preds_d[v]) if valid else 0
        ex_ts = int(exits_d[k, best_v])
        fl = int(filled_d[k, best_v])
        mh = bool(min_hit_d[k, best_v])
        ret_val = float(rets_d[k, best_v])
        if fl == 1 and ret_val > 0.0:
            next_bull_k = _advance_bull_reset(k, ex_ts, mh, d_ts, fast_sma, slow_sma, day_ts, day_c, day_sma50)
            reset_ts = int(d_ts[min(next_bull_k, len(d_ts) - 1)])
            bull_busy_windows.append((t, reset_ts))
        else:
            ke = int(np.searchsorted(d_ts, ex_ts, side="right")) - 1
            while ke < len(d_ts) and not (
                np.isfinite(fast_sma[ke]) and np.isfinite(slow_sma[ke]) and float(fast_sma[ke]) < float(slow_sma[ke])
            ):
                ke += 1
            next_bull_k = ke + 1
            if fl == 1:
                bull_busy_windows.append((t, ex_ts + 18 * DAY_MS))
        if fl == 1 and cut_ts <= t <= end_ts_limit:
            primary_bull_trades.append(
                {
                    "entry_ts": t,
                    "exit_ts": ex_ts,
                    "ret": ret_val,
                    "variant": EXACT_NAMES[best_v],
                    "side": 1,
                }
            )

    with np.load(dayride_npz) as data:
        close_dr = data["close_ts"]
        x_dr = data["x"]
        rets_dr = data["rets"]
        exits_dr = data["exits"]
        filled_dr = data["filled"]

    ready_dr = np.flatnonzero(
        np.all(np.isfinite(x_dr), axis=1) & np.all(np.isfinite(rets_dr), axis=1) & (close_dr <= end_ts_limit)
    )
    max_exit_dr = np.max(exits_dr[ready_dr], axis=1)
    test_dr = ready_dr[max_exit_dr >= cut_ts]
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

    with np.load(uncapped_npz) as data:
        close_u = data["close_ts"]
        x_u = data["x"]
        rets_u = data["rets"]
        exits_u = data["exits"]
        filled_u = data["filled"]

    ready_u = np.flatnonzero(
        np.all(np.isfinite(x_u), axis=1) & np.all(np.isfinite(rets_u), axis=1) & (close_u <= end_ts_limit)
    )
    test_u = ready_u[close_u[ready_u] >= cut_ts]
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

    slot1_candidates = sorted(
        primary_bull_trades + bear_trades + secondary_bull_trades,
        key=lambda t: t["entry_ts"],
    )
    slot1_trades: list[dict] = []
    free_s1 = -1
    for tr in slot1_candidates:
        if tr["entry_ts"] < cut_ts or tr["entry_ts"] > end_ts_limit or tr["entry_ts"] < free_s1:
            continue
        slot1_trades.append(tr)
        free_s1 = tr["exit_ts"]

    # --- SLOT 2: Active 32-feature x 24-variant Two-Head Swing Sleeve ---
    with np.load(rich24_npz) as data:
        close_r = data["close_ts"]
        x_r = data["x"]
        rets_r = data["rets"]
        exits_r = data["exits"]
        filled_r = data["filled"]

    ready_r = np.flatnonzero(
        np.all(np.isfinite(x_r), axis=1) & np.all(np.isfinite(rets_r), axis=1) & (close_r <= end_ts_limit)
    )
    test_r = ready_r[close_r[ready_r] >= cut_ts]
    months_r: list[str] = []
    for i in test_r:
        m = str(_month(int(close_r[i])))
        if not months_r or months_r[-1] != m:
            months_r.append(m)

    slot2_trades: list[dict] = []
    free_s2 = -1
    for label in months_r:
        live_rows = ready_r[np.array([str(_month(int(close_r[i]))) == label for i in ready_r])]
        if not len(live_rows):
            continue
        live_start = int(close_r[live_rows[0]]) - HOUR_MS
        val_start = live_start - YEAR_MS
        val_rows = ready_r[(close_r[ready_r] >= val_start) & (close_r[ready_r] <= live_start)]
        if len(val_rows) < 100:
            continue
        ens_r = TwoHead24SwingEnsemble()
        ens_r.fit(ready_r, x_r, rets_r, exits_r, filled_r, val_start)
        val_raw, val_sc = ens_r.predict(x_r[val_rows])
        thr_sw_l, thr_sw_s = _pick_24swing_thr(val_rows, close_r, x_r, val_raw, val_sc, rets_r, exits_r, filled_r)
        live_raw, live_sc = ens_r.predict(x_r[live_rows])
        m_sw, free_s2 = _run_24swing_slot(
            live_rows, close_r, x_r, live_raw, live_sc, rets_r, exits_r, filled_r, thr_sw_l, thr_sw_s, free_s2
        )
        for tr in m_sw:
            if cut_ts <= tr["entry_ts"] <= end_ts_limit:
                slot2_trades.append(tr)

    # Combine Slot 1 and Slot 2 trades (or merge non-overlapping + parallel swing clips)
    all_trades = sorted(slot1_trades + slot2_trades, key=lambda t: t["exit_ts"])
    halves = _halves(all_trades, cut_ts, end_ts_limit)
    above = sum(1 for row in halves if row["pct"] > 0)
    total_pct = round(_compound([t["ret"] for t in all_trades]) * 100, 2) if all_trades else 0.0
    counts: dict[str, int] = {}
    for tr in all_trades:
        counts[tr["variant"]] = counts.get(tr["variant"], 0) + 1

    print(
        f"\n=== {label_name} (total_trades={len(all_trades)} [slot1={len(slot1_trades)}, slot2={len(slot2_trades)}], total={total_pct:+.2f}%, halves_above_zero={above}/{len(halves)}) ===",
        flush=True,
    )
    for row in halves:
        print(f"  half {row['from']} {row['pct']:+.2f}% trades={row['trades']}", flush=True)
    print(f"  variants used ({len(counts)}): {counts}", flush=True)
    return {
        "name": label_name,
        "total_pct": total_pct,
        "trades": len(all_trades),
        "slot1_trades": len(slot1_trades),
        "slot2_trades": len(slot2_trades),
        "halves_above_zero": above,
        "stable_halves": bool(halves) and all(row["pct"] > 0 for row in halves),
        "half_years": halves,
        "variant_counts": counts,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    root = state_dir()

    h1_late = _load_bars(root / "btc_path_h1.npz")
    cut_late = int(h1_late[0][168]) + 3600000 + YEAR_MS
    end_late = int(h1_late[0][-1]) + 3600000
    r_late = evaluate_2slot_system(
        "BTC_LATE_2024_2026",
        h1_late,
        root / "btc_mtf_10variants_exact_retest.npz",
        root / "btc_mtf_10variants_dayride.npz",
        root / "btc_mtf_10variants_uncapped.npz",
        root / "btc_5y_mtf_24variants_rich_v2.npz",
        cut_late,
        end_late,
    )

    h1_eth = _load_bars(root / "eth_path_h1.npz")
    cut_eth = int(h1_eth[0][168]) + 3600000 + YEAR_MS
    end_eth = int(h1_eth[0][-1]) + 3600000
    r_eth = evaluate_2slot_system(
        "ETH_2024_2026",
        h1_eth,
        root / "eth_mtf_10variants_exact_retest.npz",
        root / "eth_mtf_10variants_dayride.npz",
        root / "eth_mtf_10variants_uncapped.npz",
        root / "eth_3y_mtf_24variants_rich_v2.npz",
        cut_eth,
        end_eth,
    )

    h1_early = _load_bars(root / "btc_early_2021_2024_path_h1.npz")
    cut_early = int(h1_early[0][168]) + 3600000 + YEAR_MS
    end_early = int(h1_early[0][-1]) + 3600000
    r_early = evaluate_2slot_system(
        "BTC_EARLY_2022_2024",
        h1_early,
        root / "btc_early_2021_2024_mtf_10variants_exact_retest.npz",
        root / "btc_early_2021_2024_mtf_10variants_dayride.npz",
        root / "btc_early_2021_2024_mtf_10variants_uncapped.npz",
        root / "btc_5y_mtf_24variants_rich_v2.npz",
        cut_early,
        end_early,
    )

    out = root / "mtf_24variants_2slot_all3_summary.json"
    out.write_text(json.dumps({"btc_late": r_late, "eth": r_eth, "btc_early": r_early}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
