"""Evaluate Marty Schwartz ('Pitbull') reactive system vs predictive system.

Tests both:
1. Pure Schwartz Reactive System (10d EMA traffic light + confirmed breakout/pullback + instant loss cut + trailing winners).
2. Daily-Retrained Reactive ML Model (trained ONLY on completed Schwartz-managed trades before `d`, with anti-martingale sizing after losses).

Strictly causal: at timestamp `t`, only hourly bars with close_ts <= t and funding with f_ts <= t are visible.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, HOUR, FEE
from schwartz_pitbull import FOUR_SYMS, TRADE_SYMS, build_hourly_series, simulate_schwartz_trade


def build_reactive_candidates(
    syms: list[str],
    stop_atr: float = 1.8,
    bail_hours: int = 2,
    bail_atr: float = -0.3,
    trail_arm_atr: float = 2.8,
    trail_dist_atr: float = 1.2,
    max_hold_h: int = 24,
    step_h: int = 4,
):
    series = {sym: build_hourly_series(sym) for sym in syms}
    btc = series["BTCUSDT"] if "BTCUSDT" in series else build_hourly_series("BTCUSDT")
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}

    rows = []
    for ci, sym in enumerate(syms):
        s = series[sym]
        close_ts = s["close_ts"]
        px = s["px"]
        vol = s["vol"]
        atr1h = s["atr1h"]
        ema24 = s["ema24"]
        ema72 = s["ema72"]
        ema240 = s["ema240"]
        f_ts = s["f_ts"]
        f_rate = s["f_rate"]

        for idx in range(260, len(px) - max_hold_h - 1):
            t = int(close_ts[idx])
            if (t // HOUR) % step_h != 0:
                continue
            if int(close_ts[idx - 240]) != t - 240 * HOUR:
                continue
            bi = btc_map.get(t)
            if bi is None or bi < 240:
                continue

            cur_px = float(px[idx])
            e240 = float(ema240[idx])
            e72 = float(ema72[idx])
            e24 = float(ema24[idx])
            atr = max(float(atr1h[idx]), 0.002)

            # 10d EMA Traffic Light (Schwartz's #1 rule):
            # Green light (LONG): price > 10d EMA and 10d EMA rising over last 24h
            # Red light (SHORT): price < 10d EMA and 10d EMA falling over last 24h
            ema10_slope = e240 / float(ema240[idx - 24]) - 1.0
            if cur_px > e240 and ema10_slope > 0:
                side = 1
            elif cur_px < e240 and ema10_slope < 0:
                side = -1
            else:
                continue

            # Latest settled funding rate at or before t
            fi = int(np.searchsorted(f_ts, t, side="right")) - 1
            if fi < 90:
                continue
            f_hist = f_rate[fi - 89:fi + 1]
            f_now = float(f_rate[fi])
            f_z = float(f_now - f_hist.mean()) / (float(f_hist.std()) + 1e-8)

            # Price action & momentum in the direction of side
            r1 = side * (cur_px / float(px[idx - 1]) - 1.0) / atr
            r4 = side * (cur_px / float(px[idx - 4]) - 1.0) / atr
            r8 = side * (cur_px / float(px[idx - 8]) - 1.0) / atr
            r24 = side * (cur_px / float(px[idx - 24]) - 1.0) / atr
            r72 = side * (cur_px / float(px[idx - 72]) - 1.0) / atr
            dist_ema10 = side * (cur_px / e240 - 1.0) / atr
            dist_ema3 = side * (cur_px / e72 - 1.0) / atr
            dist_ema1 = side * (cur_px / e24 - 1.0) / atr

            # Breakout / pullback position inside last 24h range
            win24 = px[idx - 24:idx]
            hi24 = float(win24.max())
            lo24 = float(win24.min())
            rng24 = max(hi24 - lo24, cur_px * 1e-4)
            pos24 = (cur_px - lo24) / rng24 if side == 1 else (hi24 - cur_px) / rng24
            brk24 = (cur_px / hi24 - 1.0) / atr if side == 1 else (lo24 / cur_px - 1.0) / atr

            # Volume surge (last 4h vs 24h average)
            v4 = float(vol[idx - 3:idx + 1].mean())
            v24 = float(vol[idx - 23:idx + 1].mean()) + 1e-9
            vol_ratio = v4 / v24

            # BTC regime alignment
            btc_px = float(btc["px"][bi])
            btc_e240 = float(btc["ema240"][bi])
            btc_trend = side * (btc_px / btc_e240 - 1.0) / max(float(btc["atr1h"][bi]), 0.002)
            btc_r24 = side * (btc_px / float(btc["px"][bi - 24]) - 1.0)

            # Carry alignment (positive carry if side * f_now < 0)
            carry_z = -side * f_z

            out = simulate_schwartz_trade(
                s,
                idx,
                side,
                stop_atr=stop_atr,
                bail_hours=bail_hours,
                bail_atr=bail_atr,
                trail_arm_atr=trail_arm_atr,
                trail_dist_atr=trail_dist_atr,
                max_hold_h=max_hold_h,
            )
            if out is None:
                continue

            feat = np.array([
                float(side),
                r1, r4, r8, r24, r72,
                dist_ema1, dist_ema3, dist_ema10,
                pos24, brk24, vol_ratio,
                btc_trend, btc_r24,
                carry_z, -side * f_now * 1000.0,
                atr * 100.0,
            ], dtype=np.float64)

            rows.append({
                "t": t,
                "exit_ts": out["exit_ts"],
                "hold_h": out["hold_h"],
                "ci": ci,
                "sym": sym,
                "side": side,
                "feat": feat,
                "r1": r1,
                "r4": r4,
                "r24": r24,
                "pos24": pos24,
                "brk24": brk24,
                "vol_ratio": vol_ratio,
                "btc_trend": btc_trend,
                "carry_z": carry_z,
                "dist_ema10": dist_ema10,
                "net": out["net"],
                "min_move": out["min_move"],
                "peak_move": out["peak_move"],
                "reason": out["reason"],
            })
    rows.sort(key=lambda r: (r["t"], r["ci"]))
    return rows


if __name__ == "__main__":
    rows = build_reactive_candidates(TRADE_SYMS, step_h=4)
    reasons = {}
    for r in rows:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    nets = np.array([r["net"] for r in rows])
    holds = np.array([r["hold_h"] for r in rows])
    print(f"total candidates={len(rows)} avg_hold={holds.mean():.1f}h mean_net={nets.mean()*100:+.3f}% hit={(nets>0).mean()*100:.1f}%")
    print("exit reasons:", reasons)
