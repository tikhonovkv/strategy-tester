"""Hard loss stop on top of the 10-day average, long z=1.5 only.

The stop size is chosen on 2024. 2025 and 2026 are scored once.
"""
from causal import COINS, DAY, HOUR, TRADE_IDX
from discipline import _ma, _paid, _px, _series
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
import search_stable as s

Z = 1.5
STOPS = (0.005, 0.01, 0.015, 0.02, 0.03)


def walk_stop(bar_ts, bar_px, d_ts, d_px, f_ts, f_rate, t, stop):
    entry = _px(bar_ts, bar_px, t)
    if entry is None or entry <= 0:
        return None
    exit_t = t + DAY
    exit_px = entry
    lo = hi = entry
    for step in range(1, 25):
        ht = t + step * HOUR
        px = _px(bar_ts, bar_px, ht)
        if px is None:
            break
        lo = min(lo, px)
        hi = max(hi, px)
        ma = _ma(d_ts, d_px, ht)
        wrong = ma is not None and px < ma
        stopped = px / entry - 1.0 <= -stop
        exit_t = ht
        exit_px = px
        if wrong or stopped or step == 24:
            break
    return {
        "ret": exit_px / entry - 1.0,
        "paid": _paid(f_ts, f_rate, t, exit_t),
        "min": lo / entry - 1.0,
        "max": hi / entry - 1.0,
        "hold": exit_t - t,
    }


def candidates(rows, stop, cache):
    out = []
    for r in rows:
        if r["ci"] not in TRADE_IDX:
            continue
        if not (float(r["z"]) <= -Z and float(r["feat"][2]) < 0 and float(r["ma_gap"]) > 0):
            continue
        series = cache[r["ci"]]
        leg = walk_stop(*series, int(r["t"]), stop)
        if leg is None:
            continue
        out.append({
            "t": int(r["t"]), "ci": r["ci"], "side": 1, "score": abs(float(r["z"])),
            "hold": leg["hold"], "ret": leg["ret"], "paid": leg["paid"],
            "min": leg["min"], "max": leg["max"],
            "ret_l": leg["ret"], "paid_l": leg["paid"], "min_l": leg["min"], "max_l": leg["max"], "hold_l": leg["hold"],
            "ret_s": leg["ret"], "paid_s": leg["paid"], "min_s": leg["min"], "max_s": leg["max"], "hold_s": leg["hold"],
        })
    return out


def one_position(events):
    open_until = 0
    out = []
    for r in sorted(events, key=lambda x: (x["t"], x["ci"])):
        if r["t"] < open_until:
            continue
        out.append(r)
        open_until = r["t"] + r["hold"]
    return out


def main():
    rows = load_rows()
    s.add_ma_gap(rows)
    cache = {ci: _series(COINS[ci]) for ci in range(len(COINS))}
    y2024 = [r for r in rows if r["t"] < Y2025]
    best = None
    print("--- 2024 stop ---", flush=True)
    for stop in STOPS:
        res = book(one_position(candidates(y2024, stop, cache)))
        halves = half_year_returns(res["curve"], 2000.0)
        h1, h2 = halves.get("2024H1"), halves.get("2024H2")
        print(
            f"  stop={stop:.1%} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% H1={h1} H2={h2}",
            flush=True,
        )
        if h1 is None or h2 is None or res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
            continue
        score = min(h1, h2)
        if best is None or score > best[0]:
            best = (score, stop)
    if best is None:
        print("NO stop kept both 2024 halves positive", flush=True)
        return
    stop = best[1]
    print(f"FROZEN stop={stop:.1%}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        res = book(one_position(candidates(part, stop, cache)))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
