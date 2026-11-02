# -*- coding: utf-8 -*-
"""Walk-forward the fast-bail distance. Entries stay Schwartz reclaim."""
from __future__ import annotations

import numpy as np

from causal import DAY, HOUR
from schwartz_pitbull import build_hourly_series
from schwartz_production import ALTS_7, TAKER_FEE, build_tranche_candidates, simulate_window
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026

BAILS = (0.5, 0.8, 1.1)


def replay(s, i, side, atr, bail_atr):
    px = s["px"]
    n = len(px)
    entry = float(px[i])
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    t_ms = int(s["close_ts"][i])
    exit_idx = i + 1
    reason = "end"
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    tp1_done = tp2_done = False
    tp1_move = tp2_move = 0.0
    for j in range(i + 1, min(n, i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
            exit_idx, reason = j - 1, "gap"
            break
        move = side * (float(px[j]) / entry - 1.0)
        min_move = min(min_move, move)
        h = j - i
        if h <= 4 and move <= -bail_atr * atr:
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
        cpx = float(px[j])
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
    exit_ts = int(s["close_ts"][exit_idx])
    lo = int(np.searchsorted(f_ts, t_ms, side="right"))
    hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
    f_adj = -paid if side == 1 else paid
    raw = side * (exit_px / entry - 1.0)
    rem = 1.0
    base = pyr_g = 0.0
    if tp1_done:
        w1 = 0.20 * rem
        base += w1 * tp1_move
        if pyr:
            pyr_g += w1 * (tp1_move - pyr_move)
        rem -= w1
    if tp2_done:
        w2 = 0.15 * rem
        base += w2 * tp2_move
        if pyr:
            pyr_g += w2 * (tp2_move - pyr_move)
        rem -= w2
    base += rem * raw
    if pyr:
        pyr_g += rem * (raw - pyr_move)
    net = base + f_adj - TAKER_FEE
    pyr_net = 0.50 * (pyr_g + 0.5 * f_adj - TAKER_FEE) if pyr else 0.0
    return {
        "exit_ts": exit_ts,
        "exit_px": round(exit_px, 6),
        "hold_h": int(exit_idx - i),
        "net": float(net),
        "pyr_net": float(pyr_net),
        "min_move": float(min_move),
        "reason": reason,
        "pyr": bool(pyr),
    }


def pack(part):
    t_max = max(e["t"] for e in part)
    cuts = {
        "30": [e for e in part if e["t"] >= t_max - 30 * DAY],
        "90": [e for e in part if e["t"] >= Q_CUT],
        "365": [e for e in part if e["t"] >= t_max - 365 * DAY],
        "2025": [e for e in part if Y2025 <= e["t"] < Y2026],
        "2026": [e for e in part if e["t"] >= Y2026],
        "full": part,
    }
    out = {}
    for name, sub in cuts.items():
        r = simulate_window([dict(e) for e in sub], core_gate=True, rank="rules")
        out[name] = (r["ret"], r["dd"], r["n"])
    return out


def line(name, res):
    bits = ["%-16s" % name]
    for w in ("30", "90", "365", "2025", "2026", "full"):
        a, b, n = res[w]
        bits.append("%s=%+6.1f/%0.fn%d" % (w, a, b, n))
    print("  ".join(bits), flush=True)


def main():
    base, _ = build_tranche_candidates()
    series = {sym: build_hourly_series(sym) for sym in ALTS_7}
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    versions = {b: [] for b in BAILS}
    for e in base:
        s = series[e["sym"]]
        i = idx[e["sym"]][int(e["t"])]
        atr = max(float(e["atr"]), 0.002)
        for b in BAILS:
            row = dict(e)
            row.update(replay(s, i, int(e["side"]), atr, b))
            versions[b].append(row)
    print("fixed bail", flush=True)
    line("BASE 0.8", pack(base))
    for b in BAILS:
        line("fixed %.1f" % b, pack(versions[b]))

    # walk-forward: every 60d pick bail by last 240d of CLOSED trades
    by_b = {}
    for b, rows in versions.items():
        by_b[b] = {(int(e["t"]), e["sym"]): e for e in rows}
    t0 = min(e["t"] for e in base)
    t1 = max(e["t"] for e in base)
    chosen = {}
    d = t0 + 240 * DAY
    picks = []
    while d < t1 + DAY:
        best_b, best_ret = 0.8, -1e9
        lo = d - 240 * DAY
        for b, rows in versions.items():
            part = [e for e in rows if lo <= e["t"] and e["exit_ts"] <= d]
            if len(part) < 25:
                continue
            ret = simulate_window(part, core_gate=True, rank="rules")["ret"]
            if ret > best_ret:
                best_b, best_ret = b, ret
        nxt = d + 60 * DAY
        picks.append((d, best_b, round(best_ret, 1)))
        for e in base:
            if d <= e["t"] < nxt:
                chosen[(int(e["t"]), e["sym"])] = best_b
        d = nxt
    stitched = []
    for e in base:
        b = chosen.get((int(e["t"]), e["sym"]), 0.8)
        stitched.append(by_b[b][(int(e["t"]), e["sym"])])
    print("picks", picks[-8:], flush=True)
    from collections import Counter
    print("bail mix", Counter(chosen.values()), flush=True)
    line("WF bail", pack(stitched))


if __name__ == "__main__":
    main()
