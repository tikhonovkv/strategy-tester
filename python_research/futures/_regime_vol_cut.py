# -*- coding: utf-8 -*-
import json
from pathlib import Path
import numpy as np
import _regime_tf as R
import _regime_vol as V
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def with_rank():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    rows = []
    for e in entries:
        s = series[e["sym"]]
        i = e["i"]
        if i < 720:
            continue
        ex = L.apply_exit(s, ohlc[e["sym"]], i, e["side"], close_pol, L.SLIP, False)
        if ex is None:
            continue
        window = s["atr1h"][i - 719:i + 1]
        cur = float(s["atr1h"][i])
        rank = float(np.mean(window <= cur))
        rows.append({"t": e["t"], "sym": e["sym"], "side": e["side"], "rank": rank, **ex})
    return rows

def quarter_gap(rules_rows, kept_rows):
    cut, end = M.ms(2026, 7, 22), M.ms(2026, 11, 2)
    def trades(rows):
        cands = L.with_hot(rows)
        res = sp.simulate_window([c for c in cands if cut <= c["t"] < end])
        return res
    a, b = trades(rules_rows), trades(kept_rows)
    by_b = {(t["t"], t["sym"]): t for t in b["trades"]}
    dropped = []
    for t in a["trades"]:
        if (t["t"], t["sym"]) not in by_b:
            dropped.append({"time": t["time"], "sym": t["sym"], "pnl": t["pnl_usd"]})
    dropped.sort(key=lambda r: r["pnl"])
    worst5 = dropped[:5]
    return {
        "rules_final": a["final"],
        "kept_final": b["final"],
        "dropped_n": len(dropped),
        "dropped_pnl_sum": round(sum(r["pnl"] for r in dropped), 2),
        "worst5_pnl_sum": round(sum(r["pnl"] for r in worst5), 2),
        "worst5": worst5,
    }

def main():
    rows = with_rank()
    rules = R.book(rows)
    report = {"rules": rules}
    gaps = {}
    for cut in (0.60, 0.70, 0.80):
        kept = [r for r in rows if r["rank"] <= cut]
        report[str(cut)] = R.book(kept)
        gaps[str(cut)] = quarter_gap(rows, kept)
        row = report[str(cut)]
        print(cut, "full", row["full"]["final"], "q", row["quarter"]["ret"], "top5", row["full"]["top5_share"], "worst5", gaps[str(cut)]["worst5_pnl_sum"], "dropped", gaps[str(cut)]["dropped_pnl_sum"], flush=True)
    report["quarter_dropped"] = gaps
    out = Path(__file__).resolve().parent / "_regime_vol_cut.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
