# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def main():
    rows = C.with_rank()
    kept = [dict(r) for r in rows if r["rank"] <= 0.70]
    a, b = M.ms(2025, 7, 1), M.ms(2026, 1, 1)
    base_c = L.with_hot(rows)
    filt_c = L.with_hot(kept)
    base = sp.simulate_window([c for c in base_c if a <= c["t"] < b])
    plain = sp.simulate_window([c for c in filt_c if a <= c["t"] < b])
    by_b = {(t["t"], t["sym"]): t for t in base["trades"]}
    force = set()
    for t in plain["trades"]:
        if t["sym"] == "SUIUSDT" and t["time"] in ("2025-10-10 15:00", "2025-11-02 09:00"):
            force.add((t["t"], t["sym"]))
    patched = []
    for c in filt_c:
        key = (c["t"], c["sym"])
        if key in force and key in by_b:
            src = by_b[key]
            c = dict(c)
            # Match the rules trade return rate so dollar pnl scales only with equity.
            c["net"] = src["raw_net_pct"] / 100.0
            c["pyr_net"] = 0.0
        patched.append(c)
    forced = sp.simulate_window([c for c in patched if a <= c["t"] < b])
    report = {
        "rules_final": base["final"],
        "filter_final": plain["final"],
        "forced_final": forced["final"],
        "gap_left": round(forced["final"] - base["final"], 2),
        "forced_keys": len(force),
        "change_bot": False,
    }
    Path(__file__).resolve().parent.joinpath("_regime_vol_sui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
