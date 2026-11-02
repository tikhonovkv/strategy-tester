"""Long only. Z threshold and the BTC 7-day filter are chosen on 2024."""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
from search_z_ma import accept_one, pack
import search_stable as s


def events(rows, thr, btc_on):
    use = [r for r in rows if r.get("btc_r7d", 0) > 0] if btc_on else rows
    return [e for e in accept_one(pack(use, thr)) if e["side"] == 1]


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    y2024 = [r for r in rows if r["t"] < Y2025]
    best = None
    print("--- 2024 long, BTC week on/off ---", flush=True)
    for btc_on in (False, True):
        for thr in (1.0, 1.5, 2.0, 2.5, 3.0):
            res = book(events(y2024, thr, btc_on))
            halves = half_year_returns(res["curve"], 2000.0)
            h1, h2 = halves.get("2024H1"), halves.get("2024H2")
            print(
                f"  btc={int(btc_on)} z={thr} n={res['n']} ret={res['return']*100:+.1f}% "
                f"dd={res['max_dd']*100:.0f}% H1={h1} H2={h2}",
                flush=True,
            )
            if h1 is None or h2 is None or res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
                continue
            score = min(h1, h2)
            if best is None or score > best[0]:
                best = (score, thr, btc_on)
    if best is None:
        print("NO 2024 config", flush=True)
        return
    _score, thr, btc_on = best
    print(f"FROZEN z={thr} btc={int(btc_on)}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        res = book(events(part, thr, btc_on))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
