"""Inspect per-coin performance in Championship Schwartz mode, filter out weak coins,
push the Championship configuration to maximum on clean liquid majors,
save equity curve PNG, and export full trade list + dashboard JSONs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from causal import DAY, HOUR, LAB
from schwartz_boost import collect_active_events
from test_schwartz_hybrid import TRADE_SYMS, Y2024, Y2025, Y2026, Q_CUT
from test_schwartz_ml import walk_forward_reactive
from test_risk_budget import sim_risk_budget


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def sim_championship_max(
    cands: list[dict],
    allowed_syms: set[str] | None = None,
    pred_thr: float = 0.0,
    risk_pct: float = 0.065,
    max_margin_frac: float = 0.55,
    lev: float = 5.0,
    max_conc: int = 2,
    loss_cut: float = 0.45,
    win_boost: float = 1.60,
    pyr_mult: float = 1.35,
):
    use = [e for e in cands if e.get("pred", 0.0) > pred_thr and (allowed_syms is None or e["sym"] in allowed_syms)]
    by_t = {}
    times = set()
    for e in use:
        by_t.setdefault(e["t"], []).append(e)
        times.add(e["t"])
        times.add(e["exit_ts"])

    free = 2000.0
    reserved = 0.0
    open_pos = []
    trades = []
    curve = [(min(times) if times else 0, free)]
    peak = free
    max_dd = 0.0
    loss_streak = 0
    win_streak = 0

    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                tot_net = e["net"] + pyr_mult * e["pyr_net"]
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
                trades.append({
                    "t": int(e["t"]),
                    "exit_ts": int(e["exit_ts"]),
                    "time": iso(int(e["t"])),
                    "exit_time": iso(int(e["exit_ts"])),
                    "hold_h": int(e["hold_h"]),
                    "sym": e["sym"],
                    "side": int(e["side"]),
                    "margin": round(float(p["margin"]), 2),
                    "eff_lev": round(float(p["eff_lev"]), 2),
                    "raw_net_pct": round(float(tot_net) * 100.0, 2),
                    "pnl_usd": round(float(pnl), 2),
                    "pnl_pct_eq": round(float(pnl / p["eq_entry"]) * 100.0, 2),
                    "eq": round(float(eq), 2),
                    "reason": e["reason"],
                    "pyr": bool(e["pyr_net"] != 0.0),
                })
                curve.append((int(t), float(eq)))
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break

        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= max_conc:
            continue
        batch = sorted(batch, key=lambda x: x.get("pred", 0.0) + 0.002 * x["carry_z"], reverse=True)
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= max_conc or e["sym"] in busy:
                continue
            if loss_streak > 0:
                mult = loss_cut ** min(loss_streak, 2)
            elif win_streak >= 2:
                mult = win_boost
            elif win_streak == 1:
                mult = 1.20
            else:
                mult = 1.0

            stop_dist = max(2.2 * e["atr"], 0.012)
            desired_notional = (eq * risk_pct * mult) / stop_dist
            margin = min(free * 0.96, eq * max_margin_frac * mult, desired_notional / lev)
            if margin < 10.0:
                continue
            eff_lev = min(lev, desired_notional / max(margin, 1e-9))
            free -= margin
            reserved += margin
            open_pos.append({
                "e": e,
                "margin": margin,
                "eff_lev": eff_lev,
                "exit_ts": e["exit_ts"],
                "eq_entry": eq,
            })
            busy.add(e["sym"])

    for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
        e = p["e"]
        tot_net = e["net"] + pyr_mult * e["pyr_net"]
        pnl = max(-p["margin"], p["margin"] * p["eff_lev"] * tot_net)
        free += p["margin"] + pnl
        reserved -= p["margin"]
        eq = free + reserved
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        trades.append({
            "t": int(e["t"]),
            "exit_ts": int(e["exit_ts"]),
            "time": iso(int(e["t"])),
            "exit_time": iso(int(e["exit_ts"])),
            "hold_h": int(e["hold_h"]),
            "sym": e["sym"],
            "side": int(e["side"]),
            "margin": round(float(p["margin"]), 2),
            "eff_lev": round(float(p["eff_lev"]), 2),
            "raw_net_pct": round(float(tot_net) * 100.0, 2),
            "pnl_usd": round(float(pnl), 2),
            "pnl_pct_eq": round(float(pnl / p["eq_entry"]) * 100.0, 2),
            "eq": round(float(eq), 2),
            "reason": e["reason"],
            "pyr": bool(e["pyr_net"] != 0.0),
        })
        curve.append((int(e["exit_ts"]), float(eq)))

    final = free + reserved
    return {
        "final": round(final, 2),
        "ret": round((final / 2000.0 - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "n": len(trades),
        "trades": trades,
        "curve": curve,
    }


def main():
    events = collect_active_events(TRADE_SYMS)
    ev48 = [e for e in events if e["setup_type"] == 1]
    scored = walk_forward_reactive(ev48, train_win_days=365)
    pos = [e for e in scored if e["pred"] > 0.0]

    # 1. Per-coin raw edge audit on pre-2025 vs 2025-2026
    print("=== PER-COIN AUDIT (in `model pred > 0`, 9 major pairs) ===", flush=True)
    for sym in TRADE_SYMS:
        c_all = [e for e in pos if e["sym"] == sym]
        c_dev = [e for e in c_all if e["t"] < Y2025]
        c_25 = [e for e in c_all if Y2025 <= e["t"] < Y2026]
        c_26 = [e for e in c_all if e["t"] >= Y2026]
        c_qtr = [e for e in c_all if e["t"] >= Q_CUT]
        m_all = np.mean([e["net"] + e["pyr_net"] for e in c_all]) * 100 if c_all else 0.0
        m_dev = np.mean([e["net"] + e["pyr_net"] for e in c_dev]) * 100 if c_dev else 0.0
        m_25 = np.mean([e["net"] + e["pyr_net"] for e in c_25]) * 100 if c_25 else 0.0
        m_26 = np.mean([e["net"] + e["pyr_net"] for e in c_26]) * 100 if c_26 else 0.0
        m_qtr = np.mean([e["net"] + e["pyr_net"] for e in c_qtr]) * 100 if c_qtr else 0.0
        print(
            f"  {sym:8s} | total n={len(c_all):3d} avg={m_all:+.2f}% | "
            f"<2025({len(c_dev):2d})={m_dev:+.2f}% | 2025({len(c_25):2d})={m_25:+.2f}% | "
            f"2026({len(c_26):2d})={m_26:+.2f}% | QTR({len(c_qtr):2d})={m_qtr:+.2f}%",
            flush=True,
        )

    # Coins that are positive in <2025 (chosen strictly pre-2025!)
    good_pre25 = set()
    for sym in TRADE_SYMS:
        c_dev = [e for e in pos if e["sym"] == sym and e["t"] < Y2025]
        if c_dev and np.mean([e["net"] + e["pyr_net"] for e in c_dev]) > 0:
            good_pre25.add(sym)
    print(f"\nCoins strictly positive pre-2025: {sorted(good_pre25)}", flush=True)

    for label_u, syms_u in (
        ("ALL_9_MAJORS", set(TRADE_SYMS)),
        ("PRE2025_WINNERS_ONLY", good_pre25),
    ):
        print(f"\n================ {label_u}: {sorted(syms_u)} ================", flush=True)
        for cfg_name, r_pct, m_frac, lv, mc, w_b, p_m in (
            ("Mode 5 Base (Risk 5.5%, 5x, mc=3)", 0.055, 0.50, 5.0, 3, 1.50, 1.0),
            ("Max Pitbull 6x (Risk 6.5%, 6x, mc=2, pyr=1.4x)", 0.065, 0.55, 6.0, 2, 1.65, 1.4),
            ("Ultra Championship 7x (Risk 7.5%, 7x, mc=2, pyr=1.5x)", 0.075, 0.60, 7.0, 2, 1.75, 1.5),
        ):
            print(f"--- {cfg_name} ---", flush=True)
            for lbl, lo, hi in (
                ("FULL", 0, 10**18),
                ("<2025", 0, Y2025),
                ("2025", Y2025, Y2026),
                ("2026", Y2026, 10**18),
                ("QTR", Q_CUT, 10**18),
            ):
                part = [dict(e) for e in pos if lo <= e["t"] < hi]
                res = sim_championship_max(
                    part,
                    allowed_syms=syms_u,
                    risk_pct=r_pct,
                    max_margin_frac=m_frac,
                    lev=lv,
                    max_conc=mc,
                    loss_cut=0.45,
                    win_boost=w_b,
                    pyr_mult=p_m,
                )
                print(
                    f"  {lbl:5s} n={res['n']:3d} ret={res['ret']:+8.2f}% dd={res['dd']:5.1f}% final=${res['final']:,.2f}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
