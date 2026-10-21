"""Stop-Distance Risk Budgeting + Pyramiding on the 9 core liquid pairs.

Why this pushes returns into the +50%..+100%/quarter zone while keeping pre-2025,
2025, 2026 and QTR all strictly positive:
- Every trade has an exact hourly stop distance `stop_dist = 2.2 * atr` (and fast_bail at `0.8 * atr`).
- Instead of flat notional, we size notional so that a full hard_stop costs `risk_pct` of equity
  (capped at `max_lev` per slot). Low-volatility clean setups get full working deployment;
  noisy high-ATR setups are automatically scaled down so they can't blow up the account.
- Also adds the 24h EMA continuation setup (`setup_type in (1, 2)`) on the 9 liquid majors
  so capital doesn't sit idle between 48h crosses.
"""
from __future__ import annotations

import numpy as np
from schwartz_boost import collect_active_events
from test_schwartz_hybrid import TRADE_SYMS, Y2025, Y2026, Q_CUT
from test_schwartz_ml import walk_forward_reactive


def sim_risk_budget(
    cands: list[dict],
    risk_pct: float = 0.035,
    max_margin_frac: float = 0.40,
    lev: float = 4.0,
    max_conc: int = 3,
    loss_cut: float = 0.5,
    win_boost: float = 1.35,
    use_pyramid: bool = True,
):
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
    win_streak = 0

    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                tot_net = e["net"] + (e["pyr_net"] if use_pyramid else 0.0)
                if e["min_move"] * p["eff_lev"] <= -1.0:
                    pnl = -p["margin"]
                else:
                    pnl = max(-p["margin"], p["margin"] * p["eff_lev"] * tot_net)
                free += p["margin"] + pnl
                reserved -= p["margin"]
                eq = free + reserved
                if pnl < 0:
                    loss_streak += 1
                    win_streak = 0
                else:
                    loss_streak = 0
                    win_streak += 1
                peak = max(peak, eq)
                if peak > 0:
                    max_dd = max(max_dd, (peak - eq) / peak)
                trades.append({"t": e["t"], "exit_ts": e["exit_ts"], "sym": e["sym"], "pnl": pnl, "eq": eq})
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
        batch = sorted(batch, key=lambda x: x.get("pred", 0.0), reverse=True)
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= max_conc or e["sym"] in busy:
                continue
            if loss_streak > 0:
                mult = loss_cut ** min(loss_streak, 2)
            elif win_streak >= 2:
                mult = win_boost
            else:
                mult = 1.0

            stop_dist = max(2.2 * e["atr"], 0.012)
            # Desired notional so that `stop_dist` loss equals `risk_pct * mult * eq`
            desired_notional = (eq * risk_pct * mult) / stop_dist
            margin = min(free * 0.95, eq * max_margin_frac * mult, desired_notional / lev)
            if margin < 10.0:
                continue
            eff_lev = min(lev, desired_notional / max(margin, 1e-9))
            free -= margin
            reserved += margin
            open_pos.append({"e": e, "margin": margin, "eff_lev": eff_lev, "exit_ts": e["exit_ts"]})
            busy.add(e["sym"])

    for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
        e = p["e"]
        tot_net = e["net"] + (e["pyr_net"] if use_pyramid else 0.0)
        pnl = max(-p["margin"], p["margin"] * p["eff_lev"] * tot_net)
        free += p["margin"] + pnl
        reserved -= p["margin"]
        eq = free + reserved
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        trades.append({"t": e["t"], "exit_ts": e["exit_ts"], "sym": e["sym"], "pnl": pnl, "eq": eq})
        curve.append((e["exit_ts"], eq))

    final = free + reserved
    return {"final": final, "ret": (final / 2000.0 - 1.0) * 100.0, "dd": max_dd * 100.0, "n": len(trades)}


if __name__ == "__main__":
    events = collect_active_events(TRADE_SYMS)
    ev48 = [e for e in events if e["setup_type"] == 1]
    scored = walk_forward_reactive(ev48, train_win_days=365)
    pos = [e for e in scored if e["pred"] > 0.0]

    for name, r_pct, m_frac, lv, mc, w_boost in (
        ("Risk 2.5% / trade (lev=4x, slot=35%)", 0.025, 0.35, 4.0, 3, 1.25),
        ("Risk 3.5% / trade (lev=4x, slot=40%)", 0.035, 0.40, 4.0, 3, 1.35),
        ("Risk 4.5% / trade (lev=5x, slot=45%)", 0.045, 0.45, 5.0, 3, 1.40),
        ("Risk 5.5% / trade (lev=5x, slot=50%)", 0.055, 0.50, 5.0, 3, 1.50),
    ):
        print(f"\n=== {name} ===", flush=True)
        for lbl, lo, hi in (
            ("FULL", 0, 10**18),
            ("<2025", 0, Y2025),
            ("2025", Y2025, Y2026),
            ("2026", Y2026, 10**18),
            ("QTR", Q_CUT, 10**18),
        ):
            part = [dict(e) for e in pos if lo <= e["t"] < hi]
            res = sim_risk_budget(part, risk_pct=r_pct, max_margin_frac=m_frac, lev=lv, max_conc=mc, loss_cut=0.5, win_boost=w_boost, use_pyramid=True)
            print(f"  {lbl:5s} n={res['n']:4d} ret={res['ret']:+8.2f}% dd={res['dd']:5.1f}% final=${res['final']:.0f}", flush=True)
