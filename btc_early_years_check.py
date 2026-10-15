"""Early-years (2021-2024) verification of `mtf_10variants_no_overbought_handoff` on BTCUSDT.

Downloads BTCUSDT spot 1h and 15m bars from late 2021 through September 2024
(`2021-09-01` to `2024-09-04`, the exact 3-year window preceding the original
test period), builds the three 10-variant datasets on that early history, and
runs the exact same 3-sleeve walk-forward ensemble with zero parameter changes.
No live orders.
"""

from __future__ import annotations

import sys

from eth_mtf_10variants_check import (
    HOUR_MS,
    STEP_15M_MS,
    YEAR_MS,
    DayrideEnsemble,
    ExactRetestEnsemble,
    ZRankVariantEnsemble,
    _advance_bull_reset,
    _blocks,
    _compound,
    _halves,
    _month,
    _pick_bear_dayride_thr,
    _pick_no_overbought_bull_thr,
    _run_bear_dayride_book,
    _run_no_overbought_bull_book,
    _sma,
    build_symbol_dayride,
    build_symbol_exact_retest,
    build_symbol_uncapped,
    fetch_symbol_spot,
)
from mtf_10variants_dayride import DAY_MS
from mtf_10variants_exact_retest import VARIANT_NAMES as EXACT_NAMES
from path_dataset import _iso
from rest import BybitClient
from scan import state_dir
import json
import numpy as np


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    root = state_dir()
    symbol = "BTCUSDT"
    symbol_prefix = "btc_early_2021_2024"

    # Early window: 2021-07-01 UTC to 2024-09-04 UTC (preceding our 2024-09..2026-10 test window)
    start_ms = 1625097600000  # 2021-07-01 00:00:00 UTC
    end_ms = 1725429600000    # 2024-09-04 06:00:00 UTC

    client = BybitClient()
    h1_bars = fetch_symbol_spot(
        client, symbol, "60", HOUR_MS, start_ms, end_ms, root / f"{symbol_prefix}_path_h1.npz", max_pages=40
    )
    actual_start_ms = int(h1_bars[0][0])
    print(f"early BTC h1 actual span: {_iso(actual_start_ms)} -> {_iso(int(h1_bars[0][-1]))} ({len(h1_bars[0])} bars)", flush=True)

    sub_bars = fetch_symbol_spot(
        client, symbol, "15", STEP_15M_MS, actual_start_ms, end_ms + HOUR_MS, root / f"{symbol_prefix}_path_15m.npz", max_pages=130
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
    print(f"early test starts from {_month(cut)} ({_iso(cut)})", flush=True)

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
    print("\n=== EARLY BTCUSDT (2021-2024) RESULTS ===", flush=True)
    for m in sorted(by_month):
        pct = _compound(by_month[m]) * 100
        print(f"EARLY_BTC {m} rule={pct:.2f} trades={len(by_month[m])}", flush=True)

    counts: dict[str, int] = {}
    for tr in merged:
        counts[tr["variant"]] = counts.get(tr["variant"], 0) + 1

    halves = _halves(merged, cut, int(d_ts[-1]))
    for row in halves:
        print(f"EARLY_BTC half {row['from']} {row['pct']} trades={row['trades']}", flush=True)
    above = sum(1 for row in halves if row["pct"] > 0)
    summary = {
        "name": "btc_early_2021_2024_mtf_10variants_no_overbought_handoff",
        "symbol": symbol,
        "span": f"{_iso(actual_start_ms)} -> {_iso(int(h1_bars[0][-1]))}",
        "test_from": str(_month(cut)),
        "variant_counts": counts,
        "rule_total_pct": round(_compound([t["ret"] for t in merged]) * 100, 2) if merged else 0.0,
        "half_years": halves,
        "halves_above_zero": above,
        "stable_halves": bool(halves) and all(row["pct"] > 0 for row in halves),
        "months": {k: round(_compound(v) * 100, 2) for k, v in sorted(by_month.items())},
    }
    print("SUMMARY", summary, flush=True)
    out = root / "btc_early_2021_2024_mtf_10variants_no_overbought_handoff.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
