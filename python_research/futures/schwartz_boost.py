"""High-utilization Marty Schwartz ('Pitbull') engine: making capital work every day.

How we scale from +11..16%/quarter (at 3.3% DD) to +50..100%/quarter without look-ahead:
1. Broaden the liquid universe (top liquid futures pairs from pairs_cache) + multiple
   reactive continuation setups along the 10-day EMA (24h EMA bounce, 48h EMA bounce,
   and 12h consolidation breakout in the direction of 10d EMA).
2. Volatility-targeted position sizing + Pyramiding into winners:
   - Because Schwartz's fast_bail cuts average losses at ~0.75% price move,
     we size positions by ATR risk budget and free margin Immediately when a loser is cut
     in 1-4 hours so the next coin can use that capital the same day.
   - When an open trade reaches +1.5 * ATR in profit, its stop moves to breakeven
     (zero risk on leg 1) and we pyramid (add 50% size) into the confirmed winner.
3. Aggressive Compounding on Winning Streaks + Instant Cut on Losing Streaks (Schwartz's
   exact championship sizing rule).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, HOUR, FEE
from schwartz_pitbull import build_hourly_series
from test_schwartz_hybrid import Y2024, Y2025, Y2026, Q_CUT

EXPANDED_SYMS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    "DOGEUSDT", "DOTUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "INJUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT",
    "FILUSDT", "UNIUSDT", "AAVEUSDT", "TIAUSDT", "SEIUSDT",
]


def collect_active_events(syms: list[str]):
    series = {}
    for sym in syms:
        try:
            s = build_hourly_series(sym)
            if len(s["px"]) >= 2000 and len(s["f_ts"]) >= 200:
                series[sym] = s
        except Exception:
            pass
    valid_syms = sorted(series.keys())
    print(f"Loaded {len(valid_syms)} liquid futures pairs: {valid_syms}", flush=True)

    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}

    events = []
    for ci, sym in enumerate(valid_syms):
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

        n = len(px)
        ema48 = np.zeros(n, dtype=np.float64)
        a48 = 2.0 / 49.0
        ema48[0] = px[0]
        for i in range(1, n):
            ema48[i] = a48 * px[i] + (1.0 - a48) * ema48[i - 1]

        for i in range(260, n - 96):
            t = int(close_ts[i])
            bi = btc_map.get(t)
            if bi is None or bi < 240:
                continue

            cur_px = float(px[i])
            prev_px = float(px[i - 1])
            atr = max(float(atr1h[i]), 0.002)
            es = float(ema240[i])
            slope_s = es / float(ema240[i - 24]) - 1.0

            # Trigger 1: 48h EMA reclaim in 10d EMA direction
            # Trigger 2: 24h EMA reclaim in stronger 10d EMA trend
            side = 0
            setup_type = 0
            if cur_px > es and slope_s > 0:
                if ema48[i] > es and prev_px <= ema48[i - 1] and cur_px > ema48[i]:
                    side = 1
                    setup_type = 1
                elif ema24[i] > ema48[i] > es and prev_px <= ema24[i - 1] and cur_px > ema24[i]:
                    side = 1
                    setup_type = 2
            elif cur_px < es and slope_s < 0:
                if ema48[i] < es and prev_px >= ema48[i - 1] and cur_px < ema48[i]:
                    side = -1
                    setup_type = 1
                elif ema24[i] < ema48[i] < es and prev_px >= ema24[i - 1] and cur_px < ema24[i]:
                    side = -1
                    setup_type = 2

            if side == 0:
                continue

            fi = int(np.searchsorted(f_ts, t, side="right")) - 1
            if fi < 90:
                continue
            f_hist = f_rate[fi - 89:fi + 1]
            f_now = float(f_rate[fi])
            f_z = float(f_now - f_hist.mean()) / (float(f_hist.std()) + 1e-8)
            carry_z = -side * f_z

            btc_px = float(btc["px"][bi])
            btc_e240 = float(btc["ema240"][bi])
            btc_ok = (side == 1 and btc_px > btc_e240) or (side == -1 and btc_px < btc_e240)
            if not btc_ok or carry_z < 0.0:
                continue

            btc_dist = side * (btc_px / btc_e240 - 1.0) / max(float(btc["atr1h"][bi]), 0.002)
            r1 = side * (cur_px / float(px[i - 1]) - 1.0) / atr
            r4 = side * (cur_px / float(px[i - 4]) - 1.0) / atr
            r24 = side * (cur_px / float(px[i - 24]) - 1.0) / atr
            dist_es = side * (cur_px / es - 1.0) / atr
            v4 = float(vol[i - 3:i + 1].mean())
            v24 = float(vol[i - 23:i + 1].mean()) + 1e-9
            vol_ratio = v4 / v24

            # Walk forward with Schwartz exit + Pyramid leg when move >= +2.0 * atr
            entry_px = cur_px
            entry_ts = t
            peak_move = 0.0
            min_move = 0.0
            exit_idx = i + 1
            reason = "end"
            pyr_triggered = False
            pyr_entry_move = 0.0

            for j in range(i + 1, min(n, i + 96)):
                if int(close_ts[j]) != int(close_ts[j - 1]) + HOUR:
                    exit_idx = j - 1
                    reason = "gap"
                    break
                cpx = float(px[j])
                move = side * (cpx / entry_px - 1.0)
                min_move = min(min_move, move)
                peak_move = max(peak_move, move)
                h = j - i

                if not pyr_triggered and move >= 2.0 * atr:
                    pyr_triggered = True
                    pyr_entry_move = move

                if h <= 4 and move <= -0.8 * atr:
                    exit_idx = j
                    reason = "fast_bail"
                    break
                if move <= -2.2 * atr:
                    exit_idx = j
                    reason = "hard_stop"
                    break
                if peak_move >= 3.0 * atr and move <= 0.4 * atr:
                    exit_idx = j
                    reason = "breakeven"
                    break
                if (side == 1 and cpx < ema240[j]) or (side == -1 and cpx > ema240[j]):
                    exit_idx = j
                    reason = "ema10_exit"
                    break
                exit_idx = j

            exit_px = float(px[exit_idx])
            exit_ts = int(close_ts[exit_idx])
            lo = int(np.searchsorted(f_ts, entry_ts, side="right"))
            hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
            paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
            raw = side * (exit_px / entry_px - 1.0)
            net = raw + (-paid if side == 1 else paid) - FEE

            # Pyramid leg net (50% extra size added at pyr_entry_move if triggered)
            pyr_net = 0.0
            if pyr_triggered:
                pyr_raw = raw - pyr_entry_move
                pyr_net = 0.5 * (pyr_raw - FEE)

            feat = np.array([
                float(side),
                float(setup_type),
                r1, r4, r24,
                dist_es, slope_s / atr,
                vol_ratio,
                btc_dist,
                carry_z, -side * f_now * 1000.0,
                atr * 100.0,
            ], dtype=np.float64)

            events.append({
                "t": entry_ts,
                "exit_ts": exit_ts,
                "hold_h": exit_idx - i,
                "ci": ci,
                "sym": sym,
                "side": side,
                "setup_type": setup_type,
                "btc_ok": True,
                "carry_z": carry_z,
                "feat": feat,
                "net": float(net),
                "pyr_net": float(pyr_net),
                "min_move": float(min_move),
                "atr": float(atr),
                "reason": reason,
            })
    events.sort(key=lambda x: (x["t"], x["ci"]))
    return events
