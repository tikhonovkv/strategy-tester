"""Daily-retrained ML model on top of Schwartz reactive candidates.

At the start of each UTC day `d`:
- Training set: only Schwartz trend+bounce events whose actual exit timestamp `exit_ts <= d`
  within the last 240 days (strictly zero look-ahead!).
- Sample weighting: closed losses increase weight on subsequent examples of that coin,
  teaching the model which market conditions lead to whipsaws vs clean trend runs.
- Test set: events occurring in `[d, d + DAY)`.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, HOUR
from futures_9to4 import half_year_returns
from test_schwartz_hybrid import TRADE_SYMS, Y2024, Y2025, Y2026, Q_CUT, collect_events, sim_portfolio


def fit_reactive_model(train_events: list[dict]):
    if len(train_events) < 250:
        return None
    by_c = {}
    for e in train_events:
        by_c.setdefault(e["ci"], []).append(e)

    xs, ys, ws = [], [], []
    for ci in sorted(by_c):
        pending = deque()
        streak = 0
        for e in sorted(by_c[ci], key=lambda x: x["t"]):
            t = e["t"]
            while pending and pending[0][0] <= t:
                lost = pending.popleft()[1]
                streak = streak + 1 if lost else 0
            w = 1.0 + 0.6 * min(streak, 4)
            pending.append((e["exit_ts"], e["net"] < 0))
            xs.append(e["feat"])
            # Clip extreme outliers in training target so the model focuses on consistent edge
            ys.append(float(np.clip(e["net"], -0.05, 0.08)))
            ws.append(w)

    model = HistGradientBoostingRegressor(
        max_iter=70,
        learning_rate=0.05,
        max_depth=3,
        min_samples_leaf=50,
        l2_regularization=2.0,
        early_stopping=False,
        random_state=0,
    )
    model.fit(np.vstack(xs), np.array(ys, dtype=np.float64), sample_weight=np.array(ws, dtype=np.float64))
    return model


def walk_forward_reactive(events: list[dict], train_win_days: int = 365):
    t_min = min(e["t"] for e in events)
    t_max = max(e["t"] for e in events)
    day0 = ((t_min // DAY) + train_win_days) * DAY
    days = list(range(day0, t_max + DAY, DAY))

    scored = []
    model = None
    for idx, d in enumerate(days):
        # Re-fit every 3 days (or daily in recent quarter) using ONLY closed trades (exit_ts <= d)
        if model is None or idx % 3 == 0 or d >= Q_CUT:
            lo = d - train_win_days * DAY
            train = [e for e in events if lo <= e["t"] and e["exit_ts"] <= d]
            m = fit_reactive_model(train)
            if m is not None:
                model = m
        if model is None:
            continue
        day_ev = [dict(e) for e in events if d <= e["t"] < d + DAY]
        if not day_ev:
            continue
        preds = model.predict(np.vstack([e["feat"] for e in day_ev]))
        for e, p in zip(day_ev, preds):
            e["pred"] = float(p)
            # Combine model prediction with non-crowded funding carry
            e["score"] = float(p) + 0.0015 * e["carry_z"]
            scored.append(e)
    return scored


if __name__ == "__main__":
    raw = collect_events(TRADE_SYMS, ema_fast_h=48, ema_slow_h=240)
    # Require BTC alignment + non-negative carry (crowd not already heavily on our side)
    base = [e for e in raw if e["btc_ok"] and e["carry_z"] >= 0.0]
    print(f"base reactive candidates (btc_ok & carry_z>=0): {len(base)}", flush=True)
    scored = walk_forward_reactive(base, train_win_days=365)
    print(f"scored candidates: {len(scored)}", flush=True)

    for name, fn in (
        ("all (btc_ok & carry_z>=0)", lambda e: True),
        ("carry_z >= 0.25", lambda e: e["carry_z"] >= 0.25),
        ("carry_z >= 0.50", lambda e: e["carry_z"] >= 0.50),
        ("carry_z >= 0.75", lambda e: e["carry_z"] >= 0.75),
        ("model pred > 0", lambda e: e["pred"] > 0.0),
        ("model pred > 0 & carry_z>=0.4", lambda e: e["pred"] > 0.0 and e["carry_z"] >= 0.4),
        ("model score > 0.0005", lambda e: e["score"] > 0.0005),
        ("model score > 0.0010", lambda e: e["score"] > 0.0010),
    ):
        sub = [e for e in scored if fn(e)]
        print(f"\n=== {name} (n={len(sub)}) ===", flush=True)
        for lbl, lo, hi in (
            ("FULL", 0, 10**18),
            ("<2025", 0, Y2025),
            ("2025", Y2025, Y2026),
            ("2026", Y2026, 10**18),
            ("QTR", Q_CUT, 10**18),
        ):
            part = [dict(e) for e in sub if lo <= e["t"] < hi]
            if not part:
                continue
            res = sim_portfolio(part, stake=0.25, lev=2.0, max_conc=2, loss_cut=0.5)
            print(f"  {lbl:5s} n={res['n']:4d} ret={res['ret']:+7.2f}% dd={res['dd']:5.1f}% final=${res['final']:.0f}", flush=True)
            if lbl == "FULL":
                halves = half_year_returns(res["curve"], 2000.0)
                pos_h = sum(1 for v in halves.values() if v > 0)
                print(f"    halves ({pos_h}/{len(halves)}): " + " ".join(f"{k}:{v:+.1f}%" for k, v in halves.items()), flush=True)
