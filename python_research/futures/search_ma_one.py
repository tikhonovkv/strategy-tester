"""One position, long only with the 10-day average, short only against it.

Threshold is chosen on 2024. 2025 and 2026 are scored with that frozen threshold.
"""
from datetime import datetime, timezone

import search_stable as s
from portfolio import simulate

s.START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
Y2025 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
Y2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def accept_one(scored, thr):
    open_until = 0
    out = []
    for r in sorted(scored, key=lambda x: (x["t"], x["ci"])):
        if r["score"] <= thr or r["side"] == 0:
            continue
        if r["side"] * r["ma_gap"] <= 0:
            continue
        if r["t"] < open_until:
            continue
        out.append(r)
        open_until = r["t"] + r["hold"]
    return out


def book(events):
    rows = []
    for e in events:
        rows.append({
            "t": e["t"], "ci": e["ci"], "gb_side": e["side"], "gb_score": e["score"],
            "ret_l": e["ret_l"], "paid_l": e["paid_l"], "min_l": e["min_l"], "max_l": e["max_l"], "hold_l": e["hold_l"],
            "ret_s": e["ret_s"], "paid_s": e["paid_s"], "min_s": e["min_s"], "max_s": e["max_s"], "hold_s": e["hold_s"],
            "ret": e["ret"], "paid": e["paid"], "min_ret": e["min"], "max_ret": e["max"],
        })
    if not rows:
        return {"n": 0, "return": 0.0, "max_dd": 0.0, "final": 2000.0}
    return simulate(rows, lambda r: (int(r["gb_side"]), float(r["gb_score"])), 1.0, 0.2, 2000.0)


def show(label, res):
    print(
        f"{label} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% final={res['final']:.0f}",
        flush=True,
    )


def main():
    rows = s.load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    scored = s.score_days(rows)
    y2024 = [r for r in scored if r["t"] < Y2025]
    best = None
    print("--- 2024 threshold ---", flush=True)
    for thr in (0.004, 0.006, 0.008, 0.01, 0.015, 0.02):
        res = book(accept_one(y2024, thr))
        ok = res["n"] >= 15 and res["return"] > 0 and res["max_dd"] <= 0.25
        score = res["return"] - 0.5 * res["max_dd"]
        print(f"  thr={thr} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%", flush=True)
        if ok and (best is None or score > best[0]):
            best = (score, thr)
    if best is None:
        print("NO 2024 config with plus and dd<=25%", flush=True)
        return
    thr = best[1]
    print(f"FROZEN thr={thr}", flush=True)
    for label, lo, hi in (
        ("2025", Y2025, Y2026),
        ("2026", Y2026, 10**18),
    ):
        show(label, book(accept_one([r for r in scored if lo <= r["t"] < hi], thr)))


if __name__ == "__main__":
    main()
