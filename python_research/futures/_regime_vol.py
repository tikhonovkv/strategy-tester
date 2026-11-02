# -*- coding: utf-8 -*-
import json
from pathlib import Path
import numpy as np
import _regime_tf as R
import _mlp_stops as M
import _profit_lock as L

def load():
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
        if rank < 1.0 / 3.0:
            bucket = "low"
        elif rank < 2.0 / 3.0:
            bucket = "mid"
        else:
            bucket = "high"
        rows.append({"t": e["t"], "sym": e["sym"], "side": e["side"], "bucket": bucket, **ex})
    return rows

def main():
    rows = load()
    print("rows", len(rows), {b: sum(r["bucket"] == b for r in rows) for b in ("low", "mid", "high")}, flush=True)
    report = {"all": R.book(rows)}
    for b in ("low", "mid", "high"):
        report[b] = R.book([r for r in rows if r["bucket"] == b])
    report["skip_high"] = R.book([r for r in rows if r["bucket"] != "high"])
    for name, row in report.items():
        print(name, "full", row["full"]["final"], "q", row["quarter"]["ret"], "top5", row["full"]["top5_share"], "n", row["full"]["n"], flush=True)
    out = Path(__file__).resolve().parent / "_regime_vol.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
