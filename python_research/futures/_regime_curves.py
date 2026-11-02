# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def curve(rows):
    cands = L.with_hot(rows)
    start, end = M.ms(2024, 7, 1), M.ms(2026, 11, 2)
    res = sp.simulate_window([c for c in cands if start <= c["t"] < end])
    pts = [(ts, eq) for ts, eq in res["curve"] if ts >= start]
    n = 28
    if len(pts) <= n:
        picked = pts
    else:
        step = (len(pts) - 1) / (n - 1)
        picked = [pts[int(round(i * step))] for i in range(n)]
    return [{"t": sp.iso(ts)[:10], "eq": eq} for ts, eq in picked], res["final"]

def main():
    rows = C.with_rank()
    kept = [r for r in rows if r["rank"] <= 0.70]
    rules, rf = curve(rows)
    filt, ff = curve(kept)
    out = {"rules": rules, "skip70": filt, "rules_final": rf, "skip70_final": ff}
    Path(__file__).resolve().parent.joinpath("_regime_curves.json").write_text(
        json.dumps(out), encoding="utf-8"
    )
    print(rf, ff, len(rules), flush=True)

if __name__ == "__main__":
    main()
