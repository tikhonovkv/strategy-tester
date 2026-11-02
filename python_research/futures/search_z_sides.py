"""Long and short are selected separately on 2024 half-years, then frozen."""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
from search_z_ma import accept_one, pack
import search_stable as s


def pick(rows, side):
    best = None
    print(f"--- 2024 side={side} ---", flush=True)
    y2024 = [r for r in rows if r["t"] < Y2025]
    for thr in (1.0, 1.5, 2.0, 2.5, 3.0):
        events = [e for e in accept_one(pack(y2024, thr)) if e["side"] == side]
        res = book(events)
        halves = half_year_returns(res["curve"], 2000.0)
        h1, h2 = halves.get("2024H1"), halves.get("2024H2")
        print(
            f"  z={thr} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% H1={h1} H2={h2}",
            flush=True,
        )
        if h1 is None or h2 is None or res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
            continue
        score = min(h1, h2)
        if best is None or score > best[0]:
            best = (score, thr)
    return None if best is None else best[1]


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    for side, name in ((1, "LONG"), (-1, "SHORT")):
        thr = pick(rows, side)
        if thr is None:
            print(f"NO {name} threshold", flush=True)
            continue
        print(f"FROZEN {name} z={thr}", flush=True)
        for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
            part = [r for r in rows if lo <= r["t"] < hi]
            events = [e for e in accept_one(pack(part, thr)) if e["side"] == side]
            res = book(events)
            show(f"{name} {label}", res)
            print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
