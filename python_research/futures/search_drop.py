"""Long after a 24h drop, negative funding, price above the 10-day average.

The drop threshold is chosen on 2024. Exit is the average cross only.
"""
from causal import TRADE_IDX
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
import search_stable as s

DROPS = (0.01, 0.02, 0.03, 0.04, 0.05, 0.08)


def events(rows, drop):
    out = []
    for r in rows:
        if r["ci"] not in TRADE_IDX or "hold_l" not in r:
            continue
        if float(r["feat"][2]) >= 0 or float(r["ma_gap"]) <= 0:
            continue
        r24 = float(r["feat"][6])
        if r24 > -drop:
            continue
        out.append({
            "t": int(r["t"]), "ci": r["ci"], "side": 1, "score": -r24,
            "hold": r["hold_l"],
            "ret": r["ret_l"], "paid": r["paid_l"], "min": r["min_l"], "max": r["max_l"],
            "ret_l": r["ret_l"], "paid_l": r["paid_l"], "min_l": r["min_l"], "max_l": r["max_l"], "hold_l": r["hold_l"],
            "ret_s": r["ret_s"], "paid_s": r["paid_s"], "min_s": r["min_s"], "max_s": r["max_s"], "hold_s": r["hold_s"],
        })
    open_until = 0
    kept = []
    for r in sorted(out, key=lambda x: (x["t"], x["ci"])):
        if r["t"] < open_until:
            continue
        kept.append(r)
        open_until = r["t"] + r["hold"]
    return kept


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    y2024 = [r for r in rows if r["t"] < Y2025]
    best = None
    print("--- 2024 drop ---", flush=True)
    for drop in DROPS:
        res = book(events(y2024, drop))
        halves = half_year_returns(res["curve"], 2000.0)
        h1, h2 = halves.get("2024H1"), halves.get("2024H2")
        print(
            f"  drop={drop:.0%} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% H1={h1} H2={h2}",
            flush=True,
        )
        if h1 is None or h2 is None or res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
            continue
        score = min(h1, h2)
        if best is None or score > best[0]:
            best = (score, drop)
    if best is None:
        print("NO 2024 drop threshold", flush=True)
        return
    drop = best[1]
    print(f"FROZEN drop={drop:.0%}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        res = book(events(part, drop))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
