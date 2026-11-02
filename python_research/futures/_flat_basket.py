# -*- coding: utf-8 -*-
"""Flat $100-risk test of three basket features. No reinvestment, no slot cap."""
import json
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
import _mlp_stops as M
import _profit_lock as L

HARD = 2.2
RISK = 100.0


def carry_z(s, t_ms, side):
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    fi = int(np.searchsorted(f_ts, t_ms, side="right")) - 1
    if fi < 90:
        return 0.0
    hist = f_rate[fi - 89:fi + 1]
    fz = (float(f_rate[fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
    return float(-side * fz)


def aligned(s, i):
    if i < 24:
        return 0
    es = float(s["ema240"][i])
    slope = es / float(s["ema240"][i - 24]) - 1.0
    px = float(s["px"][i])
    if px > es and slope > 0:
        return 1
    if px < es and slope < 0:
        return -1
    return 0


def year_of(ts):
    return int(M.iso(ts)[:4]) if False else int(__import__("datetime").datetime.fromtimestamp(ts / 1000, __import__("datetime").timezone.utc).year)


def stats(pnls):
    if not pnls:
        return {"n": 0, "mean": None, "mean_ex5": None, "sum": 0.0}
    arr = np.array(pnls, dtype=float)
    order = np.argsort(arr)[::-1]
    rest = np.delete(arr, order[: min(5, len(arr))])
    return {
        "n": int(len(arr)),
        "mean": round(float(arr.mean()), 2),
        "mean_ex5": round(float(rest.mean()), 2) if len(rest) else None,
        "sum": round(float(arr.sum()), 2),
        "sum_ex5": round(float(rest.sum()), 2) if len(rest) else None,
    }


def main():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    alts = sp_alts()
    idx = {sym: {int(ts): i for i, ts in enumerate(series[sym]["close_ts"])} for sym in alts}
    trades = []
    for e in entries:
        s = series[e["sym"]]
        i, side = e["i"], e["side"]
        if i < 240:
            continue
        ex = L.apply_exit(s, ohlc[e["sym"]], i, side, close_pol, L.SLIP, False)
        if ex is None:
            continue
        atr = max(float(s["atr1h"][i]), 0.002)
        atr7 = float(np.mean(np.abs(s["ret1"][i - 167:i + 1])))
        comp = atr / max(atr7, 1e-8)
        t_ms = e["t"]
        breadth = 0
        carries = []
        for sym in alts:
            j = idx[sym].get(t_ms)
            if j is None:
                continue
            if aligned(series[sym], j) == side:
                breadth += 1
            carries.append(carry_z(series[sym], t_ms, side))
        basket = float(np.mean(carries)) if carries else 0.0
        flags = {
            "breadth": int(breadth >= 5),
            "compress": int(comp < 1.0),
            "funding": int(basket > 0.0),
        }
        score = sum(flags.values())
        pnl = RISK * float(ex["net"]) / (HARD * atr)
        trades.append({
            "t": t_ms, "year": year_of(t_ms), "sym": e["sym"], "pnl": pnl,
            "score": score, **flags, "breadth_n": breadth, "comp": comp, "basket": basket,
        })
    years = [2022, 2023, 2024, 2025, 2026]
    masks = {
        "all": lambda t: True,
        "breadth>=5": lambda t: t["breadth"] == 1,
        "compressed": lambda t: t["compress"] == 1,
        "basket_funding>0": lambda t: t["funding"] == 1,
        "score>=2": lambda t: t["score"] >= 2,
        "score==3": lambda t: t["score"] == 3,
    }
    table = {}
    for name, fn in masks.items():
        table[name] = {}
        for y in years:
            table[name][str(y)] = stats([t["pnl"] for t in trades if t["year"] == y and fn(t)])
    # Walk-forward logistic: 3 raw features, train on past years only.
    wf = []
    Xall = np.array([[t["breadth_n"], t["comp"], t["basket"]] for t in trades], dtype=float)
    yall = np.array([1 if t["pnl"] > 0 else 0 for t in trades])
    yidx = np.array([t["year"] for t in trades])
    pnl = np.array([t["pnl"] for t in trades])
    for test_year in (2023, 2024, 2025, 2026):
        tr = yidx < test_year
        te = yidx == test_year
        if tr.sum() < 30 or te.sum() < 8 or len(set(yall[tr])) < 2:
            continue
        clf = LogisticRegression(C=0.2, max_iter=200)
        clf.fit(Xall[tr], yall[tr])
        pred = clf.predict(Xall[te])
        kept = pnl[te][pred == 1]
        base = pnl[te]
        wf.append({
            "year": test_year,
            "coef": [round(float(c), 3) for c in clf.coef_[0]],
            "all": stats(base.tolist()),
            "model_keeps": stats(kept.tolist()),
            "kept_n": int((pred == 1).sum()),
        })
    signs = {}
    for name in masks:
        if name == "all":
            continue
        signs[name] = []
        for y in years:
            a = table["all"][str(y)]["mean_ex5"]
            b = table[name][str(y)]["mean_ex5"]
            if a is None or b is None:
                continue
            signs[name].append({"year": y, "delta_ex5": round(b - a, 2)})
    report = {"n": len(trades), "table": table, "walk_forward": wf, "delta_ex5": signs}
    Path(__file__).resolve().parent.joinpath("_flat_basket.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("n", len(trades), flush=True)
    for name in masks:
        line = []
        for y in years:
            st = table[name][str(y)]
            line.append(f"{y}:{st['mean_ex5']}/{st['n']}")
        print(name, " ".join(line), flush=True)
    print("WF", json.dumps(wf, ensure_ascii=False), flush=True)


def sp_alts():
    import schwartz_production as sp
    return sp.ALTS_7


if __name__ == "__main__":
    main()
