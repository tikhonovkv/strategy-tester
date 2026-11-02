# -*- coding: utf-8 -*-
"""Walk-forward runner classifier. Local only."""
import json
from pathlib import Path
import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

SLIP = L.SLIP


def is_runner(rows, atr):
    sl = -2.2 * atr
    tp = 6.0 * atr
    for fav, adv, cls, o, ts, j in rows:
        if adv <= sl:
            return 0
        if fav >= tp:
            return 1
    return 0


def scratch(s, rows, side, entry, entry_ts, atr):
    stop = -0.8 * atr
    take = 1.0 * atr
    limit_ts = entry_ts + 24 * sp.HOUR
    peak = 0.0
    min_move = 0.0
    if not rows:
        return None
    for fav, adv, cls, o, ts, j in rows:
        if ts > limit_ts:
            break
        min_move = min(min_move, adv)
        if adv <= stop:
            px = L.fill_at(side, entry, stop, o, SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, False, 0.0, min_move, atr, "scratch_stop", peak)
        if fav >= take:
            px = M.tp_fill(side, entry, take, o)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, False, 0.0, min_move, atr, "scratch_take", max(peak, fav))
        peak = max(peak, fav)
    # Neither level inside 24h: leave at the 24h close.
    last = None
    for row in rows:
        if row[4] <= limit_ts:
            last = row
    if last is None:
        return None
    fav, adv, cls, o, ts, j = last
    c = float(s["px"][j])
    px = c * (1.0 - side * SLIP)
    raw = side * (px / entry - 1.0)
    return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, False, 0.0, min_move, atr, "scratch_24h", peak)


def fit_cls(X, y):
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    clf = MLPClassifier(
        hidden_layer_sizes=(12,),
        activation="relu",
        alpha=0.05,
        learning_rate_init=0.002,
        max_iter=250,
        early_stopping=True,
        validation_fraction=0.2,
        n_iter_no_change=12,
        random_state=0,
    )
    clf.fit(xs, y)
    return scaler, clf


def main():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    prepared = []
    for e in entries:
        s = series[e["sym"]]
        x, atr = M.features(s, btc, btc_map, e["i"], e["side"])
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * SLIP)
        rows = M.path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        hourly = L.apply_exit(s, ohlc[e["sym"]], e["i"], e["side"], close_pol, SLIP, False)
        if hourly is None:
            continue
        prepared.append({
            "e": e, "s": s, "x": x, "atr": atr, "entry": entry, "rows": rows,
            "t": e["t"], "exit_ready": rows[-1][4], "y": is_runner(rows, atr),
            "side": e["side"], "sym": e["sym"], "hourly": hourly,
        })
    prepared.sort(key=lambda z: z["t"])
    print("prepared", len(prepared), "runners", sum(z["y"] for z in prepared), flush=True)

    model = None
    threshold = 0.5
    next_refit = 0
    closed = []
    cands = []
    base = []
    used = 0
    called_runner = 0

    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 80 and len(set(z["y"] for z in ready)) > 1:
                cut = ready[int(len(ready) * 0.75)]["t"]
                fit_rows = [z for z in ready if z["t"] < cut]
                val_rows = [z for z in ready if z["t"] >= cut]
                if len(fit_rows) >= 60 and len(set(z["y"] for z in fit_rows)) > 1 and len(val_rows) >= 15:
                    X = np.vstack([z["x"] for z in fit_rows])
                    y = np.array([z["y"] for z in fit_rows])
                    model = fit_cls(X, y)
                    best_t, best_s = 0.5, -1e18
                    for thr in (0.35, 0.5, 0.65):
                        score = 0.0
                        for z in val_rows:
                            p = float(model[1].predict_proba(model[0].transform(z["x"].reshape(1, -1)))[0, 1])
                            if p >= thr:
                                score += z["hourly"]["net"] + z["hourly"]["pyr_net"]
                            else:
                                ex = scratch(z["s"], z["rows"], z["side"], z["entry"], z["t"], z["atr"])
                                if ex:
                                    score += ex["net"] + ex["pyr_net"]
                        if score > best_s:
                            best_s, best_t = score, thr
                    threshold = best_t
                    next_refit = item["t"] + 45 * sp.DAY
                    print("refit", sp.iso(item["t"]), "n", len(fit_rows), "thr", threshold, flush=True)
        runner = True
        if model is not None:
            p = float(model[1].predict_proba(model[0].transform(item["x"].reshape(1, -1)))[0, 1])
            runner = p >= 0.25
            used += 1
        if runner:
            called_runner += 1
            ex = dict(item["hourly"])
            ex["reason"] = "runner_" + str(ex.get("reason"))
        else:
            ex = scratch(item["s"], item["rows"], item["side"], item["entry"], item["t"], item["atr"])
        if ex:
            cands.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
        base.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]})
        closed.append(item)

    cands = L.with_hot(cands)
    base = L.with_hot(base)
    cls_tbl = M.period_table(cands)
    base_tbl = M.period_table(base)
    wins = 0
    compared = 0
    by_b = {r["period"]: r for r in base_tbl}
    for r in cls_tbl:
        b = by_b.get(r["period"])
        if not b:
            continue
        compared += 1
        if r["ret"] > b["ret"]:
            wins += 1
    report = {
        "used_model": used,
        "called_runner": called_runner,
        "true_runners": int(sum(z["y"] for z in prepared)),
        "classifier": cls_tbl,
        "hourly_rules": base_tbl,
        "periods_won": wins,
        "periods_compared": compared,
        "q_ok": (by_b.get("2026Q") or {}).get("ret", 0) <= next((r["ret"] for r in cls_tbl if r["period"] == "2026Q"), -1e9),
        "change_bot": wins >= 4 and (by_b.get("2026Q") or {}).get("ret", 0) <= next((r["ret"] for r in cls_tbl if r["period"] == "2026Q"), -1e9),
    }
    out = Path(__file__).resolve().parent / "_runner_cls_strict.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
