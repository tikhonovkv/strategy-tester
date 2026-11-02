# -*- coding: utf-8 -*-
"""Walk-forward lock classifier with a money penalty on wrong calls.
A false lock that gives up later upside is weighted 5x the missed ATR.
A false hold that gives back profit is weighted 2x the giveback.
Local only.
"""
import json
from pathlib import Path
import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import _dyn_lock as D
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

SLIP = L.SLIP


def decision_rows(item):
    rows = item["rows"]
    atr = item["atr"]
    peak = 0.0
    out = []
    for k, (fav, adv, cls, o, ts, j) in enumerate(rows):
        peak = max(peak, cls)
        if cls < 1.0 * atr:
            continue
        fut = rows[k + 1:k + 25]
        if len(fut) < 3:
            continue
        later = fut[-1][2]
        missed = max(0.0, later - cls) / atr
        gave = max(0.0, cls - later) / atr
        y = 1 if gave > missed and gave >= 0.4 else 0
        if y == 0:
            w = 1.0 + 5.0 * missed
        else:
            w = 1.0 + 2.0 * gave
        x = D.bar_x(item["s"], item["i"], j, item["side"], atr, cls, peak, item["base"])
        out.append((x, y, w))
    return out


def fit_clf(rows):
    X = np.vstack([r[0] for r in rows])
    y = np.array([r[1] for r in rows])
    w = np.array([r[2] for r in rows], dtype=np.float64)
    if len(set(y.tolist())) < 2:
        return None
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    clf = MLPClassifier(
        hidden_layer_sizes=(16,), activation="relu", alpha=0.08,
        learning_rate_init=0.002, max_iter=200, early_stopping=True,
        validation_fraction=0.15, n_iter_no_change=10, random_state=0,
    )
    clf.fit(xs, y, sample_weight=w)
    return scaler, clf


def p_lock(model, x):
    scaler, clf = model
    proba = clf.predict_proba(scaler.transform(x.reshape(1, -1)))[0]
    classes = list(clf.classes_)
    if 1 not in classes:
        return 0.0
    return float(proba[classes.index(1)])


def play(item, model, thr, min_profit):
    s, side, atr = item["s"], item["side"], item["atr"]
    entry, entry_ts = item["entry"], item["t"]
    peak = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    locked = False
    exit_px, exit_ts = entry, entry_ts
    reason = "end_96h"
    for fav, adv, cls, o, ts, j in item["rows"]:
        min_move = min(min_move, adv)
        h = j - item["i"]
        if adv <= -2.2 * atr:
            px = L.fill_at(side, entry, -2.2 * atr, o, SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "hard_stop", peak), locked
        if h <= 4 and cls <= -0.8 * atr:
            px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "fast_bail", peak), locked
        if model is not None and cls >= min_profit * atr:
            x = D.bar_x(s, item["i"], j, side, atr, cls, max(peak, cls), item["base"])
            if p_lock(model, x) >= thr:
                px = float(s["px"][j]) * (1.0 - side * SLIP)
                raw = side * (px / entry - 1.0)
                return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "lock", max(peak, cls)), True
        be = 1.15 * atr if pyr else 0.4 * atr
        if peak >= 3.0 * atr and cls <= be:
            px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "breakeven", peak), locked
        if (not pyr) and cls >= 2.0 * atr:
            pyr = True
            pyr_move = 2.0 * atr
        peak = max(peak, cls)
        c = float(s["px"][j])
        if (side == 1 and c < float(s["ema240"][j])) or (side == -1 and c > float(s["ema240"][j])):
            px = c * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "ema10_exit", peak), locked
        exit_px, exit_ts = c * (1.0 - side * SLIP), ts
    raw = side * (exit_px / entry - 1.0)
    return M.finish(s, side, entry, entry_ts, exit_px, exit_ts, raw, 0, 0, exit_ts, pyr, pyr_move, min_move, atr, "end_96h", peak), locked


def choose(model, val_items):
    best, best_s = (0.7, 1.5), -1e18
    for thr in (0.55, 0.7, 0.85):
        for profit in (1.2, 2.0):
            score = 0.0
            for item in val_items:
                ex, _ = play(item, model, thr, profit)
                if ex:
                    score += ex["net"] + ex["pyr_net"]
            if score > best_s:
                best_s, best = score, (thr, profit)
    return best


def main():
    prepared = D.main.__wrapped__ if False else None
    # Rebuild the same prepared trades by calling the loader section via a small copy.
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    prepared = []
    for e in entries:
        s = series[e["sym"]]
        base, atr = M.features(s, btc, btc_map, e["i"], e["side"])
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * SLIP)
        rows = M.path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        hourly = L.apply_exit(s, ohlc[e["sym"]], e["i"], e["side"], close_pol, SLIP, False)
        if hourly is None:
            continue
        prepared.append({
            "s": s, "i": e["i"], "t": e["t"], "sym": e["sym"], "side": e["side"],
            "atr": atr, "entry": entry, "rows": rows, "base": base,
            "exit_ready": rows[-1][4], "hourly": hourly,
        })
    prepared.sort(key=lambda z: z["t"])
    print("prepared", len(prepared), flush=True)
    model = None
    params = (0.7, 1.5)
    next_refit = 0
    closed = []
    dyn, base = [], []
    locks = 0
    used = 0
    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 90:
                cut_i = int(len(ready) * 0.8)
                fit_items, val_items = ready[:cut_i], ready[cut_i:]
                rows = []
                for z in fit_items:
                    rows.extend(decision_rows(z))
                if len(rows) >= 200 and len(set(r[1] for r in rows)) > 1:
                    model = fit_clf(rows)
                    if model is not None:
                        params = choose(model, val_items[-30:])
                        next_refit = item["t"] + 60 * sp.DAY
                        print("refit", sp.iso(item["t"]), "decisions", len(rows), "params", params, flush=True)
        if model is not None:
            ex, did = play(item, model, params[0], params[1])
            used += 1
            locks += int(did)
        else:
            ex = dict(item["hourly"])
        if ex:
            dyn.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
        base.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]})
        closed.append(item)
    dyn = L.with_hot(dyn)
    base = L.with_hot(base)
    d_tbl, b_tbl = M.period_table(dyn), M.period_table(base)
    by_b = {r["period"]: r for r in b_tbl}
    wins = sum(1 for r in d_tbl if r["period"] in by_b and r["ret"] > by_b[r["period"]]["ret"])
    q_dyn = next((r["ret"] for r in d_tbl if r["period"] == "2026Q"), None)
    q_base = (by_b.get("2026Q") or {}).get("ret")
    report = {
        "penalty": "false lock that misses later upside weighted 5x missed ATR; false hold that gives back weighted 2x giveback",
        "used_model": used,
        "locks": locks,
        "params_last": {"thr": params[0], "min_profit_atr": params[1]},
        "penalty_model": d_tbl,
        "hourly_rules": b_tbl,
        "periods_won": wins,
        "q_not_worse": q_dyn is not None and q_base is not None and q_dyn >= q_base,
        "change_bot": False,
    }
    report["change_bot"] = wins >= 4 and report["q_not_worse"]
    out = Path(__file__).resolve().parent / "_penalty_train.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
