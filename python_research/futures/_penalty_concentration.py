# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _penalty_study as S
import _penalty_train as P
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def main():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    prepared = []
    for e in entries:
        s = series[e["sym"]]
        base, atr = M.features(s, btc, btc_map, e["i"], e["side"])
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * S.SLIP)
        rows = M.path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        hourly = L.apply_exit(s, ohlc[e["sym"]], e["i"], e["side"], close_pol, S.SLIP, False)
        if hourly is None:
            continue
        prepared.append({
            "s": s, "i": e["i"], "t": e["t"], "sym": e["sym"], "side": e["side"],
            "atr": atr, "entry": entry, "rows": rows, "base": base,
            "exit_ready": rows[-1][4], "hourly": hourly,
        })
    prepared.sort(key=lambda z: z["t"])
    model = None
    params = (0.8, 1.5, 1.0)
    next_refit = 0
    closed, chosen, forced, base, flags = [], [], [], [], []
    force_keys = {("2025-10-10 15:00", "SOLUSDT"), ("2025-09-21 21:00", "ETHUSDT")}
    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 90:
                cut_i = int(len(ready) * 0.8)
                fit_items, val_items = ready[:cut_i], ready[cut_i:]
                rows = []
                for z in fit_items:
                    rows.extend(P.decision_rows(z))
                if len(rows) >= 200 and len(set(r[1] for r in rows)) > 1:
                    fitted = P.fit_clf(rows)
                    if fitted is not None:
                        model = fitted
                        params = S.choose(model, val_items[-25:])
                        next_refit = item["t"] + 60 * sp.DAY
        did = False
        if model is not None:
            ex, did = S.play(item, model, params[0], params[1], params[2])
        else:
            ex = dict(item["hourly"])
        hourly_ex = {"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]}
        if ex:
            chosen.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
            flags.append((item["t"], item["sym"], bool(did), ex.get("reason")))
            key = (sp.iso(item["t"]), item["sym"])
            forced.append(dict(hourly_ex) if key in force_keys else {"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
        base.append(hourly_ex)
        closed.append(item)
    chosen, forced, base = L.with_hot(chosen), L.with_hot(forced), L.with_hot(base)
    a, b = M.ms(2024, 7, 1), M.ms(2026, 11, 2)
    h2a, h2b = M.ms(2025, 7, 1), M.ms(2026, 1, 1)
    book_c = sp.simulate_window([c for c in chosen if a <= c["t"] < b])
    book_f = sp.simulate_window([c for c in forced if a <= c["t"] < b])
    book_h = sp.simulate_window([c for c in base if a <= c["t"] < b])
    by_h = {(t["t"], t["sym"]): t for t in book_h["trades"]}
    locked_keys = {(t, sym) for t, sym, did, reason in flags if did}
    skip = {("2025-10-10 15:00", "SOLUSDT"), ("2025-09-21 21:00", "ETHUSDT")}
    rows = []
    for t in book_c["trades"]:
        key = (t["t"], t["sym"])
        if key not in locked_keys:
            continue
        h = by_h.get(key)
        if not h:
            continue
        rows.append({
            "time": t["time"], "sym": t["sym"], "side": t["side"],
            "lock_pnl": t["pnl_usd"], "hourly_pnl": h["pnl_usd"],
            "delta": round(t["pnl_usd"] - h["pnl_usd"], 2),
            "lock_reason": t["reason"], "hourly_reason": h["reason"],
        })
    rows.sort(key=lambda r: r["delta"], reverse=True)
    rest = [r for r in rows if (r["time"], r["sym"]) not in skip]
    rest_sum = round(sum(r["delta"] for r in rest), 2)
    top10 = rest[:10]
    top10_sum = round(sum(r["delta"] for r in top10), 2)
    gap = book_c["final"] - book_h["final"]
    remain_gap = book_f["final"] - book_h["final"]
    report = {
        "book_final_lock": book_c["final"],
        "book_final_without_two": book_f["final"],
        "book_final_hourly": book_h["final"],
        "equity_gap": round(gap, 2),
        "remaining_gap_without_two": round(remain_gap, 2),
        "locks_matched": len(rows),
        "rest_locks": len(rest),
        "rest_delta_sum": rest_sum,
        "top10_delta_sum": top10_sum,
        "top10_share_of_rest_delta": round(top10_sum / rest_sum, 3) if rest_sum else None,
        "top10": top10,
        "concentrated": bool(rest_sum and abs(top10_sum) >= 0.6 * abs(rest_sum)),
        "change_bot": False,
    }
    out = Path(__file__).resolve().parent / "_penalty_concentration.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
