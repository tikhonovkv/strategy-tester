# -*- coding: utf-8 -*-
"""Causal multi-timeframe regime filters. Local only."""
import json
from pathlib import Path
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def load():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    rows = []
    for e in entries:
        s = series[e["sym"]]
        i, side = e["i"], e["side"]
        if i < 80:
            continue
        ex = L.apply_exit(s, ohlc[e["sym"]], i, e["side"], close_pol, L.SLIP, False)
        if ex is None:
            continue
        px = s["px"]
        atr = max(float(s["atr1h"][i]), 0.002)
        h4 = side * (float(px[i]) / float(px[i - 4]) - 1.0) / atr
        d1 = side * (float(px[i]) / float(s["ema24"][i]) - 1.0) / atr
        d3 = side * (float(s["ema72"][i]) / float(s["ema72"][i - 24]) - 1.0) / atr
        bi = btc_map.get(e["t"])
        if bi is None or bi < 24:
            btc_ag = 0.0
        else:
            ba = max(float(btc["atr1h"][bi]), 0.002)
            btc_ag = side * (float(btc["px"][bi]) / float(btc["ema240"][bi]) - 1.0) / ba
        votes = sum(x > 0 for x in (h4, d1, d3))
        rows.append({
            "t": e["t"], "sym": e["sym"], "side": side, "h4": h4, "d1": d1, "d3": d3,
            "btc": btc_ag, "votes": votes, **ex,
        })
    return rows

def book(rows):
    cands = L.with_hot(rows)
    start, cut, end = M.ms(2024, 7, 1), M.ms(2026, 7, 22), M.ms(2026, 11, 2)
    def sim(a, b):
        res = sp.simulate_window([c for c in cands if a <= c["t"] < b])
        wins = [t for t in res["trades"] if t["pnl_usd"] > 0]
        wins.sort(key=lambda t: t["pnl_usd"], reverse=True)
        pos = sum(t["pnl_usd"] for t in wins) or 1.0
        top5 = sum(t["pnl_usd"] for t in wins[:5])
        return {
            "final": res["final"], "ret": res["ret"], "dd": res["dd"], "n": res["n"],
            "top5_share": round(top5 / pos, 3),
        }
    return {"pre": sim(start, cut), "quarter": sim(cut, end), "full": sim(start, end)}

def main():
    rows = load()
    print("rows", len(rows), flush=True)
    filters = {
        "rules": lambda r: True,
        "daily_agrees": lambda r: r["d1"] > 0,
        "4h_and_daily": lambda r: r["h4"] > 0 and r["d1"] > 0,
        "veto_if_daily_and_3d_against": lambda r: not (r["d1"] < 0 and r["d3"] < 0),
        "at_least_2_of_3": lambda r: r["votes"] >= 2,
        "all_3_plus_btc": lambda r: r["votes"] == 3 and r["btc"] > 0,
    }
    report = {}
    for name, fn in filters.items():
        part = [r for r in rows if fn(r)]
        report[name] = {"kept": len(part), **book(part)}
        print(name, report[name]["full"]["final"], "q", report[name]["quarter"]["ret"], "top5", report[name]["full"]["top5_share"], flush=True)
    out = Path(__file__).resolve().parent / "_regime_tf.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out, flush=True)

if __name__ == "__main__":
    main()
