"""Daily model trained only on long outcomes.

It may trade only a long with z<=-1.5, negative funding and price above the
10-day average. The score threshold is chosen on 2024.
"""
from collections import deque
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, FEE, TRADE_IDX
from futures_9to4 import WINDOW_MS, half_year_returns, load_rows
from search_ma_one import Y2025, Y2026, book, show
from signals import PK, model_x
import search_stable as s

START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
THRS = (0.0, 0.002, 0.004, 0.006, 0.008, 0.012, 0.02)


def fit_long(rows):
    by = {}
    for r in rows:
        if "ret_l" not in r:
            continue
        by.setdefault(r["ci"], []).append(r)
    xs, ys, ws = [], [], []
    for ci in sorted(by):
        pending = deque()
        streak = 0
        for r in by[ci]:
            t = int(r["t"])
            while pending and pending[0][0] <= t:
                streak = streak + 1 if pending.popleft()[1] else 0
            net = r["ret_l"] - r["paid_l"] - FEE
            pending.append((t + int(r["hold_l"]), net < 0))
            xs.append(model_x(r, 1))
            ys.append(net)
            ws.append(1.0 + PK * min(streak, 4))
    if len(xs) < 400:
        return None
    model = HistGradientBoostingRegressor(
        max_iter=80, learning_rate=0.06, max_depth=3,
        min_samples_leaf=80, l2_regularization=1.0,
        early_stopping=False, random_state=0,
    )
    model.fit(np.vstack(xs), np.array(ys), sample_weight=np.array(ws))
    return model


def setup(r):
    return float(r["z"]) <= -1.5 and float(r["feat"][2]) < 0 and float(r["ma_gap"]) > 0


def one(events, thr):
    open_until = 0
    kept = []
    for r in sorted(events, key=lambda x: (x["t"], x["ci"])):
        if r["score"] <= thr or r["t"] < open_until:
            continue
        kept.append(r)
        open_until = r["t"] + r["hold"]
    return kept


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    for group in {}:
        pass
    by = {}
    for r in rows:
        by.setdefault(r["ci"], []).append(r)
    for group in by.values():
        group.sort(key=lambda r: r["t"])
    ts = np.array([r["t"] for r in rows], dtype=np.int64)
    t_min = int(ts[0])
    t_max = int(ts[-1])
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
            x = np.vstack([model_x(r, 1) for r in test])
            pred = model.predict(x)
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
    y2024 = [r for r in scored if r["t"] < Y2025]
    best = None
    print("--- 2024 model threshold ---", flush=True)
    for thr in THRS:
        res = book(one(y2024, thr))
        halves = half_year_returns(res["curve"], 2000.0)
        h1, h2 = halves.get("2024H1"), halves.get("2024H2")
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
        print("NO 2024 model threshold", flush=True)
        return
    thr = best[1]
    print(f"FROZEN thr={thr}", flush=True)
    for label, lo, hi in (("2025", Y2025, Y2026), ("2026", Y2026, 10**18)):
        res = book(one([r for r in scored if lo <= r["t"] < hi], thr))
        show(label, res)
        print(" ", half_year_returns(res["curve"], 2000.0), flush=True)


if __name__ == "__main__":
    main()
