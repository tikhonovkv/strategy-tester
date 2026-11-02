# -*- coding: utf-8 -*-
"""Walk-forward pitbull reaction network.
Four penalties: sitting on a loss, taking a chop, giving back a sprint,
and cutting a move that keeps going. Local only. Does not touch the live bot.
"""
from __future__ import annotations

import json

import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

import _anatomy_top25 as A
import _sprint_reentry as R
import schwartz_production as sp

HOLD, CUT, LOCK = "hold", "cut", "lock"
W_CAP = 20.0


def clip(v, lo, hi):
    return float(np.clip(v, lo, hi))


def entry_x(s, series, i, side):
    atr = max(float(s["atr1h"][i]), 0.002)
    week = float(np.median(s["atr1h"][i - 167:i + 1])) if i >= 168 else atr
    vol = float(s["vol"][i])
    base = float(np.median(s["vol"][i - 24:i])) if i >= 24 else vol
    dist = lambda back: A.dist_atr(s, i, side, atr, back)
    vals = [
        clip(A.trend_age(s["px"], s["ema240"], i, side) / 168.0, 0, 4),
        clip(atr / max(week, 1e-8), 0, 4),
        A.breadth_at(series, int(s["close_ts"][i]), side) / 7.0,
        clip(A.carry_z(s, i, side), -3, 3),
        clip(dist(0) or 0.0, -8, 8),
        clip(dist(24) or 0.0, -8, 8),
        clip(dist(72) or 0.0, -8, 8),
        clip(vol / max(base, 1e-8), 0, 5),
    ]
    return np.array(vals, dtype=np.float64), atr


def forward_moves(s, i, side, atr, n=96):
    px, ts = s["px"], s["close_ts"]
    entry = float(px[i])
    out = []
    for h in range(1, n + 1):
        j = i + h
        if j >= len(px) or int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            break
        out.append(side * (float(px[j]) / entry - 1.0) / atr)
    return out


def manage_rows(s, series, i, side):
    x0, atr = entry_x(s, series, i, side)
    moves = forward_moves(s, i, side, atr)
    if len(moves) < 4:
        return [], None
    vol = s["vol"]
    rows = []
    peak = -1e9
    worst = 1e9
    for h, cur in enumerate(moves, start=1):
        j = i + h
        peak = max(peak, cur)
        worst = min(worst, cur)
        fut = moves[h - 1:]
        later_max = max(fut)
        later_min = min(fut)
        missed = max(0.0, later_max - cur)
        gave = max(0.0, cur - later_min)
        if h <= 6 and cur <= -0.3 and later_max < 1.5 and later_min <= -0.8:
            y, w = CUT, 1.0 + 3.0 * (abs(min(later_min, -0.5)) ** 2)
        elif cur >= 2.0 and gave >= missed and gave >= 1.0:
            y, w = LOCK, 1.0 + 2.0 * gave
        elif missed >= 1.0:
            y, w = HOLD, 1.0 + 4.0 * missed
        elif cur < 0 and later_max < 0.5:
            y, w = CUT, 1.0 + 3.0 * abs(min(later_min, cur))
        else:
            y, w = HOLD, 1.0
        interesting = h <= 6 or cur >= 2.0 or cur <= -0.3 or h % 6 == 0
        if not interesting:
            continue
        base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
        m1 = moves[0]
        m3 = moves[min(2, len(moves) - 1)]
        x = np.concatenate([x0, np.array([
            h / 96.0,
            clip(cur, -6, 12),
            clip(peak, -2, 12),
            clip(peak - cur, 0, 12),
            clip(worst, -6, 2),
            clip(m1, -6, 8),
            clip(m3, -6, 8),
            clip(float(vol[j]) / max(base, 1e-8), 0, 5),
        ], dtype=np.float64)])
        rows.append((x, y, min(w, W_CAP)))
    return rows, (x0, moves, atr)


def entry_row(packet):
    x0, moves, _atr = packet
    peak = max(moves) if moves else 0.0
    final = moves[-1] if moves else 0.0
    skip = peak < 1.5 and final < 0
    if skip:
        w = 1.0 + 2.0 * abs(final)
        y = 1
    else:
        w = 1.0 + 4.0 * max(peak, 0.0)
        y = 0
    return x0, y, min(w, W_CAP)


def fit_clf(rows):
    if len(rows) < 40:
        return None
    y = [r[1] for r in rows]
    if len(set(y)) < 2:
        return None
    X = np.vstack([r[0] for r in rows])
    w = np.array([r[2] for r in rows], dtype=np.float64)
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    clf = MLPClassifier(
        hidden_layer_sizes=(24,), activation="relu", alpha=0.1,
        learning_rate_init=0.002, max_iter=160, early_stopping=True,
        validation_fraction=0.15, n_iter_no_change=10, random_state=0,
    )
    clf.fit(xs, y, sample_weight=w)
    return scaler, clf


def class_p(model, X, label):
    if model is None or len(X) == 0:
        return np.zeros(len(X))
    scaler, clf = model
    proba = clf.predict_proba(scaler.transform(X))
    classes = list(clf.classes_)
    if label not in classes:
        return np.zeros(len(X))
    return proba[:, classes.index(label)]


def finish(s, i, exit_i, side, atr, reason, pyr, pyr_move):
    px, ts = s["px"], s["close_ts"]
    entry = float(px[i]) * (1.0 + side * R.SLIP)
    exit_px = float(px[exit_i]) * (1.0 - side * R.SLIP)
    raw = side * (exit_px / entry - 1.0)
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, int(ts[i]), side="right"))
    hi = int(np.searchsorted(f_ts, int(ts[exit_i]), side="right"))
    f_adj = 0.0
    if hi > lo:
        paid = float(f_rate[lo:hi].sum())
        f_adj = -paid if side == 1 else paid
    net = raw + f_adj - sp.TAKER_FEE
    pyr_net = 0.50 * ((raw - pyr_move) + 0.5 * f_adj - sp.TAKER_FEE) if pyr else 0.0
    return {
        "entry_i": i, "exit_i": exit_i, "t": int(ts[i]), "exit_ts": int(ts[exit_i]),
        "entry_px": float(px[i]), "net": float(net), "pyr_net": float(pyr_net),
        "atr": float(atr), "reason": reason, "win": net > 0,
    }


def play_leg(s, i, side, deadline, atr, p_cut, p_lock, moves, thr):
    px, ts, ema = s["px"], s["close_ts"], s["ema240"]
    entry = float(px[i]) * (1.0 + side * R.SLIP)
    peak = 0.0
    pyr = False
    pyr_move = 0.0
    exit_i = i
    reason = "end_96h"
    for h in range(1, min(deadline - i, len(moves) + 1)):
        j = i + h
        if j >= len(px) or int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            exit_i = max(i, j - 1)
            reason = "gap"
            break
        c = float(px[j])
        move = side * (c / entry - 1.0)
        peak = max(peak, move)
        if not pyr and move >= 2.0 * atr:
            pyr = True
            pyr_move = move
        if move <= -2.2 * atr:
            exit_i, reason = j, "hard_stop"
            break
        if h <= 6 and move < 1.0 * atr and p_cut[h - 1] >= thr["cut"]:
            exit_i, reason = j, "model_cut"
            break
        if h <= 4 and move <= -0.8 * atr:
            exit_i, reason = j, "fast_bail"
            break
        if move >= 2.0 * atr and p_lock[h - 1] >= thr["lock"]:
            exit_i, reason = j, "lock"
            break
        if peak >= 3.0 * atr and move <= (1.15 * atr if pyr else 0.4 * atr):
            exit_i, reason = j, "breakeven"
            break
        if (side == 1 and c < float(ema[j])) or (side == -1 and c > float(ema[j])):
            exit_i, reason = j, "ema10_exit"
            break
        exit_i = j
    if exit_i <= i:
        return None
    return finish(s, i, exit_i, side, atr, reason, pyr, pyr_move)


_PROB = {}


def cached_probs(s, series, i, side, models):
    key = (id(models.get("manage")), s["sym"], int(i), int(side))
    hit = _PROB.get(key)
    if hit is not None:
        return hit
    x0, atr = entry_x(s, series, i, side)
    moves = forward_moves(s, i, side, atr)
    vol = s["vol"]
    hour_x = []
    peak = -1e9
    worst = 1e9
    for h, cur in enumerate(moves, start=1):
        j = i + h
        peak = max(peak, cur)
        worst = min(worst, cur)
        base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
        hour_x.append(np.concatenate([x0, np.array([
            h / 96.0, clip(cur, -6, 12), clip(peak, -2, 12), clip(peak - cur, 0, 12),
            clip(worst, -6, 2), clip(moves[0], -6, 8), clip(moves[min(2, len(moves) - 1)], -6, 8),
            clip(float(vol[j]) / max(base, 1e-8), 0, 5),
        ], dtype=np.float64)]))
    X = np.vstack(hour_x) if hour_x else np.zeros((0, 16))
    packed = (class_p(models["manage"], X, CUT), class_p(models["manage"], X, LOCK), moves, atr)
    _PROB[key] = packed
    return packed


def nn_cascade(s, btc, btc_map, series, i, side, models, thr):
    deadline = min(len(s["px"]), i + 96)
    legs = []
    cursor = i
    while cursor < deadline and len(legs) < 4:
        p_cut, p_lock, moves, atr = cached_probs(s, series, cursor, side, models)
        if len(moves) < 2:
            break
        leg = play_leg(s, cursor, side, deadline, atr, p_cut, p_lock, moves, thr)
        if leg is None:
            break
        legs.append(leg)
        if leg["reason"] != "lock" or not leg["win"]:
            break
        nxt = None
        for wait in range(1, 4):
            j = leg["exit_i"] + wait
            if j >= deadline:
                break
            if R.trend_ok(s, btc, btc_map, j, side):
                nxt = j
                break
        if nxt is None:
            break
        x_next, _atr_next = entry_x(s, series, nxt, side)
        if class_p(models["entry"], x_next.reshape(1, -1), 1)[0] >= thr["skip"]:
            break
        cursor = nxt
    return legs


def collect(cands, series):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    out = []
    for e in cands:
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None or i < 168:
            continue
        out.append({
            "e": e, "i": i, "sym": e["sym"], "side": int(e["side"]), "t": int(e["t"]),
        })
    return out


def train_models(signals, series, t0, t1):
    manage, entry = [], []
    for sig in signals:
        if not (t0 <= sig["t"] and sig["t"] + 96 * sp.HOUR <= t1):
            continue
        rows, packet = manage_rows(series[sig["sym"]], series, sig["i"], sig["side"])
        manage.extend(rows)
        if packet is not None:
            entry.append(entry_row(packet))
    return {"manage": fit_clf(manage), "entry": fit_clf(entry)}, len(manage), len(entry)


def book_from(signals, series, btc, btc_map, models, thr, t0, t1, use_nn):
    book = []
    spec = R.SPECS["rules"]
    for sig in signals:
        if not (t0 <= sig["t"] < t1):
            continue
        e = sig["e"]
        s = series[sig["sym"]]
        if use_nn:
            legs = nn_cascade(s, btc, btc_map, series, sig["i"], sig["side"], models, thr)
        else:
            legs = R.cascade(s, btc, btc_map, sig["i"], sig["side"], spec)
        if not legs:
            continue
        book.append({
            "sym": e["sym"], "coin": e["coin"], "side": int(e["side"]),
            "hot_n": int(e.get("hot_n") or 0), "hot_avg": float(e.get("hot_avg") or 0.0),
            "c_lstreak": int(e.get("c_lstreak") or 0), "legs": legs,
        })
    return book


def choose(signals, series, btc, btc_map, models, t0, t1):
    grid = []
    for cut in (0.55, 0.70, 0.85, 1.01):
        for lock in (0.60, 0.80, 1.01):
            for skip in (0.70, 1.01):
                grid.append({"cut": cut, "lock": lock, "skip": skip})
    best, best_final = {"cut": 1.01, "lock": 1.01, "skip": 1.01}, -1e18
    for thr in grid:
        book = book_from(signals, series, btc, btc_map, models, thr, t0, t1, True)
        res = R.simulate(book, t0, t1)
        if res["final"] > best_final:
            best, best_final = thr, res["final"]
    return best, round(best_final, 2)


def main():
    cands, series = sp.build_tranche_candidates()
    signals = collect(cands, series)
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    folds = [
        ("2023", R.stamp(2022, 1, 1), R.stamp(2022, 7, 1), R.stamp(2023, 1, 1), R.stamp(2024, 1, 1)),
        ("2024", R.stamp(2022, 1, 1), R.stamp(2023, 1, 1), R.stamp(2024, 1, 1), R.stamp(2025, 1, 1)),
        ("2025", R.stamp(2022, 1, 1), R.stamp(2024, 1, 1), R.stamp(2025, 1, 1), R.stamp(2026, 1, 1)),
        ("2026H1", R.stamp(2022, 1, 1), R.stamp(2025, 1, 1), R.stamp(2026, 1, 1), sp.Q_CUT),
        ("2026Q", R.stamp(2022, 1, 1), R.stamp(2026, 1, 1), sp.Q_CUT, R.stamp(2026, 11, 3)),
    ]
    chosen = {}
    parts = []
    report = {"nn": {}, "rules": {}}
    for label, train_from, val_from, test_from, test_to in folds:
        models, n_m, n_e = train_models(signals, series, train_from, val_from)
        thr, val_final = choose(signals, series, btc, btc_map, models, val_from, test_from)
        chosen[label] = {"thr": thr, "val_final": val_final, "train_manage": n_m, "train_entry": n_e}
        print(label, "thr", thr, "val", val_final, "rows", n_m, n_e, flush=True)
        nn_book = book_from(signals, series, btc, btc_map, models, thr, test_from, test_to, True)
        rules_book = book_from(signals, series, btc, btc_map, models, thr, test_from, test_to, False)
        nn_res = R.simulate(nn_book, test_from, test_to)
        rules_res = R.simulate(rules_book, test_from, test_to)
        nn_curve = nn_res.pop("curve")
        rules_res.pop("curve")
        report["nn"][label] = nn_res
        report["rules"][label] = rules_res
        parts.append(nn_book)
        print(" ", label, "nn", nn_res["ret"], nn_res["final"], "rules", rules_res["ret"], rules_res["final"], flush=True)
        if label == "2026Q":
            report["nn_curve_last"] = nn_curve
    # Continuous book: 2022 rules, then each tested year with its own model.
    # Rebuild 2022 rules and concatenate the already chosen test books.
    rules_2022 = book_from(signals, series, btc, btc_map, None, {"cut": 1.01, "lock": 1.01, "skip": 1.01}, R.stamp(2022, 1, 1), R.stamp(2023, 1, 1), False)
    # The test books above were built inside the loop and not kept. Rebuild from chosen thr is expensive
    # because models were not stored. Keep books in the loop instead.
    full_book = rules_2022 + [b for part in parts for b in part]
    full = R.simulate(full_book, R.stamp(2022, 1, 1), R.stamp(2026, 11, 3))
    full_curve = full.pop("curve")
    rules_full_book = book_from(signals, series, btc, btc_map, None, {"cut": 1.01, "lock": 1.01, "skip": 1.01}, R.stamp(2022, 1, 1), R.stamp(2026, 11, 3), False)
    rules_full = R.simulate(rules_full_book, R.stamp(2022, 1, 1), R.stamp(2026, 11, 3))
    rules_curve = rules_full.pop("curve")
    report["nn"]["full"] = full
    report["rules"]["full"] = rules_full
    print("FULL nn", full, flush=True)
    print("FULL rules", rules_full, flush=True)
    out = {
        "chosen": chosen,
        "report": {"nn": report["nn"], "rules": report["rules"]},
    }
    (sp.LAB / "_pitbull_reaction_nn.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    (sp.LAB / "_pitbull_curves.json").write_text(json.dumps({"nn": full_curve, "rules": rules_curve}), encoding="utf-8")
    print("wrote", sp.LAB / "_pitbull_reaction_nn.json")


if __name__ == "__main__":
    main()
