# -*- coding: utf-8 -*-
"""Hourly exit head. Entries stay the Schwartz reclaim. The model may only exit early.

At each bar after entry, features use only that bar. Label: the eventual
Schwartz path from here is a loss (bail/stop/negative end). If the model is
sure, close now. A runner already in profit is never touched.
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression

from causal import DAY, HOUR
from schwartz_pitbull import build_hourly_series
from schwartz_production import (
    ALTS_7,
    TAKER_FEE,
    build_tranche_candidates,
    simulate_window,
)
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026


def replay(s, i, side, atr, bail_fn=None):
    """Schwartz path. bail_fn(j, h, move, peak, state) may force an early exit."""
    px = s["px"]
    n = len(px)
    entry = float(px[i])
    exit_idx = i + 1
    reason = "end"
    peak = 0.0
    pyr = False
    pyr_move = 0.0
    tp1 = tp1_m = False, 0.0
    tp2 = tp2_m = False, 0.0
    tp1_done = False
    tp2_done = False
    tp1_move = tp2_move = 0.0
    for j in range(i + 1, min(n, i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
            exit_idx, reason = j - 1, "gap"
            break
        cpx = float(px[j])
        move = side * (cpx / entry - 1.0)
        peak = max(peak, move)
        h = j - i
        if bail_fn is not None and bail_fn(j, h, move, peak):
            exit_idx, reason = j, "model_bail"
            break
        if h <= 4 and move <= -0.8 * atr:
            exit_idx, reason = j, "fast_bail"
            break
        if move <= -2.2 * atr:
            exit_idx, reason = j, "hard_stop"
            break
        be = 1.15 * atr if pyr else 0.4 * atr
        if tp1_done and move <= be:
            exit_idx, reason = j, "tp_scale+be"
            break
        ema = float(s["ema240"][j])
        if (side == 1 and cpx < ema) or (side == -1 and cpx > ema):
            exit_idx, reason = j, "ema10_exit"
            break
        if not pyr and move >= 2.0 * atr:
            pyr, pyr_move = True, move
        if not tp1_done and move >= 3.0 * atr:
            tp1_done, tp1_move = True, move
        if not tp2_done and move >= 5.5 * atr:
            tp2_done, tp2_move = True, move
        exit_idx = j
    exit_px = float(px[exit_idx])
    raw = side * (exit_px / entry - 1.0)
    rem = 1.0
    gross = 0.0
    if tp1_done:
        gross += 0.20 * tp1_move
        rem -= 0.20
    if tp2_done:
        w2 = 0.15 * rem
        gross += w2 * tp2_move
        rem -= w2
    gross += rem * raw
    return exit_idx, reason, gross - TAKER_FEE, peak


def collect_bars(cands, series):
    """One row per hour of each open Schwartz trade. Future label, past features."""
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    rows = []
    for e in cands:
        s = series[e["sym"]]
        i0 = idx[e["sym"]].get(int(e["t"]))
        if i0 is None:
            continue
        side = int(e["side"])
        atr = max(float(e["atr"]), 0.002)
        entry = float(s["px"][i0])
        # eventual pnl from each future bar, computed once along the fixed path
        end_i, _, _, _ = replay(s, i0, side, atr)
        for j in range(i0 + 1, end_i + 1):
            if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR and j > i0 + 1:
                break
            move = side * (float(s["px"][j]) / entry - 1.0)
            # label: holding the remaining Schwartz path loses money from HERE
            rest_entry_move = side * (float(s["px"][end_i]) / float(s["px"][j]) - 1.0)
            y = 1 if rest_entry_move < -0.3 * atr else 0
            slope = float(s["ema240"][j]) / float(s["ema240"][j - 24]) - 1.0
            dist = side * (float(s["px"][j]) / float(s["ema240"][j]) - 1.0) / atr
            rows.append({
                "t": int(s["close_ts"][j]),
                "sym": e["sym"],
                "entry_t": int(e["t"]),
                "y": y,
                "x": np.array([
                    float(side),
                    move / atr,
                    (j - i0) / 24.0,
                    dist,
                    slope / atr,
                    1.0 if move < 0 else 0.0,
                ], dtype=np.float64),
            })
    return rows


def fit_walk(rows):
    rows = sorted(rows, key=lambda r: r["t"])
    by = {}
    for r in rows:
        by.setdefault((r["t"] // DAY) * DAY, []).append(r)
    clf = None
    mu = sd = None
    last = 0
    scored = []
    ordered = rows
    for d in sorted(by):
        if clf is None or d - last >= 21 * DAY:
            train = [r for r in ordered if r["t"] < d - 2 * DAY and r["t"] >= d - 400 * DAY]
            # a bar's label uses the trade exit, which can be up to 96h later
            train = [r for r in train if r["t"] + 96 * HOUR <= d]
            ys = [r["y"] for r in train]
            if len(train) >= 200 and len(set(ys)) == 2:
                X = np.vstack([r["x"] for r in train])
                mu = X.mean(0)
                sd = X.std(0)
                sd[sd < 1e-6] = 1.0
                clf = LogisticRegression(C=0.5, max_iter=200)
                clf.fit((X - mu) / sd, np.array(ys))
                last = d
        for r in by[d]:
            if clf is None:
                p = 0.0
            else:
                z = (r["x"].reshape(1, -1) - mu) / sd
                p = float(clf.predict_proba(z)[0, 1])
            scored.append((r["sym"], r["entry_t"], r["t"], p))
    return scored


def apply(cands, series, scored, thr):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    bail_at = {}
    for sym, entry_t, t, p in scored:
        if p >= thr:
            bail_at.setdefault((sym, entry_t), t)
    out = []
    for e in cands:
        r = dict(e)
        key = (e["sym"], int(e["t"]))
        cut = bail_at.get(key)
        if cut is None or cut >= int(e["exit_ts"]):
            out.append(r)
            continue
        s = series[e["sym"]]
        i0 = idx[e["sym"]][int(e["t"])]
        side = int(e["side"])
        atr = max(float(e["atr"]), 0.002)
        # only allow the model to cut a trade that is still red and not yet a pyramid
        def bail_fn(j, h, move, peak, cut=cut, s=s):
            if int(s["close_ts"][j]) < cut:
                return False
            if peak >= 2.0 * atr or move > 0.2 * atr:
                return False
            return True
        exit_i, reason, gross, _ = replay(s, i0, side, atr, bail_fn)
        if reason != "model_bail":
            out.append(r)
            continue
        r["exit_ts"] = int(s["close_ts"][exit_i])
        r["exit_px"] = round(float(s["px"][exit_i]), 6)
        r["hold_h"] = int(exit_i - i0)
        r["net"] = float(gross)
        r["pyr"] = False
        r["pyr_net"] = 0.0
        r["reason"] = reason
        out.append(r)
    return out


def pack(part):
    t_max = max(e["t"] for e in part)
    cuts = {
        "30": lambda e: e["t"] >= t_max - 30 * DAY,
        "90": lambda e: e["t"] >= Q_CUT,
        "365": lambda e: e["t"] >= t_max - 365 * DAY,
        "2025": lambda e: Y2025 <= e["t"] < Y2026,
        "2026": lambda e: e["t"] >= Y2026,
        "full": lambda e: True,
    }
    out = {}
    for name, fn in cuts.items():
        res = simulate_window([e for e in part if fn(e)], core_gate=True, rank="rules")
        out[name] = (res["ret"], res["dd"], res["n"])
    return out


def line(name, res):
    bits = ["%-18s" % name]
    for w in ("30", "90", "365", "2025", "2026", "full"):
        a, b, n = res[w]
        bits.append("%s=%+6.1f/%0.fn%d" % (w, a, b, n))
    print("  ".join(bits), flush=True)


def main():
    cands, _ = build_tranche_candidates()
    series = {sym: build_hourly_series(sym) for sym in ["BTCUSDT"] + ALTS_7}
    print("bars...", flush=True)
    rows = collect_bars(cands, series)
    print("hourly rows", len(rows), "pos rate", round(np.mean([r["y"] for r in rows]), 3), flush=True)
    scored = fit_walk(rows)
    base = pack(cands)
    line("BASE", base)
    for thr in (0.65, 0.75, 0.85):
        alt = apply(cands, series, scored, thr)
        ncut = sum(1 for a, b in zip(alt, cands) if a["reason"] == "model_bail")
        print("cuts", ncut, flush=True)
        line("bail p>=%.2f" % thr, pack(alt))


if __name__ == "__main__":
    main()
