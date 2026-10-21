"""Signals shared by the walk-forward backtest and the live bot.

Rules read only the causal feature vector. The gradient-boosting candidate is
fit on trades whose 24h exit is already known, and sample weights use the
loss streak of trades that had closed before the current entry.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, FEE, N_FEAT

PK = 0.5
THR = 0.006

# Fixed menu. Selection inside a training window is allowed; the traded
# window is always after that selection.
MENU = [("flat",)]
for _z in (1.0, 1.5, 2.0, 2.5):
    for _trend in ("none", "with"):
        MENU.append(("carry", _z, _trend))
for _thr in (0.03, 0.05, 0.08):
    MENU.append(("revert", _thr))
for _thr in (0.04, 0.08):
    MENU.append(("mom3", _thr))
for _thr in (0.0, 0.03):
    MENU.append(("btc", _thr))
MENU.append(("gb", THR))


def decide(spec, row) -> tuple[int, float]:
    kind = spec[0]
    f = row["feat"]
    z = float(row["z"])
    r24 = float(f[6])
    r3 = float(f[7])
    fund = float(f[2])
    if kind == "flat":
        return 0, 0.0
    if kind == "carry":
        zthr, trend = float(spec[1]), spec[2]
        if z >= zthr and fund > 0:
            side = -1
        elif z <= -zthr and fund < 0:
            side = 1
        else:
            return 0, 0.0
        if trend == "with" and side * r24 <= 0:
            return 0, 0.0
        return side, abs(z)
    if kind == "revert":
        thr = float(spec[1])
        if r24 >= thr:
            return -1, abs(r24)
        if r24 <= -thr:
            return 1, abs(r24)
        return 0, 0.0
    if kind == "mom3":
        thr = float(spec[1])
        if r3 >= thr:
            return 1, abs(r3)
        if r3 <= -thr:
            return -1, abs(r3)
        return 0, 0.0
    if kind == "btc":
        thr = float(spec[1])
        b = float(row["btc_r7d"])
        if b > thr and r24 > 0:
            return 1, abs(b) + r24
        if b < -thr and r24 < 0:
            return -1, abs(b) + abs(r24)
        return 0, 0.0
    if kind == "gb":
        side = int(row.get("gb_side") or 0)
        score = float(row.get("gb_score") or 0.0)
        return (side, score) if score > float(spec[1]) else (0, 0.0)
    return 0, 0.0


def model_x(row, side: int) -> np.ndarray:
    return np.concatenate([
        row["feat"],
        np.array([row["btc_r1d"], row["btc_r7d"], float(side)], dtype=np.float64),
    ])


def _weights_and_targets(rows: list[dict]):
    by = {}
    for r in rows:
        by.setdefault(r["ci"], []).append(r)
    xs, ys, ws = [], [], []
    for ci in sorted(by):
        pending = deque()
        streak = 0
        for r in sorted(by[ci], key=lambda x: x["t"]):
            t = int(r["t"])
            while pending and pending[0][0] <= t:
                loss = pending.popleft()[1]
                streak = streak + 1 if loss else 0
            wt = 1.0 + PK * min(streak, 4)
            if "ret_l" in r:
                net_l = r["ret_l"] - r["paid_l"] - FEE
                net_s = -r["ret_s"] + r["paid_s"] - FEE
            else:
                net_l = r["ret"] - r["paid"] - FEE
                net_s = -r["ret"] + r["paid"] - FEE
            pending.append((t + DAY, max(net_l, net_s) < 0))
            xs.append(model_x(r, 1))
            ys.append(net_l)
            ws.append(wt)
            xs.append(model_x(r, 0))
            ys.append(net_s)
            ws.append(wt)
    if not xs:
        return None
    return np.vstack(xs), np.array(ys, dtype=np.float64), np.array(ws, dtype=np.float64)


def fit_gb(rows: list[dict]):
    packed = _weights_and_targets(rows)
    if packed is None or len(packed[0]) < 800:
        return None
    x, y, w = packed
    model = HistGradientBoostingRegressor(
        max_iter=80, learning_rate=0.06, max_depth=3,
        min_samples_leaf=80, l2_regularization=1.0,
        early_stopping=False, random_state=0,
    )
    model.fit(x, y, sample_weight=w)
    return model


def apply_gb(model, rows: list[dict], thr: float = THR) -> None:
    if model is None:
        for r in rows:
            r["gb_side"] = 0
            r["gb_score"] = 0.0
        return
    xl = np.vstack([model_x(r, 1) for r in rows])
    xs = np.vstack([model_x(r, 0) for r in rows])
    pl = model.predict(xl)
    ps = model.predict(xs)
    for r, a, b in zip(rows, pl, ps):
        if a >= b:
            r["gb_side"] = 1
            r["gb_score"] = float(a)
        else:
            r["gb_side"] = -1
            r["gb_score"] = float(b)
        if r["gb_score"] <= thr:
            r["gb_side"] = 0


def spec_name(spec) -> str:
    return ":".join(str(x) for x in spec)


def assert_feature_width():
    if N_FEAT != 16:
        raise RuntimeError("feature contract changed")
