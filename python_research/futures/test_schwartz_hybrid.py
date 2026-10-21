"""Combine Schwartz's 2 core filters:
1. 10-day EMA traffic light (Long > 10d EMA, Short < 10d EMA) + instant cut if hourly close breaks 10d EMA or hard stop.
2. Sentiment/Funding confirmation (enter when funding is NOT crowded in our direction, i.e., carry_z >= thr, or when pullback bounces off fast EMA).
3. Test daily-retrained ML model on top of this exact candidate stream vs pure rule.
"""
from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from causal import DAY, HOUR, FEE
from schwartz_pitbull import FOUR_SYMS, TRADE_SYMS, build_hourly_series

Y2024 = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
Y2025 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
Y2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
Q_CUT = int(datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp() * 1000)


def collect_events(syms: list[str], ema_fast_h: int = 48, ema_slow_h: int = 240):
    series = {sym: build_hourly_series(sym) for sym in syms}
    btc = series.get("BTCUSDT") or build_hourly_series("BTCUSDT")
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}

    events = []
    for ci, sym in enumerate(syms):
        s = series[sym]
        close_ts = s["close_ts"]
        px = s["px"]
        vol = s["vol"]
        atr1h = s["atr1h"]
        f_ts = s["f_ts"]
        f_rate = s["f_rate"]

        n = len(px)
        ef = np.zeros(n, dtype=np.float64)
        es = np.zeros(n, dtype=np.float64)
        af = 2.0 / (ema_fast_h + 1.0)
        asl = 2.0 / (ema_slow_h + 1.0)
        ef[0] = px[0]
        es[0] = px[0]
        for i in range(1, n):
            ef[i] = af * px[i] + (1.0 - af) * ef[i - 1]
            es[i] = asl * px[i] + (1.0 - asl) * es[i - 1]

        for i in range(260, n - 96):
            t = int(close_ts[i])
            bi = btc_map.get(t)
            if bi is None or bi < 240:
                continue

            cur_px = float(px[i])
            prev_px = float(px[i - 1])
            atr = max(float(atr1h[i]), 0.002)
            slope_s = es[i] / es[i - 24] - 1.0

            side = 0
            if cur_px > es[i] and ef[i] > es[i] and slope_s > 0 and prev_px <= ef[i - 1] and cur_px > ef[i]:
                side = 1
            elif cur_px < es[i] and ef[i] < es[i] and slope_s < 0 and prev_px >= ef[i - 1] and cur_px < ef[i]:
                side = -1
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
            btc_dist = side * (btc_px / btc_e240 - 1.0) / max(float(btc["atr1h"][bi]), 0.002)

            r1 = side * (cur_px / float(px[i - 1]) - 1.0) / atr
            r4 = side * (cur_px / float(px[i - 4]) - 1.0) / atr
            r24 = side * (cur_px / float(px[i - 24]) - 1.0) / atr
            dist_es = side * (cur_px / es[i] - 1.0) / atr
            v4 = float(vol[i - 3:i + 1].mean())
            v24 = float(vol[i - 23:i + 1].mean()) + 1e-9
            vol_ratio = v4 / v24

            # Walk forward with Schwartz exit
            entry_px = cur_px
            entry_ts = t
            peak_move = 0.0
            min_move = 0.0
            exit_idx = i + 1
            reason = "end"
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
                if (side == 1 and cpx < es[j]) or (side == -1 and cpx > es[j]):
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

            feat = np.array([
                float(side),
                r1, r4, r24,
                dist_es, slope_s / atr,
                vol_ratio,
                btc_dist, float(btc_ok),
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
                "btc_ok": btc_ok,
                "carry_z": carry_z,
                "vol_ratio": vol_ratio,
                "dist_es": dist_es,
                "feat": feat,
                "net": float(net),
                "min_move": float(min_move),
                "reason": reason,
            })
    events.sort(key=lambda x: (x["t"], x["ci"]))
    return events


def sim_portfolio(cands: list[dict], stake: float = 0.2, lev: float = 1.5, max_conc: int = 2, loss_cut: float = 0.5):
    """Causal portfolio simulator with Schwartz anti-martingale sizing after losses."""
    by_t = {}
    times = set()
    for e in cands:
        by_t.setdefault(e["t"], []).append(e)
        times.add(e["t"])
        times.add(e["exit_ts"])

    free = 2000.0
    reserved = 0.0
    open_pos = []
    trades = []
    curve = []
    peak = free
    max_dd = 0.0
    loss_streak = 0

    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                if e["min_move"] * lev <= -1.0:
                    pnl = -p["margin"]
                else:
                    pnl = max(-p["margin"], p["margin"] * lev * e["net"])
                free += p["margin"] + pnl
                reserved -= p["margin"]
                eq = free + reserved
                if pnl < 0:
                    loss_streak += 1
                else:
                    loss_streak = 0
                peak = max(peak, eq)
                if peak > 0:
                    max_dd = max(max_dd, (peak - eq) / peak)
                trades.append({"t": e["t"], "exit_ts": e["exit_ts"], "sym": e["sym"], "side": e["side"], "pnl": pnl, "eq": eq, "net": e["net"], "reason": e["reason"]})
                curve.append((t, eq))
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break

        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= max_conc:
            continue
        batch = sorted(batch, key=lambda x: x.get("score", x["carry_z"]), reverse=True)
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= max_conc or e["sym"] in busy:
                continue
            # Schwartz anti-martingale: cut stake after losses
            mult = (loss_cut ** min(loss_streak, 2)) if loss_streak > 0 else 1.0
            margin = eq * stake * mult
            if free < margin or margin <= 1.0:
                continue
            free -= margin
            reserved += margin
            open_pos.append({"e": e, "margin": margin, "exit_ts": e["exit_ts"]})
            busy.add(e["sym"])

    for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
        e = p["e"]
        pnl = max(-p["margin"], p["margin"] * lev * e["net"])
        free += p["margin"] + pnl
        reserved -= p["margin"]
        eq = free + reserved
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        trades.append({"t": e["t"], "exit_ts": e["exit_ts"], "sym": e["sym"], "side": e["side"], "pnl": pnl, "eq": eq, "net": e["net"], "reason": e["reason"]})
        curve.append((e["exit_ts"], eq))

    final = free + reserved
    return {"final": final, "ret": (final / 2000.0 - 1.0) * 100.0, "dd": max_dd * 100.0, "n": len(trades), "trades": trades, "curve": curve}


if __name__ == "__main__":
    events = collect_events(TRADE_SYMS, ema_fast_h=48, ema_slow_h=240)
    print(f"total raw trend+bounce events={len(events)}")
    for name, fn in (
        ("btc_aligned", lambda e: e["btc_ok"]),
        ("btc + carry_z>0", lambda e: e["btc_ok"] and e["carry_z"] > 0.0),
        ("btc + carry_z>0.5", lambda e: e["btc_ok"] and e["carry_z"] > 0.5),
        ("btc + carry_z>1.0", lambda e: e["btc_ok"] and e["carry_z"] > 1.0),
        ("carry_z>0.5 any_btc", lambda e: e["carry_z"] > 0.5),
        ("carry_z>1.0 any_btc", lambda e: e["carry_z"] > 1.0),
    ):
        sub = [e for e in events if fn(e)]
        print(f"=== {name} (total={len(sub)}) ===")
        for lbl, lo, hi in (("<2025", 0, Y2025), ("2025", Y2025, Y2026), ("2026", Y2026, 10**18), ("QTR", Q_CUT, 10**18)):
            part = [dict(e) for e in sub if lo <= e["t"] < hi]
            if not part:
                continue
            res = sim_portfolio(part, stake=0.25, lev=2.0, max_conc=2, loss_cut=0.5)
            print(f"  {lbl:5s} n={res['n']:3d} ret={res['ret']:+6.2f}% dd={res['dd']:5.1f}% final=${res['final']:.0f}")
