# -*- coding: utf-8 -*-
"""Same penalty-trained lock model. Two uses:
val-chosen threshold, and a frozen rule that locks only after price
has already fallen back from the peak. Local only.
"""
import json
from pathlib import Path
import _penalty_train as P
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

SLIP = L.SLIP


def play(item, model, thr, min_profit, giveback, min_hold=0):
    s, side, atr = item["s"], item["side"], item["atr"]
    entry, entry_ts = item["entry"], item["t"]
    peak = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    locked = False
    exit_px, exit_ts = entry, entry_ts
    for fav, adv, cls, o, ts, j in item["rows"]:
        min_move = min(min_move, adv)
        h = j - item["i"]
        prior = peak
        if adv <= -2.2 * atr:
            px = L.fill_at(side, entry, -2.2 * atr, o, SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "hard_stop", peak), locked
        if h <= 4 and cls <= -0.8 * atr:
            px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "fast_bail", peak), locked
        fell = (prior - cls) >= giveback * atr
        if model is not None and h >= min_hold and cls >= min_profit * atr and fell:
            x = __import__("_dyn_lock", fromlist=["bar_x"]).bar_x(
                s, item["i"], j, side, atr, cls, max(prior, cls), item["base"]
            )
            if P.p_lock(model, x) >= thr:
                px = float(s["px"][j]) * (1.0 - side * SLIP)
                raw = side * (px / entry - 1.0)
                return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "lock", max(prior, cls)), True
        be = 1.15 * atr if pyr else 0.4 * atr
        if prior >= 3.0 * atr and cls <= be:
            px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "breakeven", prior), locked
        if (not pyr) and cls >= 2.0 * atr:
            pyr = True
            pyr_move = 2.0 * atr
        peak = max(peak, cls)
        c = float(s["px"][j])
        if (side == 1 and c < float(s["ema240"][j])) or (side == -1 and c > float(s["ema240"][j])):
            px = c * (1.0 - side * SLIP)
            raw = side * (px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "ema10_exit", peak), locked
        exit_px, exit_ts = c * (1.0 - side * SLIP), ts
    raw = side * (exit_px / entry - 1.0)
    return M.finish(s, side, entry, entry_ts, exit_px, exit_ts, raw, 0, 0, exit_ts, pyr, pyr_move, min_move, atr, "end_96h", peak), locked


def choose(model, val_items):
    best, best_s = (0.8, 1.5, 1.0), -1e18
    for thr in (0.7, 0.85):
        for profit in (1.2, 2.0):
            for gb in (0.0, 0.8, 1.5):
                score = 0.0
                nlock = 0
                for item in val_items:
                    ex, did = play(item, model, thr, profit, gb)
                    nlock += int(did)
                    if ex:
                        score += ex["net"] + ex["pyr_net"]
                if nlock > max(3, int(0.35 * len(val_items))):
                    score -= 0.5
                if score > best_s:
                    best_s, best = score, (thr, profit, gb)
    return best


def pack(prepared, mode):
    # mode is unused; outcomes already on items
    cands = []
    for item in prepared:
        ex = item.get("out")
        if ex:
            cands.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
    return L.with_hot(cands)


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
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * SLIP)
        rows = M.path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        hourly = L.apply_exit(s, ohlc[e["sym"]], e["i"], e["side"], close_pol, SLIP, False)
        if hourly is None:
            continue
        prepared.append({
            "s": s, "i": e["i"], "t": e["t"], "sym": e["sym"], "side": e["side"],
            "atr": atr, "entry": entry, "rows": rows, "base": base,
            "exit_ready": rows[-1][4], "hourly": hourly,
        })
    prepared.sort(key=lambda z: z["t"])
    print("prepared", len(prepared), flush=True)
    model = None
    params = (0.8, 1.5, 1.0)
    next_refit = 0
    closed = []
    chosen, frozen, base = [], [], []
    locks_c = locks_f = 0
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
                        params = choose(model, val_items[-25:])
                        next_refit = item["t"] + 60 * sp.DAY
                        print("refit", sp.iso(item["t"]), "params", params, flush=True)
        if model is not None:
            ex_c, did_c = play(item, model, params[0], params[1], params[2])
            ex_f, did_f = play(item, model, 0.85, 1.5, 1.2)
            locks_c += int(did_c)
            locks_f += int(did_f)
        else:
            ex_c = dict(item["hourly"])
            ex_f = dict(item["hourly"])
        if ex_c:
            chosen.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex_c})
        if ex_f:
            frozen.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex_f})
        base.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]})
        closed.append(item)
    chosen, frozen, base = L.with_hot(chosen), L.with_hot(frozen), L.with_hot(base)
    start = M.ms(2024, 7, 1)
    def one(cands):
        part = [c for c in cands if c["t"] >= start]
        res = L.run_window(part, 0)
        return {"n": res["n"], "wr": res["wr"], "ret": res["ret"], "dd": res["dd"], "final": res["final"]}
    report = {
        "note": "Penalty model unchanged. val_chosen picks threshold and giveback on the last train slice. frozen locks only after a 1.2 ATR fallback from the peak, probability >= 0.85, profit >= 1.5 ATR.",
        "locks_val_chosen": locks_c,
        "locks_frozen": locks_f,
        "val_chosen_periods": M.period_table(chosen),
        "frozen_periods": M.period_table(frozen),
        "hourly_periods": M.period_table(base),
        "from_2024H2_one_book": {
            "val_chosen": one(chosen),
            "frozen_after_peak": one(frozen),
            "hourly_rules": one(base),
        },
    }
    out = Path(__file__).resolve().parent / "_penalty_study.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["from_2024H2_one_book"], ensure_ascii=False, indent=2), flush=True)
    print("LOCKS", locks_c, locks_f, flush=True)
    for name, tbl in (("chosen", report["val_chosen_periods"]), ("frozen", report["frozen_periods"]), ("hourly", report["hourly_periods"])):
        print(name, [(r["period"], r["ret"], r["dd"]) for r in tbl], flush=True)


if __name__ == "__main__":
    main()
