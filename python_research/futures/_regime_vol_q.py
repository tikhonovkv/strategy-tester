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
    a, b = M.ms(2026, 7, 22), M.ms(2026, 11, 2)
    def run(src):
        cands = L.with_hot(src)
        return sp.simulate_window([c for c in cands if a <= c["t"] < b])
    base, filt = run(rows), run(kept)
    by_b = {(t["t"], t["sym"]): t for t in base["trades"]}
    same_reason = 0
    diffs = []
    for t in filt["trades"]:
        h = by_b.get((t["t"], t["sym"]))
        if not h:
            continue
        if t["reason"] == h["reason"]:
            same_reason += 1
        diffs.append({
            "time": t["time"], "sym": t["sym"],
            "filt_pnl": round(t["pnl_usd"], 2),
            "rules_pnl": round(h["pnl_usd"], 2),
            "delta": round(t["pnl_usd"] - h["pnl_usd"], 2),
            "same_reason": t["reason"] == h["reason"],
            "filt_pct": t.get("raw_net_pct"),
            "rules_pct": h.get("raw_net_pct"),
        })
    diffs.sort(key=lambda r: abs(r["delta"]), reverse=True)
    gap = filt["final"] - base["final"]
    top = diffs[:5]
    report = {
        "rules_final": base["final"],
        "filt_final": filt["final"],
        "equity_gap": round(gap, 2),
        "shared_n": len(diffs),
        "same_reason_n": same_reason,
        "top5": top,
        "change_bot": False,
    }
    Path(__file__).resolve().parent.joinpath("_regime_vol_q.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
