# -*- coding: utf-8 -*-
"""Decide the pyramid at the bar price first hits +2 ATR, not at entry."""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression

from causal import DAY, HOUR
from schwartz_pitbull import build_hourly_series
from schwartz_production import ALTS_7, build_tranche_candidates, simulate_window
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026


def touch(s, i, side, atr):
    px = s["px"]
    entry = float(px[i])
    worst = 0.0
    for j in range(i + 1, min(len(px), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
            return None
        move = side * (float(px[j]) / entry - 1.0)
        worst = min(worst, move)
        h = j - i
        if h <= 4 and move <= -0.8 * atr:
            return None
        if move <= -2.2 * atr:
            return None
        if move >= 2.0 * atr:
            return j, h, worst
    return None


def annotate(cands, series):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    for e in cands:
        e["p_add"] = 0.5
        e["saw_2atr"] = 0
        s = series[e["sym"]]
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None:
            continue
        side = int(e["side"])
        atr = max(float(e["atr"]), 0.002)
        hit = touch(s, i, side, atr)
        if hit is None:
            continue
        j, h, worst = hit
        e["saw_2atr"] = 1
        e["y_add"] = 1 if e.get("pyr_net", 0.0) > 0.004 else 0
        dist = side * (float(s["px"][j]) / float(s["ema240"][j]) - 1.0) / atr
        slope = float(s["ema240"][j]) / float(s["ema240"][max(0, j - 24)]) - 1.0
        e["x"] = np.array([
            float(side), h / 24.0, worst / atr, dist, slope / atr, float(e.get("hot_avg", 0.0)) * 20.0,
        ], dtype=np.float64)
        e["feat_ready"] = True


def score(cands):
    trainable = [e for e in cands if e.get("feat_ready")]
    ordered = sorted(trainable, key=lambda e: e["t"])
    clf = None
    mu = sd = None
    last = 0
    by = {}
    for e in ordered:
        by.setdefault((e["t"] // DAY) * DAY, []).append(e)
    for d in sorted(by):
        if clf is None or d - last >= 30 * DAY:
            train = [e for e in ordered if e["exit_ts"] <= d and e["t"] >= d - 500 * DAY]
            ys = [e["y_add"] for e in train]
            if len(train) >= 60 and len(set(ys)) == 2:
                X = np.vstack([e["x"] for e in train])
                mu = X.mean(0)
                sd = X.std(0)
                sd[sd < 1e-6] = 1.0
                clf = LogisticRegression(C=0.5, max_iter=180)
                clf.fit((X - mu) / sd, np.array(ys))
                last = d
        for e in by[d]:
            if clf is None:
                e["p_add"] = 0.5
            else:
                z = (e["x"].reshape(1, -1) - mu) / sd
                e["p_add"] = float(clf.predict_proba(z)[0, 1])


def apply(cands, thr):
    out = []
    for e in cands:
        r = dict(e)
        if r.get("saw_2atr") and r.get("p_add", 0.5) < thr:
            r["pyr"] = False
            r["pyr_net"] = 0.0
        out.append(r)
    return out


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
    cands, _ = build_tranche_candidates()
    series = {sym: build_hourly_series(sym) for sym in ALTS_7}
    annotate(cands, series)
    score(cands)
    saw = [e for e in cands if e.get("saw_2atr")]
    print("touched +2 ATR", len(saw), "pay rate", round(np.mean([e["y_add"] for e in saw]), 3), flush=True)
    pay = [e["p_add"] for e in saw if e["y_add"] == 1]
    nop = [e["p_add"] for e in saw if e["y_add"] == 0]
    print("p|pay", round(float(np.mean(pay)), 3), "p|no", round(float(np.mean(nop)), 3), flush=True)
    line("BASE", pack(cands))
    for thr in (0.35, 0.45, 0.55):
        line("no pyr p<%.2f" % thr, pack(apply(cands, thr)))


if __name__ == "__main__":
    main()
