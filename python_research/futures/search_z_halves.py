"""Pick z on 2024 only when both half-years are positive and drawdown is at most 15%.

Among those, keep the threshold whose weaker half-year is higher.
2025 and 2026 are scored once with that frozen threshold.
"""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
from search_z_ma import accept_one, pack
import search_stable as s


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    y2024 = [r for r in rows if r["t"] < Y2025]
    best = None
    print("--- 2024 half-years ---", flush=True)
    for thr in (1.0, 1.5, 2.0, 2.5, 3.0):
        res = book(accept_one(pack(y2024, thr)))
        halves = half_year_returns(res["curve"], 2000.0)
        h1 = halves.get("2024H1")
        h2 = halves.get("2024H2")
        print(
            f"  z={thr} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% "
            f"H1={h1} H2={h2}",
            flush=True,
        )
        if h1 is None or h2 is None:
            continue
        if res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
            continue
        score = min(h1, h2)
        if best is None or score > best[0]:
            best = (score, thr)
    if best is None:
        print("NO 2024 threshold with both halves positive and dd<=15%", flush=True)
        return
    thr = best[1]
    print(f"FROZEN z={thr}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        res = book(accept_one(pack(part, thr)))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
