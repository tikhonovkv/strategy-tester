# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def main():
    rows = C.with_rank()
    kept = [r for r in rows if r["rank"] <= 0.70]
    a, b = M.ms(2025, 7, 1), M.ms(2026, 1, 1)
    def run(src):
        cands = L.with_hot(src)
        return sp.simulate_window([c for c in cands if a <= c["t"] < b])
    base, filt = run(rows), run(kept)
    by_b = {(t["t"], t["sym"]): t for t in base["trades"]}
    diffs = []
    for t in filt["trades"]:
        h = by_b.get((t["t"], t["sym"]))
        if not h:
            continue
        diffs.append({
            "time": t["time"], "sym": t["sym"],
            "filt_pnl": round(t["pnl_usd"], 2),
            "rules_pnl": round(h["pnl_usd"], 2),
            "delta": round(t["pnl_usd"] - h["pnl_usd"], 2),
        })
    diffs.sort(key=lambda r: r["delta"], reverse=True)
    gap = filt["final"] - base["final"]
    top = diffs[:5]
    top_sum = round(sum(r["delta"] for r in top), 2)
    all_sum = round(sum(r["delta"] for r in diffs), 2)
    report = {
        "equity_gap": round(gap, 2),
        "shared_n": len(diffs),
        "shared_delta_sum": all_sum,
        "top5_delta_sum": top_sum,
        "top5_share_of_gap": round(top_sum / gap, 3) if gap else None,
        "top5": top,
        "change_bot": False,
    }
    Path(__file__).resolve().parent.joinpath("_regime_vol_shared.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
