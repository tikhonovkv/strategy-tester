# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _regime_vol_cut as C
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def sim(rows, a, b):
    cands = L.with_hot(rows)
    res = sp.simulate_window([c for c in cands if a <= c["t"] < b])
    return {"ret": res["ret"], "final": res["final"], "dd": res["dd"], "n": res["n"]}

def main():
    rows = C.with_rank()
    kept = [r for r in rows if r["rank"] <= 0.70]
    periods = [
        ("2024H2", M.ms(2024, 7, 1), M.ms(2025, 1, 1)),
        ("2025H1", M.ms(2025, 1, 1), M.ms(2025, 7, 1)),
        ("2025H2", M.ms(2025, 7, 1), M.ms(2026, 1, 1)),
        ("2026H1", M.ms(2026, 1, 1), M.ms(2026, 7, 22)),
        ("2026Q", M.ms(2026, 7, 22), M.ms(2026, 11, 2)),
    ]
    report = []
    wins = 0
    for name, a, b in periods:
        rules, filt = sim(rows, a, b), sim(kept, a, b)
        win = filt["ret"] > rules["ret"]
        wins += int(win)
        report.append({"period": name, "rules": rules, "skip70": filt, "filt_better": win})
        print(name, "rules", rules["ret"], "skip70", filt["ret"], "n", filt["n"], "/", rules["n"], flush=True)
    out = {"wins": wins, "of": len(report), "rows": report, "change_bot": False}
    Path(__file__).resolve().parent.joinpath("_regime_vol_half.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wins", wins, flush=True)

if __name__ == "__main__":
    main()
