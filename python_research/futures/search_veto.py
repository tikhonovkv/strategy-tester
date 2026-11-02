"""Daily long model. It may trade only z=1.5 longs above the 10-day average.

The score threshold is chosen on entries before 2024. 2024-2026 are one test.
"""
from datetime import datetime, timezone

import numpy as np

from causal import DAY, TRADE_IDX
from futures_9to4 import WINDOW_MS, half_year_returns, load_rows
from search_ma_one import book, show
from search_model_long import fit_long, one, setup
from search_skip import skip_after_two
from signals import model_x
import search_stable as s

START = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
CUT = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
THRS = (0.0, 0.002, 0.004, 0.006, 0.008, 0.012, 0.02)


def score_days(rows):
    ts = np.array([r["t"] for r in rows], dtype=np.int64)
    t_min, t_max = int(ts[0]), int(ts[-1])
    day0 = (t_min // DAY + 1) * DAY
    days = [d for d in range(day0 + WINDOW_MS, t_max, DAY) if d >= START]
    print(f"fits={len(days)}", flush=True)
    scored = []
    t0 = __import__("time").time()
    for i, d in enumerate(days):
        lo = int(np.searchsorted(ts, d - WINDOW_MS, side="left"))
        hi = int(np.searchsorted(ts, d - DAY, side="right"))
        model = fit_long(rows[lo:hi])
        a = int(np.searchsorted(ts, d, side="left"))
        b = int(np.searchsorted(ts, d + DAY, side="left"))
        test = [r for r in rows[a:b] if r["ci"] in TRADE_IDX and "ret_l" in r and setup(r)]
        if model is not None and test:
            pred = model.predict(np.vstack([model_x(r, 1) for r in test]))
            for r, p in zip(test, pred):
                scored.append({
                    "t": int(r["t"]), "ci": r["ci"], "side": 1, "score": float(p),
                    "hold": int(r["hold_l"]),
                    "ret": r["ret_l"], "paid": r["paid_l"], "min": r["min_l"], "max": r["max_l"],
                    "ret_l": r["ret_l"], "paid_l": r["paid_l"], "min_l": r["min_l"], "max_l": r["max_l"], "hold_l": r["hold_l"],
                    "ret_s": r["ret_s"], "paid_s": r["paid_s"], "min_s": r["min_s"], "max_s": r["max_s"], "hold_s": r["hold_s"],
                })
        if i == 0 or (i + 1) % 40 == 0 or i + 1 == len(days):
            el = __import__("time").time() - t0
            left = el / (i + 1) * (len(days) - i - 1)
            print(f"day {i+1}/{len(days)} kept={len(scored)} eta {left/60:.1f}m", flush=True)
    return scored


def prepare(events, thr):
    kept, _skipped = skip_after_two(one(events, thr))
    return kept


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    scored = score_days(rows)
    dev = [r for r in scored if r["t"] < CUT]
    best = None
    print("--- before 2024 ---", flush=True)
    for thr in THRS:
        res = book(prepare(dev, thr))
        halves = half_year_returns(res["curve"], 2000.0)
        h1, h2 = halves.get("2023H1"), halves.get("2023H2")
        print(
            f"  thr={thr} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}% H1={h1} H2={h2}",
            flush=True,
        )
        if h1 is None or h2 is None or res["n"] < 15 or res["max_dd"] > 0.15 or h1 <= 0 or h2 <= 0:
            continue
        score = min(h1, h2)
        if best is None or score > best[0]:
            best = (score, thr)
    if best is None:
        print("NO threshold before 2024", flush=True)
        return
    thr = best[1]
    print(f"FROZEN thr={thr}", flush=True)
    res = book(prepare([r for r in scored if r["t"] >= CUT], thr))
    show("2024-2026", res)
    for k, v in half_year_returns(res["curve"], 2000.0).items():
        print(f"{k} {v:+.1f}%", flush=True)


if __name__ == "__main__":
    main()
