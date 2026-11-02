# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def gap(rows, kept, a, b):
    def run(src):
        cands = L.with_hot(src)
        return sp.simulate_window([c for c in cands if a <= c["t"] < b])
    base, filt = run(rows), run(kept)
    by_f = {(t["t"], t["sym"]): t for t in filt["trades"]}
    dropped = []
    for t in base["trades"]:
        if (t["t"], t["sym"]) not in by_f:
            dropped.append({"time": t["time"], "sym": t["sym"], "pnl": round(t["pnl_usd"], 2)})
    dropped.sort(key=lambda r: r["pnl"])
    worst = dropped[:5]
    dsum = sum(r["pnl"] for r in dropped) or 0.0
    wsum = sum(r["pnl"] for r in worst)
    return {
        "rules_ret": base["ret"], "filt_ret": filt["ret"],
        "rules_final": base["final"], "filt_final": filt["final"],
        "dropped_n": len(dropped),
        "dropped_pnl": round(dsum, 2),
        "worst5_pnl": round(wsum, 2),
        "worst5_share_of_dropped": round(wsum / dsum, 3) if dsum else None,
        "worst5": worst,
        "mostly_five": bool(dsum and abs(wsum) >= 0.6 * abs(dsum)),
    }

def main():
    rows = C.with_rank()
    kept = [r for r in rows if r["rank"] <= 0.70]
    windows = {
        "2025H2": (M.ms(2025, 7, 1), M.ms(2026, 1, 1)),
        "2026Q": (M.ms(2026, 7, 22), M.ms(2026, 11, 2)),
    }
    report = {}
    for name, (a, b) in windows.items():
        report[name] = gap(rows, kept, a, b)
        print(name, report[name]["dropped_n"], report[name]["worst5_share_of_dropped"], report[name]["mostly_five"], flush=True)
    Path(__file__).resolve().parent.joinpath("_regime_vol_conc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
