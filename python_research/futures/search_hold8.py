"""Fixed 8-hour exit for the long z=1.5 setup. No threshold is chosen."""
from causal import HOUR, TRADE_IDX
from discipline import _paid, _px, _series
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
import search_stable as s

HOLD = 8 * HOUR
Z = 1.5


def legs(rows):
    cache = {}
    out = []
    for r in rows:
        if r["ci"] not in TRADE_IDX:
            continue
        if not (float(r["z"]) <= -Z and float(r["feat"][2]) < 0 and float(r["ma_gap"]) > 0):
            continue
        if r["ci"] not in cache:
            from causal import COINS
            cache[r["ci"]] = _series(COINS[r["ci"]])
        bar_ts, bar_px, _d_ts, _d_px, f_ts, f_rate = cache[r["ci"]]
        t = int(r["t"])
        entry = _px(bar_ts, bar_px, t)
        exit_px = _px(bar_ts, bar_px, t + HOLD)
        if entry is None or exit_px is None or entry <= 0:
            continue
        lo = hi = entry
        ok = True
        for step in range(1, 9):
            px = _px(bar_ts, bar_px, t + step * HOUR)
            if px is None:
                ok = False
                break
            lo = min(lo, px)
            hi = max(hi, px)
        if not ok:
            continue
        ret = exit_px / entry - 1.0
        paid = _paid(f_ts, f_rate, t, t + HOLD)
        out.append({
            "t": t, "ci": r["ci"], "side": 1, "score": abs(float(r["z"])),
            "hold": HOLD, "ret": ret, "paid": paid, "min": lo / entry - 1.0, "max": hi / entry - 1.0,
            "ret_l": ret, "paid_l": paid, "min_l": lo / entry - 1.0, "max_l": hi / entry - 1.0, "hold_l": HOLD,
            "ret_s": ret, "paid_s": paid, "min_s": lo / entry - 1.0, "max_s": hi / entry - 1.0, "hold_s": HOLD,
        })
    open_until = 0
    kept = []
    for r in sorted(out, key=lambda x: (x["t"], -x["score"], x["ci"])):
        if r["t"] < open_until:
            continue
        kept.append(r)
        open_until = r["t"] + HOLD
    return kept


def main():
    rows = load_rows()
    s.add_ma_gap(rows)
    for label, lo, hi in (("2024", 0, Y2025), ("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        if label == "2024":
            part = [r for r in part if r["t"] >= int(__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)]
        res = book(legs(part))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
