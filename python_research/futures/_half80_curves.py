# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _vol70_halfsize as H
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def curve(rows):
    cands = L.with_hot(rows)
    start, end = M.ms(2022, 1, 1), M.ms(2026, 11, 2)
    res = sp.simulate_window([c for c in cands if start <= c["t"] < end])
    pts = [(ts, eq) for ts, eq in res["curve"] if start <= ts < end]
    n = 32
    step = (len(pts) - 1) / (n - 1)
    picked = [pts[int(round(i * step))] for i in range(n)]
    return [{"t": sp.iso(ts)[:7], "eq": round(eq)} for ts, eq in picked]

def main():
    rows = C.with_rank()
    out = {"rules": curve(rows), "half80": curve(H.half(rows))}
    Path(__file__).resolve().parent.joinpath("_half80_curves.json").write_text(json.dumps(out), encoding="utf-8")
    print(out["rules"][-1], out["half80"][-1], flush=True)

if __name__ == "__main__":
    main()
