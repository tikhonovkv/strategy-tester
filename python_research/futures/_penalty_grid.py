# -*- coding: utf-8 -*-
"""Grid the money-penalty weights. Pick the best book on entries before 2026-07-22.
The quarter after that is only reported. Local only.
"""
import json
from pathlib import Path
import _penalty_study as S
import _penalty_train as P
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def decisions(item, lock_w, hold_w):
    rows = item["rows"]
    atr = item["atr"]
    peak = 0.0
    out = []
    for k, (fav, adv, cls, o, ts, j) in enumerate(rows):
        peak = max(peak, cls)
        if cls < 1.0 * atr:
            continue
        fut = rows[k + 1:k + 25]
        if len(fut) < 3:
            continue
        later = fut[-1][2]
        missed = max(0.0, later - cls) / atr
        gave = max(0.0, cls - later) / atr
        y = 1 if gave > missed and gave >= 0.4 else 0
        w = 1.0 + (hold_w * gave if y == 1 else lock_w * missed)
        x = __import__("_dyn_lock", fromlist=["bar_x"]).bar_x(item["s"], item["i"], j, item["side"], atr, cls, peak, item["base"])
        out.append((x, y, w))
    return out

def blend25(lock, hourly):
    out = dict(hourly)
    out["net"] = 0.05 * lock["net"] + 0.95 * hourly["net"]
    out["pyr_net"] = 0.05 * lock["pyr_net"] + 0.95 * hourly["pyr_net"]
    out["reason"] = "partial5"
    return out

def choose(model, val_items):
    best, best_s = (0.7, 1.2), -1e18
    for thr in (0.6, 0.75, 0.9):
        for profit in (1.2, 2.0):
            score = 0.0
            for item in val_items:
                ex, did = S.play(item, model, thr, profit, 0.0, 0)
                if did and ex is not None:
                    ex = blend25(ex, item["hourly"])
                if ex:
                    score += ex["net"] + ex["pyr_net"]
            if score > best_s:
                best_s, best = score, (thr, profit)
    return best

def load_prepared():
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
    return prepared

def run_one(prepared, lock_w, hold_w):
    model = None
    params = (0.7, 1.2)
    next_refit = 0
    closed, chosen, base = [], [], []
    locks = 0
    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 90:
                cut_i = int(len(ready) * 0.8)
                fit_items, val_items = ready[:cut_i], ready[cut_i:]
                rows = []
                for z in fit_items:
                    rows.extend(decisions(z, lock_w, hold_w))
                if len(rows) >= 200 and len(set(r[1] for r in rows)) > 1:
                    fitted = P.fit_clf(rows)
                    if fitted is not None:
                        model = fitted
                        params = choose(model, val_items[-20:])
                        next_refit = item["t"] + 90 * sp.DAY
        if model is not None:
            ex, did = S.play(item, model, params[0], params[1], 0.0, 0)
            if did and ex is not None:
                ex = blend25(ex, item["hourly"])
            locks += int(did)
        else:
            ex = dict(item["hourly"])
        if ex:
            chosen.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
        base.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]})
        closed.append(item)
    chosen, base = L.with_hot(chosen), L.with_hot(base)
    start, cut, end = M.ms(2024, 7, 1), M.ms(2026, 7, 22), M.ms(2026, 11, 2)
    pre = sp.simulate_window([c for c in chosen if start <= c["t"] < cut])
    q = sp.simulate_window([c for c in chosen if cut <= c["t"] < end])
    full = sp.simulate_window([c for c in chosen if start <= c["t"] < end])
    return {
        "lock_w": lock_w, "hold_w": hold_w, "locks": locks, "last_params": {"thr": params[0], "min_profit": params[1]},
        "before_quarter": {"final": pre["final"], "ret": pre["ret"], "dd": pre["dd"], "n": pre["n"]},
        "quarter": {"final": q["final"], "ret": q["ret"], "dd": q["dd"], "n": q["n"]},
        "full_book": {"final": full["final"], "ret": full["ret"], "dd": full["dd"], "n": full["n"]},
    }

def main():
    prepared = load_prepared()
    print("prepared", len(prepared), flush=True)
    base = [c for c in prepared]
    # hourly reference
    hourly = L.with_hot([{"t": z["t"], "sym": z["sym"], "side": z["side"], **z["hourly"]} for z in prepared])
    start, cut, end = M.ms(2024, 7, 1), M.ms(2026, 7, 22), M.ms(2026, 11, 2)
    href = {
        "before_quarter": sp.simulate_window([c for c in hourly if start <= c["t"] < cut]),
        "quarter": sp.simulate_window([c for c in hourly if cut <= c["t"] < end]),
        "full_book": sp.simulate_window([c for c in hourly if start <= c["t"] < end]),
    }
    hourly_ref = {k: {"final": v["final"], "ret": v["ret"], "dd": v["dd"], "n": v["n"]} for k, v in href.items()}
    grid = []
    for lock_w, hold_w in ((12, 2),):
        row = run_one(prepared, lock_w, hold_w)
        grid.append(row)
        print(lock_w, hold_w, "pre", row["before_quarter"]["final"], "q", row["quarter"]["ret"], "full", row["full_book"]["final"], flush=True)
    best = max(grid, key=lambda r: r["before_quarter"]["final"])
    report = {"hourly": hourly_ref, "grid": grid, "chosen_by_pre_quarter_final": {"lock_w": best["lock_w"], "hold_w": best["hold_w"]}, "change_bot": False}
    q_ok = best["quarter"]["ret"] >= hourly_ref["quarter"]["ret"]
    pre_ok = best["before_quarter"]["final"] > hourly_ref["before_quarter"]["final"]
    report["change_bot"] = bool(q_ok and pre_ok)
    out = Path(__file__).resolve().parent / "_penalty_p5.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"chosen": report["chosen_by_pre_quarter_final"], "change_bot": report["change_bot"], "hourly_q": hourly_ref["quarter"]["ret"]}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
