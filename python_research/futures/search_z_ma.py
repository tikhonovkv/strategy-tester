"""Funding z-score plus the 10-day average. No new boosting fit.

The z threshold is chosen on 2024 only. 2025 and 2026 stay frozen.
One position, leverage 1x, exit when price closes on the wrong side of the average.
"""
from datetime import datetime, timezone

from causal import TRADE_IDX
from futures_9to4 import load_rows
from search_ma_one import Y2025, Y2026, book, show
import search_stable as s


def side_of(row, thr):
    z = float(row["z"])
    fund = float(row["feat"][2])
    if z >= thr and fund > 0:
        return -1
    if z <= -thr and fund < 0:
        return 1
    return 0


def pack(rows, thr):
    out = []
    for r in rows:
        if r["ci"] not in TRADE_IDX:
            continue
        side = side_of(r, thr)
        if side == 0 or "ma_gap" not in r:
            continue
        hold = r["hold_l"] if side == 1 else r["hold_s"]
        ret = r["ret_l"] if side == 1 else r["ret_s"]
        paid = r["paid_l"] if side == 1 else r["paid_s"]
        out.append({
            "t": r["t"], "ci": r["ci"], "side": side, "score": abs(float(r["z"])),
            "ma_gap": r["ma_gap"], "hold": hold, "ret": ret, "paid": paid,
            "min": r["min_l"] if side == 1 else r["min_s"],
            "max": r["max_l"] if side == 1 else r["max_s"],
            "ret_l": r["ret_l"], "paid_l": r["paid_l"], "min_l": r["min_l"], "max_l": r["max_l"], "hold_l": r["hold_l"],
            "ret_s": r["ret_s"], "paid_s": r["paid_s"], "min_s": r["min_s"], "max_s": r["max_s"], "hold_s": r["hold_s"],
        })
    return out


def accept_one(events):
    open_until = 0
    out = []
    for r in sorted(events, key=lambda x: (x["t"], x["ci"])):
        if r["side"] * r["ma_gap"] <= 0:
            continue
        if r["t"] < open_until:
            continue
        out.append(r)
        open_until = r["t"] + r["hold"]
    return out


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    y2024 = [r for r in rows if r["t"] < Y2025]
    best = None
    print("--- 2024 z threshold ---", flush=True)
    for thr in (1.0, 1.5, 2.0, 2.5, 3.0):
        res = book(accept_one(pack(y2024, thr)))
        ok = res["n"] >= 15 and res["return"] > 0 and res["max_dd"] <= 0.25
        score = res["return"] - 0.5 * res["max_dd"]
        print(f"  z={thr} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%", flush=True)
        if ok and (best is None or score > best[0]):
            best = (score, thr)
    if best is None:
        print("NO 2024 config with plus and dd<=25%", flush=True)
        return
    thr = best[1]
    print(f"FROZEN z={thr}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        part = [r for r in rows if lo <= r["t"] < hi]
        show(label, book(accept_one(pack(part, thr))))


if __name__ == "__main__":
    main()
