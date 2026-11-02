# -*- coding: utf-8 -*-
"""Same-hour ranker. Lone Schwartz signal is always taken.

Label: price hits +3 ATR before -0.8 ATR. Score only breaks ties
when two or more signals share an hour.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.linear_model import LogisticRegression

from causal import DAY, HOUR
from schwartz_pitbull import build_hourly_series
from schwartz_production import ALTS_7, CORE_4, build_tranche_candidates, simulate_window
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026


def race_label(s, i, side, atr):
    px = s["px"]
    entry = float(px[i])
    for j in range(i + 1, min(len(px), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
            return 0
        move = side * (float(px[j]) / entry - 1.0)
        h = j - i
        if h <= 4 and move <= -0.8 * atr:
            return 0
        if move <= -2.2 * atr:
            return 0
        if move >= 3.0 * atr:
            return 1
    return 0


def features(cands, series):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    btc = series["BTCUSDT"]
    btc_map = idx["BTCUSDT"]
    for e in cands:
        s = series[e["sym"]]
        i = idx[e["sym"]][int(e["t"])]
        side = int(e["side"])
        atr = max(float(e["atr"]), 0.002)
        e["y_race"] = race_label(s, i, side, atr)
        es = float(s["ema240"][i])
        slope = es / float(s["ema240"][i - 24]) - 1.0
        dist = side * (float(s["px"][i]) / es - 1.0) / atr
        bi = btc_map.get(int(e["t"]), i)
        btc_dist = side * (float(btc["px"][bi]) / float(btc["ema240"][bi]) - 1.0) / max(float(btc["atr1h"][bi]), 0.002)
        e["x"] = np.array([
            float(side),
            dist,
            slope / atr,
            btc_dist,
            float(e.get("hot_avg", 0.0)) * 20.0,
            float(e.get("c_lstreak", 0)),
            1.0 if e["sym"] in CORE_4 else 0.0,
        ], dtype=np.float64)


def score(cands):
    ordered = sorted(cands, key=lambda e: e["t"])
    by = defaultdict(list)
    for e in ordered:
        by[(e["t"] // DAY) * DAY].append(e)
    clf = None
    mu = sd = None
    last = 0
    out = []
    for d in sorted(by):
        if clf is None or d - last >= 30 * DAY:
            train = [e for e in ordered if e["t"] >= d - 500 * DAY and e["exit_ts"] <= d]
            ys = [e["y_race"] for e in train]
            if len(train) >= 80 and len(set(ys)) == 2:
                X = np.vstack([e["x"] for e in train])
                mu = X.mean(0)
                sd = X.std(0)
                sd[sd < 1e-6] = 1.0
                clf = LogisticRegression(C=0.4, max_iter=200)
                clf.fit((X - mu) / sd, np.array(ys))
                last = d
        for e in by[d]:
            r = dict(e)
            if clf is None:
                r["p_race"] = 0.5
            else:
                z = (r["x"].reshape(1, -1) - mu) / sd
                r["p_race"] = float(clf.predict_proba(z)[0, 1])
            r["nn_score"] = r["p_race"] + (0.01 if r["sym"] in CORE_4 else 0.0)
            out.append(r)
    return out


def pack(part, rank):
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
        r = simulate_window([dict(e) for e in sub], core_gate=True, rank=rank)
        out[name] = (r["ret"], r["dd"], r["n"])
    return out


def line(name, res):
    bits = ["%-14s" % name]
    for w in ("30", "90", "365", "2025", "2026", "full"):
        a, b, n = res[w]
        bits.append("%s=%+6.1f/%0.fn%d" % (w, a, b, n))
    print("  ".join(bits), flush=True)


def main():
    cands, _ = build_tranche_candidates()
    series = {sym: build_hourly_series(sym) for sym in ["BTCUSDT"] + ALTS_7}
    features(cands, series)
    print("race rate", round(np.mean([e["y_race"] for e in cands]), 3), flush=True)
    hours = defaultdict(list)
    for e in cands:
        if e["sym"] in CORE_4 or e.get("hot_n", 0) >= 4:
            hours[e["t"]].append(e)
    multi = [v for v in hours.values() if len(v) >= 2]
    print("hours", len(hours), "with 2+ signals", len(multi), flush=True)
    scored = score(cands)
    pay = [e["p_race"] for e in scored if e["y_race"] == 1]
    nop = [e["p_race"] for e in scored if e["y_race"] == 0]
    print("p|win", round(float(np.mean(pay)), 3), "p|lose", round(float(np.mean(nop)), 3), flush=True)
    hits = 0
    for batch in multi:
        sm = { (e["t"], e["sym"]): e for e in scored }
        got = [sm[(e["t"], e["sym"])] for e in batch]
        pick = max(got, key=lambda e: e["p_race"])
        best = max(got, key=lambda e: e["y_race"])
        hits += int(pick["sym"] == best["sym"] and pick["y_race"] == 1) if best["y_race"] == 1 else int(pick["y_race"] == 0)
    print("tie pick agrees with race winner", hits, "/", len(multi), flush=True)
    line("BASE", pack(cands, "rules"))
    line("RANK hour", pack(scored, "nn"))


if __name__ == "__main__":
    main()
