"""Daily model, Schwartz exit, then a causal grid.

Scores are walk-forward. Threshold, leverage, the 10-day-average filter and
the cooldown after a loss are chosen only on entries before 2026-07-22.
The last quarter is scored once.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from causal import DAY, FEE, TRADE_IDX
from discipline import _ma, _px, _series, apply_schwartz
from futures_9to4 import WINDOW_MS, load_rows
from portfolio import simulate
from signals import apply_gb, fit_gb

CUT = int(datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp() * 1000)
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def add_ma_gap(rows: list[dict]) -> None:
    by = {}
    for row in rows:
        by.setdefault(row["ci"], []).append(row)
    from causal import COINS
    for ci, group in by.items():
        bar_ts, bar_px, d_ts, d_px, _f_ts, _f_rate = _series(COINS[ci])
        for row in group:
            t = int(row["t"])
            px = _px(bar_ts, bar_px, t)
            ma = _ma(d_ts, d_px, t)
            gap = 0.0 if px is None or ma is None or ma == 0 else px / ma - 1.0
            row["ma_gap"] = float(gap)
            row["feat"] = np.append(np.asarray(row["feat"], dtype=np.float64), gap)


def score_days(rows: list[dict]) -> list[dict]:
    t_min = min(r["t"] for r in rows)
    t_max = max(r["t"] for r in rows)
    ts = np.array([r["t"] for r in rows], dtype=np.int64)
    day0 = (t_min // DAY + 1) * DAY
    days = [d for d in range(day0 + WINDOW_MS, t_max, DAY) if d >= START]
    print(f"fits={len(days)} from {iso(days[0])}", flush=True)
    scored = []
    t0 = __import__("time").time()
    for i, d in enumerate(days):
        lo = int(np.searchsorted(ts, d - WINDOW_MS, side="left"))
        hi = int(np.searchsorted(ts, d - DAY, side="right"))
        train = rows[lo:hi]
        model = fit_gb(train)
        a = int(np.searchsorted(ts, d, side="left"))
        b = int(np.searchsorted(ts, d + DAY, side="left"))
        test = [r for r in rows[a:b] if r["ci"] in TRADE_IDX]
        if model is not None and test:
            apply_gb(model, test, thr=-1.0)
            for r in test:
                if int(r.get("gb_side") or 0) == 0:
                    continue
                side = int(r["gb_side"])
                if side == 1:
                    ret, paid, hold = r["ret_l"], r["paid_l"], r["hold_l"]
                    net = ret - paid - FEE
                else:
                    ret, paid, hold = r["ret_s"], r["paid_s"], r["hold_s"]
                    net = -ret + paid - FEE
                scored.append({
                    "t": int(r["t"]),
                    "ci": int(r["ci"]),
                    "side": side,
                    "score": float(r["gb_score"]),
                    "ma_gap": float(r["ma_gap"]),
                    "ret": float(ret),
                    "paid": float(paid),
                    "min": float(r["min_l"] if side == 1 else r["min_s"]),
                    "max": float(r["max_l"] if side == 1 else r["max_s"]),
                    "hold": int(hold),
                    "net": float(net),
                    "ret_l": float(r["ret_l"]), "paid_l": float(r["paid_l"]),
                    "min_l": float(r["min_l"]), "max_l": float(r["max_l"]), "hold_l": int(r["hold_l"]),
                    "ret_s": float(r["ret_s"]), "paid_s": float(r["paid_s"]),
                    "min_s": float(r["min_s"]), "max_s": float(r["max_s"]), "hold_s": int(r["hold_s"]),
                })
        if i == 0 or (i + 1) % 20 == 0 or i + 1 == len(days):
            el = __import__("time").time() - t0
            left = el / (i + 1) * (len(days) - i - 1)
            print(f"day {i+1}/{len(days)} {iso(d)} kept={len(scored)} eta {left/60:.1f}m", flush=True)
    return scored


def accept(scored: list[dict], thr: float, with_ma: bool, cooldown_days: int) -> list[dict]:
    cool = cooldown_days * DAY
    pending = []
    blocked_until = {}
    out = []
    for r in sorted(scored, key=lambda x: (x["t"], x["ci"])):
        t = r["t"]
        still = []
        for exit_t, ci, lost in pending:
            if exit_t <= t:
                if lost and cool:
                    blocked_until[ci] = exit_t + cool
            else:
                still.append((exit_t, ci, lost))
        pending = still
        if r["score"] <= thr or r["side"] == 0:
            continue
        if with_ma and r["side"] * r["ma_gap"] <= 0:
            continue
        if blocked_until.get(r["ci"], 0) > t:
            continue
        out.append(r)
        pending.append((t + r["hold"], r["ci"], r["net"] < 0))
    return out


def run_book(events: list[dict], lev: float, stake: float) -> dict:
    rows = []
    for e in events:
        rows.append({
            "t": e["t"], "ci": e["ci"], "gb_side": e["side"], "gb_score": e["score"],
            "ret_l": e["ret_l"], "paid_l": e["paid_l"], "min_l": e["min_l"], "max_l": e["max_l"], "hold_l": e["hold_l"],
            "ret_s": e["ret_s"], "paid_s": e["paid_s"], "min_s": e["min_s"], "max_s": e["max_s"], "hold_s": e["hold_s"],
            "ret": e["ret"], "paid": e["paid"], "min_ret": e["min"], "max_ret": e["max"],
        })
    return simulate(rows, lambda r: (int(r["gb_side"]), float(r["gb_score"])), lev, stake, 2000.0)


def main():
    rows = load_rows()
    print(apply_schwartz(rows), flush=True)
    add_ma_gap(rows)
    scored = score_days(rows)
    dev = [r for r in scored if r["t"] < CUT]
    hold = [r for r in scored if r["t"] >= CUT]
    best = None
    print("--- dev grid ---", flush=True)
    for thr in (0.004, 0.008, 0.012, 0.02):
        for with_ma in (False, True):
            for cool in (0, 5):
                for lev, stake in ((1, 0.2), (2, 0.15)):
                    ev = [r for r in accept(dev, thr, with_ma, cool)]
                    if len(ev) < 12:
                        continue
                    res = run_book(ev, lev, stake)
                    ok = res["return"] > 0 and res["max_dd"] <= 0.25
                    mark = " *" if ok else ""
                    name = f"thr={thr} ma={int(with_ma)} cool={cool}d lev={lev} st={stake}"
                    print(
                        f"  {name} n={res['n']} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%{mark}",
                        flush=True,
                    )
                    score = res["return"] - 0.5 * res["max_dd"]
                    if ok and (best is None or score > best[0]):
                        best = (score, name, thr, with_ma, cool, lev, stake)
    if best is None:
        print("NO stable profitable dev config", flush=True)
        return
    _s, name, thr, with_ma, cool, lev, stake = best
    ev = accept(hold, thr, with_ma, cool)
    res = run_book(ev, lev, stake)
    print(
        f"PICK {name} QUARTER n={res['n']} ret={res['return']*100:+.1f}% "
        f"dd={res['max_dd']*100:.0f}% final={res['final']:.0f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
