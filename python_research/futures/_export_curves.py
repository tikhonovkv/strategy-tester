# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _penalty_grid as G
import _penalty_study as S
import _penalty_train as P
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def blend(lock, hourly, frac):
    out = dict(hourly)
    out["net"] = frac * lock["net"] + (1.0 - frac) * hourly["net"]
    out["pyr_net"] = frac * lock["pyr_net"] + (1.0 - frac) * hourly["pyr_net"]
    out["reason"] = "partial"
    return out

def choose_for(model, val_items, frac):
    best, best_s = (0.7, 1.2), -1e18
    for thr in (0.6, 0.75, 0.9):
        for profit in (1.2, 2.0):
            score = 0.0
            for item in val_items:
                ex, did = S.play(item, model, thr, profit, 0.0, 0)
                if did and ex is not None and frac < 1:
                    ex = blend(ex, item["hourly"], frac)
                if ex:
                    score += ex["net"] + ex["pyr_net"]
            if score > best_s:
                best_s, best = score, (thr, profit)
    return best

def walk(prepared, frac):
    model = None
    params = (0.7, 1.2)
    next_refit = 0
    closed = []
    rows = []
    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 90:
                cut_i = int(len(ready) * 0.8)
                fit_items, val_items = ready[:cut_i], ready[cut_i:]
                dec = []
                for z in fit_items:
                    dec.extend(G.decisions(z, 12, 2))
                if len(dec) >= 200 and len(set(r[1] for r in dec)) > 1:
                    fitted = P.fit_clf(dec)
                    if fitted is not None:
                        model = fitted
                        params = choose_for(model, val_items[-20:], frac)
                        next_refit = item["t"] + 90 * sp.DAY
        lock = None
        if model is not None:
            lock, did = S.play(item, model, params[0], params[1], 0.0, 0)
            if not did:
                lock = None
        rows.append((item, lock))
        closed.append(item)
    return rows

def book(rows, frac):
    cands = []
    for item, lock in rows:
        ex = item["hourly"] if lock is None or frac <= 0 else (lock if frac >= 1 else blend(lock, item["hourly"], frac))
        cands.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
    cands = L.with_hot(cands)
    start, end = M.ms(2024, 7, 1), M.ms(2026, 11, 2)
    res = sp.simulate_window([c for c in cands if start <= c["t"] < end])
    return res["curve"]

def downsample(curve, n=28):
    pts = [(ts, eq) for ts, eq in curve if ts >= M.ms(2024, 7, 1)]
    if len(pts) <= n:
        return pts
    step = (len(pts) - 1) / (n - 1)
    out = []
    for i in range(n):
        out.append(pts[int(round(i * step))])
    return out

def main():
    prepared = G.load_prepared()
    print("prepared", len(prepared), flush=True)
    series = {}
    for name, frac in (("rules", 0.0), ("lock100", 1.0), ("lock25", 0.25), ("lock10", 0.10), ("lock5", 0.05)):
        print("walk", name, flush=True)
        if frac <= 0:
            rows = [(item, None) for item in prepared]
        else:
            rows = walk(prepared, frac)
        curve = downsample(book(rows, frac))
        series[name] = [{"t": sp.iso(ts)[:10], "eq": eq} for ts, eq in curve]
        print(name, series[name][-1], flush=True)
    out = Path(__file__).resolve().parent / "_equity_curves.json"
    out.write_text(json.dumps(series), encoding="utf-8")
    print("wrote", out, flush=True)

if __name__ == "__main__":
    main()
