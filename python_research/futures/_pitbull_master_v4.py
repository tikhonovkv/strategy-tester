# -*- coding: utf-8 -*-
"""Walk-forward reaction net on top of the sized rules.
Do-nothing thresholds reproduce V1. A lock can re-enter only after an ema24 pullback.
One portfolio from 2022 through November 2026. Local only.
"""
from __future__ import annotations

import json

import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

import _anatomy_top25 as A
import _pitbull_master_audit as M
import schwartz_production as sp

HOLD, CUT, LOCK = "hold", "cut", "lock"
CAP = 20.0
_CACHE = {}


def clip(v, lo, hi):
    return float(np.clip(v, lo, hi))


def entry_x(s, series, i, side):
    atr = max(float(s["atr1h"][i]), 0.002)
    week = float(np.median(s["atr1h"][i - 167:i + 1])) if i >= 168 else atr
    dist = lambda back: A.dist_atr(s, i, side, atr, back) or 0.0
    return np.array([
        clip(A.trend_age(s["px"], s["ema240"], i, side) / 168.0, 0, 4),
        clip(atr / max(week, 1e-8), 0, 4),
        A.breadth_at(series, int(s["close_ts"][i]), side) / 7.0,
        clip(A.carry_z(s, i, side), -3, 3),
        clip(dist(0), -8, 8),
        clip(dist(24), -8, 8),
        clip(M.vol_ratio(s, i), 0, 5),
        1.0 if side == 1 else 0.0,
    ], dtype=np.float64), atr


def moves_of(s, i, side, atr):
    px, ts = s["px"], s["close_ts"]
    entry = float(px[i])
    out = []
    for h in range(1, 97):
        j = i + h
        if j >= len(px) or int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            break
        out.append(side * (float(px[j]) / entry - 1.0) / atr)
    return out


def rows_of(s, series, i, side):
    x0, atr = entry_x(s, series, i, side)
    moves = moves_of(s, i, side, atr)
    if len(moves) < 4:
        return []
    vol = s["vol"]
    rows = []
    peak = worst = moves[0]
    for h, cur in enumerate(moves, start=1):
        peak, worst = max(peak, cur), min(worst, cur)
        fut = moves[h - 1:]
        missed = max(0.0, max(fut) - cur)
        gave = max(0.0, cur - min(fut))
        later_max, later_min = max(fut), min(fut)
        if h <= 6 and cur <= -0.3 and later_max < 1.5 and later_min <= -0.8:
            y, w = CUT, 1.0 + 3.0 * abs(min(later_min, -0.5)) ** 2
        elif cur >= 2.0 and gave >= missed and gave >= 1.0:
            y, w = LOCK, 1.0 + 2.0 * gave
        elif missed >= 1.0:
            y, w = HOLD, 1.0 + 4.0 * missed
        elif cur < 0 and later_max < 0.5:
            y, w = CUT, 1.0 + 3.0 * abs(min(later_min, cur))
        else:
            y, w = HOLD, 1.0
        if not (h <= 6 or cur >= 2.0 or cur <= -0.3 or h % 6 == 0):
            continue
        j = i + h
        base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
        x = np.concatenate([x0, np.array([
            h / 96.0, clip(cur, -6, 12), clip(peak, -2, 12), clip(peak - cur, 0, 12),
            clip(worst, -6, 2), clip(moves[0], -6, 8), clip(M.vol_ratio(s, i), 0, 5),
            clip(float(vol[j]) / max(base, 1e-8), 0, 5),
        ], dtype=np.float64)])
        rows.append((x, y, min(w, CAP)))
    return rows


def fit(rows):
    if len(rows) < 40 or len({r[1] for r in rows}) < 2:
        return None
    scaler = StandardScaler()
    xs = scaler.fit_transform(np.vstack([r[0] for r in rows]))
    clf = MLPClassifier(
        hidden_layer_sizes=(24,), activation="relu", alpha=0.1,
        learning_rate_init=0.002, max_iter=160, early_stopping=True,
        validation_fraction=0.15, n_iter_no_change=10, random_state=0,
    )
    clf.fit(xs, [r[1] for r in rows], sample_weight=np.array([r[2] for r in rows], dtype=np.float64))
    return scaler, clf


def class_col(model, X, label):
    if model is None or len(X) == 0:
        return np.zeros(len(X))
    scaler, clf = model
    proba = clf.predict_proba(scaler.transform(X))
    if label not in list(clf.classes_):
        return np.zeros(len(X))
    return proba[:, list(clf.classes_).index(label)]


def probs(s, series, i, side, atr, model):
    key = (id(model) if model else 0, s["sym"], int(i), int(side))
    if key in _CACHE:
        return _CACHE[key]
    x0, _atr = entry_x(s, series, i, side)
    moves = moves_of(s, i, side, atr)
    vol = s["vol"]
    xs = []
    peak = worst = 0.0
    for h, cur in enumerate(moves, start=1):
        peak, worst = max(peak, cur), min(worst, cur)
        j = i + h
        base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
        xs.append(np.concatenate([x0, np.array([
            h / 96.0, clip(cur, -6, 12), clip(peak, -2, 12), clip(peak - cur, 0, 12),
            clip(worst, -6, 2), clip(moves[0], -6, 8), clip(M.vol_ratio(s, i), 0, 5),
            clip(float(vol[j]) / max(base, 1e-8), 0, 5),
        ], dtype=np.float64)]))
    X = np.vstack(xs) if xs else np.zeros((0, 16))
    packed = (class_col(model, X, CUT), class_col(model, X, LOCK))
    _CACHE[key] = packed
    return packed


def make_book(signals, series, btc, btc_map, idx, model_of, thr_of):
    book = []
    base = {
        "bail": True, "be": True, "ema": True, "impulse": False, "tighten": False,
        "target": None, "champion_holds": True, "reentry": True, "reentry_reasons": {"lock"},
    }
    for sig in signals:
        name = model_of(sig["t"])
        model = None if name is None else model_of.models[name]
        thr = {"cut": 1.01, "lock": 1.01} if name is None else thr_of[name]
        spec = dict(base)
        spec["thr_cut"] = thr["cut"]
        spec["thr_lock"] = thr["lock"]
        spec["vol_ratio"] = M.vol_ratio(series[sig["sym"]], sig["i"])

        def prob_fn(s, cursor, side, atr, model=model):
            if model is None:
                return None
            return probs(s, series, cursor, side, atr, model)

        legs = M.cascade(series[sig["sym"]], btc, btc_map, sig["i"], sig["side"], spec, prob_fn if model else None)
        if not legs:
            continue
        e = sig["e"]
        book.append({
            "sym": e["sym"], "side": int(e["side"]), "t": int(e["t"]),
            "hot_n": int(e.get("hot_n") or 0), "hot_avg": float(e.get("hot_avg") or 0.0),
            "c_lstreak": int(e.get("c_lstreak") or 0), "legs": legs,
        })
    return book


def collect(cands, series):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    out = []
    for e in cands:
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None or i < 168:
            continue
        out.append({"e": e, "i": i, "sym": e["sym"], "side": int(e["side"]), "t": int(e["t"])})
    return out


def main():
    cands, series = sp.build_tranche_candidates()
    signals = collect(cands, series)
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    folds = [
        ("2023", M.stamp(2022, 1, 1), M.stamp(2022, 7, 1), M.stamp(2023, 1, 1), M.stamp(2024, 1, 1)),
        ("2024", M.stamp(2022, 1, 1), M.stamp(2023, 1, 1), M.stamp(2024, 1, 1), M.stamp(2025, 1, 1)),
        ("2025", M.stamp(2022, 1, 1), M.stamp(2024, 1, 1), M.stamp(2025, 1, 1), M.stamp(2026, 1, 1)),
        ("2026H1", M.stamp(2022, 1, 1), M.stamp(2025, 1, 1), M.stamp(2026, 1, 1), sp.Q_CUT),
        ("2026Q", M.stamp(2022, 1, 1), M.stamp(2026, 1, 1), sp.Q_CUT, M.END),
    ]
    models = {}
    thrs = {}
    chosen = {}
    for label, train_from, val_from, test_from, test_to in folds:
        train_rows = []
        for sig in signals:
            if train_from <= sig["t"] and sig["t"] + 96 * sp.HOUR <= val_from:
                train_rows.extend(rows_of(series[sig["sym"]], series, sig["i"], sig["side"]))
        model = fit(train_rows)
        models[label] = model
        best = {"cut": 1.01, "lock": 1.01}
        best_final = -1e18
        val_sigs = [s for s in signals if val_from <= s["t"] < test_from]

        def model_of_val(t, label=label):
            return label

        model_of_val.models = {label: model}
        for cut in (0.60, 0.80, 1.01):
            for lock in (0.70, 1.01):
                thr = {"cut": cut, "lock": lock}
                book = make_book(val_sigs, series, btc, btc_map, None, model_of_val, {label: thr})
                res = M.simulate(book, val_from, test_from, old_boost=False)
                if res["final"] > best_final:
                    best, best_final = thr, res["final"]
        thrs[label] = best
        chosen[label] = {"thr": best, "val_final": round(best_final, 2), "train_rows": len(train_rows)}
        print(label, best, round(best_final, 2), "rows", len(train_rows), flush=True)

    bounds = {
        "2023": (M.stamp(2023, 1, 1), M.stamp(2024, 1, 1)),
        "2024": (M.stamp(2024, 1, 1), M.stamp(2025, 1, 1)),
        "2025": (M.stamp(2025, 1, 1), M.stamp(2026, 1, 1)),
        "2026H1": (M.stamp(2026, 1, 1), sp.Q_CUT),
        "2026Q": (sp.Q_CUT, M.END),
    }

    def model_of(t):
        for name, (a, b) in bounds.items():
            if a <= t < b:
                return name
        return None

    model_of.models = models
    book = make_book(signals, series, btc, btc_map, None, model_of, thrs)
    report, curves = M.run_named(book, old_boost=False)
    print("V4 full", report["full"], flush=True)
    path = sp.LAB / "_pitbull_master_audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["report"]["V4_nn"] = report
    payload["v4_chosen"] = chosen
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    curve_path = sp.LAB / "_pitbull_master_curves.json"
    all_curves = json.loads(curve_path.read_text(encoding="utf-8"))
    all_curves["V4_nn"] = curves
    curve_path.write_text(json.dumps(all_curves), encoding="utf-8")
    print("merged V4")


if __name__ == "__main__":
    main()
