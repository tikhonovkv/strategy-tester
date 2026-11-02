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
    keys_b = {(t["t"], t["sym"]) for t in base["trades"]}
    keys_f = {(t["t"], t["sym"]) for t in filt["trades"]}
    added = [t for t in filt["trades"] if (t["t"], t["sym"]) not in keys_b]
    removed = [t for t in base["trades"] if (t["t"], t["sym"]) not in keys_f]
    added.sort(key=lambda t: t["pnl_usd"], reverse=True)
    gap = filt["final"] - base["final"]
    add_sum = sum(t["pnl_usd"] for t in added)
    report = {
        "rules_final": base["final"],
        "filt_final": filt["final"],
        "equity_gap": round(gap, 2),
        "added_n": len(added),
        "added_pnl": round(add_sum, 2),
        "added_share_of_gap": round(add_sum / gap, 3) if gap else None,
        "removed_n": len(removed),
        "removed_pnl": round(sum(t["pnl_usd"] for t in removed), 2),
        "added": [
            {"time": t["time"], "sym": t["sym"], "pnl": round(t["pnl_usd"], 2), "reason": t["reason"]}
            for t in added
        ],
        "change_bot": False,
    }
    Path(__file__).resolve().parent.joinpath("_regime_vol_repl.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
