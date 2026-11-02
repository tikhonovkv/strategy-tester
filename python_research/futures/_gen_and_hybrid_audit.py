# -*- coding: utf-8 -*-
"""Audit of Variant 2 (Generator expansion, Side Asymmetry, Coil Breakout, 25 Majors)
and Variant 3 (Hybrid Core + Carry/Momentum Sleeve: Split & Idle Cash Overlay).
Strictly causal, real costs (0.11% taker + 3 bps slippage). Local only.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

SLIP = 0.0003
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
OUT_JSON = sp.LAB / "_gen_and_hybrid_audit.json"
OUT_CURVES = sp.LAB / "_gen_and_hybrid_curves.json"


def stamp(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


WINDOWS = [
    ("2022", stamp(2022, 1, 1), stamp(2023, 1, 1)),
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, END),
    ("full", stamp(2022, 1, 1), END),
]


def load_all_series() -> dict:
    series = st._load_universe()
    for sym, idx in enumerate(series.keys()):
        sp.COIN_IDX.setdefault(idx, sym)
    return series


def walk_schwartz_exit(s: dict, i: int, side: int) -> dict | None:
    """Single-exit Schwartz trade from close_ts[i] with 0.11% fee + 3 bps slip per leg."""
    px = s["px"]
    close_ts = s["close_ts"]
    ema240 = s["ema240"]
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    n = len(px)
    if i < 240 or i >= n - 2:
        return None
    entry_raw = float(px[i])
    entry_px = entry_raw * (1.0 + side * SLIP)
    atr = max(float(s["atr1h"][i]), 0.002)
    peak_move = 0.0
    min_move = 0.0
    exit_idx = i + 1
    reason = "end"
    pyr_triggered = False
    pyr_entry_move = 0.0
    for j in range(i + 1, min(n, i + 96)):
        if int(close_ts[j]) != int(close_ts[j - 1]) + sp.HOUR:
            exit_idx = j - 1
            reason = "gap"
            break
        cpx = float(px[j])
        move = side * (cpx / entry_raw - 1.0)
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
        be_floor = 1.15 * atr if pyr_triggered else 0.4 * atr
        if peak_move >= 3.0 * atr and move <= be_floor:
            exit_idx = j
            reason = "breakeven"
            break
        if (side == 1 and cpx < float(ema240[j])) or (side == -1 and cpx > float(ema240[j])):
            exit_idx = j
            reason = "ema10_exit"
            break
        exit_idx = j
    if exit_idx <= i:
        return None
    exit_raw = float(px[exit_idx])
    exit_px = exit_raw * (1.0 - side * SLIP)
    t_ms = int(close_ts[i])
    exit_ts = int(close_ts[exit_idx])
    lo = int(np.searchsorted(f_ts, t_ms, side="right"))
    hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
    f_adj = -paid if side == 1 else paid
    raw_end = side * (exit_px / entry_px - 1.0)
    net = raw_end + f_adj - sp.TAKER_FEE
    if pyr_triggered:
        pyr_net = 0.50 * ((raw_end - pyr_entry_move - SLIP) + 0.5 * f_adj - sp.TAKER_FEE)
    else:
        pyr_net = 0.0
    return {
        "t": t_ms,
        "exit_ts": exit_ts,
        "hold_h": int(exit_idx - i),
        "sym": s["sym"],
        "coin": sp.SHORT_NAME.get(s["sym"], s["sym"].replace("USDT", "")),
        "ci": sp.COIN_IDX.get(s["sym"], 0),
        "side": int(side),
        "entry_px": round(entry_raw, 6),
        "exit_px": round(exit_raw, 6),
        "net": float(net),
        "pyr_net": float(pyr_net),
        "min_move": float(min_move),
        "atr": float(atr),
        "reason": reason,
        "pyr": bool(pyr_triggered),
        "path": [],
    }


def add_causal_meta(cands: list[dict], series: dict, syms: list[str]) -> list[dict]:
    """Attach hot_n, hot_avg, c_lstreak, vol_surge, rs24, rank24 to each candidate."""
    idx_map = {sym: {int(t): i for i, t in enumerate(series[sym]["close_ts"])} for sym in syms if sym in series}
    sig_hist = {sym: deque(maxlen=6) for sym in syms}
    sig_lstreak = {sym: 0 for sym in syms}
    pend = []
    out = []
    for e in sorted(cands, key=lambda x: (x["t"], x["sym"])):
        t = int(e["t"])
        sym = e["sym"]
        still = []
        for ex_t, sym_p, tot_p in pend:
            if ex_t <= t:
                sig_hist[sym_p].append(tot_p)
                if tot_p < 0:
                    sig_lstreak[sym_p] += 1
                else:
                    sig_lstreak[sym_p] = 0
            else:
                still.append((ex_t, sym_p, tot_p))
        pend = still
        ch = sig_hist[sym]
        row = dict(e)
        row["hot_n"] = len(ch)
        row["hot_avg"] = float(np.mean(ch)) if len(ch) > 0 else 0.0
        row["c_lstreak"] = sig_lstreak[sym]

        s = series[sym]
        i = idx_map[sym].get(t)
        side = int(e["side"])
        if i is not None and i >= 24:
            vol = s["vol"]
            base_v = float(np.median(vol[i - 24:i]))
            row["vol_surge"] = float(vol[i]) / max(base_v, 1e-8)
            r24_self = side * (float(s["px"][i]) / max(float(s["px"][i - 24]), 1e-12) - 1.0)
            row["r24_dir"] = r24_self
        else:
            row["vol_surge"] = 1.0
            row["r24_dir"] = 0.0

        # Rank across universe at timestamp t in signal direction
        peers = []
        for psym in syms:
            if psym not in idx_map:
                continue
            pi = idx_map[psym].get(t)
            if pi is None or pi < 24:
                continue
            ps = series[psym]
            pr = side * (float(ps["px"][pi]) / max(float(ps["px"][pi - 24]), 1e-12) - 1.0)
            peers.append((psym, pr))
        peers.sort(key=lambda x: x[1], reverse=True)
        rank = next((k + 1 for k, (psym, _v) in enumerate(peers) if psym == sym), len(peers) + 1)
        row["rank24"] = rank
        pend.append((row["exit_ts"], sym, row["net"] + 1.5 * row["pyr_net"]))
        out.append(row)
    return out


def generate_coil_and_reclaim(series: dict, syms: list[str], include_coil: bool = True) -> list[dict]:
    """Generate classic 48h EMA reclaim + 24h Donchian coil breakout along 10d EMA."""
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    cands = []
    for sym in syms:
        if sym not in series:
            continue
        s = series[sym]
        close_ts, px, vol, atr1h, ema240, f_ts, f_rate = (
            s["close_ts"], s["px"], s["vol"], s["atr1h"], s["ema240"], s["f_ts"], s["f_rate"]
        )
        n = len(px)
        if n < 265:
            continue
        ema48 = st._ema(px, 48)
        last_entry_i = -999
        for i in range(240, n - 2):
            t_ms = int(close_ts[i])
            bi = btc_map.get(t_ms)
            if bi is None or bi < 240:
                continue
            cur_px, prev_px = float(px[i]), float(px[i - 1])
            es = float(ema240[i])
            if es <= 0 or float(ema240[i - 24]) <= 0:
                continue
            slope_s = es / float(ema240[i - 24]) - 1.0
            atr = max(float(atr1h[i]), 0.002)

            side = 0
            trig = ""
            # Trigger 1: Classic 48h EMA reclaim
            if cur_px > es and slope_s > 0 and ema48[i] > es and prev_px <= ema48[i - 1] and cur_px > ema48[i]:
                side = 1
                trig = "reclaim"
            elif cur_px < es and slope_s < 0 and ema48[i] < es and prev_px >= ema48[i - 1] and cur_px < ema48[i]:
                side = -1
                trig = "reclaim"
            # Trigger 2: 12h Coil + 24h Breakout along trend (when not overlapping recent signal)
            elif include_coil and (i - last_entry_i) >= 12:
                win12 = px[i - 12:i]
                win24 = px[i - 24:i]
                coil_w = (float(np.max(win12)) - float(np.min(win12))) / max(cur_px * atr, 1e-8)
                base_v = float(np.median(vol[i - 24:i]))
                v_surge = float(vol[i]) / max(base_v, 1e-8)
                dist_e = abs(cur_px / es - 1.0) / atr
                if coil_w <= 5.5 and v_surge >= 1.6 and dist_e <= 6.0:
                    if cur_px > es and slope_s > 0 and cur_px > ema48[i] and ema48[i] > es and cur_px > float(np.max(win24)):
                        side = 1
                        trig = "coil_brk"
                    elif cur_px < es and slope_s < 0 and cur_px < ema48[i] and ema48[i] < es and cur_px < float(np.min(win24)):
                        side = -1
                        trig = "coil_brk"
            if side == 0:
                continue
            fi = int(np.searchsorted(f_ts, t_ms, side="right")) - 1
            if fi < 90:
                continue
            f_hist = f_rate[fi - 89:fi + 1]
            f_z = float(f_rate[fi] - f_hist.mean()) / (float(f_hist.std()) + 1e-8)
            carry_z = -side * f_z
            btc_px, btc_e240 = float(btc["px"][bi]), float(btc["ema240"][bi])
            btc_ok = (side == 1 and btc_px > btc_e240) or (side == -1 and btc_px < btc_e240)
            if not btc_ok or carry_z < 0.0:
                continue
            tr = walk_schwartz_exit(s, i, side)
            if tr is None:
                continue
            tr["trig"] = trig
            tr["carry_z"] = float(carry_z)
            cands.append(tr)
            last_entry_i = i
    return add_causal_meta(cands, series, syms)


def build_carry_mom_sleeve(series: dict, syms: list[str], hold_h: int = 24, leverage: float = 2.0) -> dict[int, float]:
    """Build causal 24h return stream for the Cross-Sectional Momentum (168h) + Funding Carry (21) sleeve."""
    btc = series["BTCUSDT"]
    ts_arr = btc["close_ts"]
    idx_map = {sym: {int(t): i for i, t in enumerate(series[sym]["close_ts"])} for sym in syms if sym in series}
    step_rets = {}
    for k in range(400, len(ts_arr) - hold_h, hold_h):
        t = int(ts_arr[k])
        t_next = int(ts_arr[k + hold_h])
        if t_next != t + hold_h * sp.HOUR:
            continue
        scores = []
        for sym in syms:
            if sym not in idx_map:
                continue
            i = idx_map[sym].get(t)
            i_next = idx_map[sym].get(t_next)
            if i is None or i_next is None or i < 180:
                continue
            s = series[sym]
            px_now = float(s["px"][i])
            px_168 = float(s["px"][i - 168])
            atr = max(float(s["atr1h"][i]), 0.002)
            if px_168 <= 0 or px_now <= 0:
                continue
            mom = (px_now / px_168 - 1.0) / (atr * np.sqrt(168.0))
            fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
            if fi < 25:
                continue
            carry_21 = -float(np.mean(s["f_rate"][fi - 20:fi + 1])) * 1000.0
            # Net 24h forward return of holding 1 unit of sym
            px_end = float(s["px"][i_next])
            raw_r = px_end / px_now - 1.0
            lo = int(np.searchsorted(s["f_ts"], t, side="right"))
            hi = int(np.searchsorted(s["f_ts"], t_next, side="right"))
            paid = float(s["f_rate"][lo:hi].sum()) if hi > lo else 0.0
            scores.append((sym, mom, carry_21, raw_r, paid))
        if len(scores) < 6:
            continue
        moms = np.array([x[1] for x in scores])
        carries = np.array([x[2] for x in scores])
        mz = (moms - moms.mean()) / (moms.std() + 1e-8)
        cz = (carries - carries.mean()) / (carries.std() + 1e-8)
        combo = 0.70 * mz + 0.30 * cz
        order = np.argsort(combo)
        k_leg = min(3, len(scores) // 3)
        shorts = order[:k_leg]
        longs = order[-k_leg:]
        # BTC 60d regime check for momentum direction; carry always active
        b_ma = float(np.mean(btc["px"][max(0, k - 24 * 60):k + 1]))
        b_now = float(btc["px"][k])
        mom_on = b_now > b_ma
        basket_ret = 0.0
        for idx_s in longs:
            _sym, _m, _c, raw_r, paid = scores[int(idx_s)]
            w = 1.0 if mom_on else 0.4
            basket_ret += w * (raw_r - paid - sp.TAKER_FEE - SLIP)
        for idx_s in shorts:
            _sym, _m, _c, raw_r, paid = scores[int(idx_s)]
            w = 1.0 if not mom_on else 0.6
            basket_ret += w * (-raw_r + paid - sp.TAKER_FEE - SLIP)
        basket_ret /= float(2 * k_leg)
        step_rets[t] = float(np.clip(basket_ret * leverage, -0.25, 0.40))
    return step_rets


def simulate_custom(
    part: list[dict],
    start_ts: int,
    end_ts: int,
    side_asym: str = "none",
    tie_break: str = "default",
    core_syms: set[str] | None = None,
    sleeve_rets: dict[int, float] | None = None,
    mode: str = "pure",
    split_schwartz: float = 0.65,
) -> dict:
    """Unified simulator for pure Schwartz, Variant 2 filters, and Variant 3 hybrids."""
    if core_syms is None:
        core_syms = sp.CORE_4
    by_t = {}
    times = set()
    for e in part:
        if not (start_ts <= int(e["t"]) < end_ts):
            continue
        sym = e["sym"]
        side = int(e["side"])
        if side_asym == "eth_btc_top2" and side == 1:
            if sym not in {"BTCUSDT", "ETHUSDT"} and int(e.get("rank24", 99)) > 2:
                continue
        elif side_asym == "eth_btc_only" and side == 1:
            if sym not in {"BTCUSDT", "ETHUSDT"}:
                continue
        if sym not in core_syms:
            # Allow non-core either when proven hot OR when top-1 leader with strong volume
            hot_ok = int(e.get("hot_n", 0)) >= 4 and float(e.get("hot_avg", 0.0)) >= 0.015
            leader_ok = int(e.get("rank24", 99)) == 1 and float(e.get("vol_surge", 1.0)) >= 1.8
            if not (hot_ok or leader_ok):
                continue
        by_t.setdefault(int(e["t"]), []).append(e)
        times.add(int(e["t"]))
        times.add(int(e["exit_ts"]))

    if sleeve_rets and mode in ("split", "idle"):
        for st_t in sleeve_rets:
            if start_ts <= st_t < end_ts:
                times.add(st_t)

    if mode == "split":
        free_s = 2000.0 * split_schwartz
        eq_sleeve = 2000.0 * (1.0 - split_schwartz)
    else:
        free_s = 2000.0
        eq_sleeve = 0.0

    reserved_s = 0.0
    open_pos = []
    trades = []
    peak = 2000.0
    max_dd = 0.0
    loss_streak = 0
    win_streak = 0
    curve = [(start_ts, 2000.0)]

    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                tot_net = e["net"] + p["pyr_scale"] * e["pyr_net"]
                if e["min_move"] * p["eff_lev"] <= -1.0:
                    pnl = -p["margin"]
                else:
                    pnl = max(-p["margin"], p["margin"] * p["eff_lev"] * tot_net)
                free_s += p["margin"] + pnl
                reserved_s -= p["margin"]
                if pnl < 0:
                    loss_streak += 1
                    win_streak = 0
                else:
                    loss_streak = 0
                    win_streak += 1
                trades.append(float(pnl))
            else:
                still.append(p)
        open_pos = still

        # Apply sleeve return at 24h checkpoints
        if sleeve_rets and t in sleeve_rets:
            s_ret = sleeve_rets[t]
            if mode == "split":
                eq_sleeve *= (1.0 + s_ret)
            elif mode == "idle":
                # Park only unreserved free cash when <= 1 Schwartz slot is active
                if len(open_pos) < 2 and free_s > 50.0:
                    deploy = free_s * (0.60 if len(open_pos) == 0 else 0.35)
                    pnl_idle = deploy * s_ret
                    free_s += pnl_idle
                    trades.append(float(pnl_idle))

        total_eq = free_s + reserved_s + eq_sleeve
        if total_eq <= 0:
            break
        peak = max(peak, total_eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - total_eq) / peak)
        curve.append((int(t), round(float(total_eq), 2)))

        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= 2:
            continue
        if tie_break == "vol_surge":
            batch = sorted(
                batch,
                key=lambda x: (1 if x["sym"] in core_syms else 0, float(x.get("vol_surge", 1.0)) + 5.0 * float(x.get("hot_avg", 0.0))),
                reverse=True,
            )
        else:
            batch = sorted(batch, key=lambda x: (1 if x["sym"] in core_syms else 0, float(x.get("hot_avg", 0.0))), reverse=True)

        busy = {p["e"]["sym"] for p in open_pos}
        eq_s = free_s + reserved_s
        for e in batch:
            if len(open_pos) >= 2 or e["sym"] in busy:
                continue
            mult = sp.size_multiplier(
                loss_streak,
                win_streak,
                int(e.get("c_lstreak") or 0),
                float(e.get("hot_avg") or 0.0),
                win_cap=1.15,
            )
            stop_dist = max(1.6 * e["atr"], 0.008)
            target_notional = (eq_s * 0.075 * mult) / stop_dist
            raw_margin = min(target_notional / 7.0, eq_s * 0.60, free_s * 0.90)
            if raw_margin < 15.0:
                continue
            qty = sp.fit_lot_qty(e["sym"], (raw_margin * 7.0) / max(e["entry_px"], 1e-8))
            if qty <= 0:
                continue
            margin = (qty * e["entry_px"]) / 7.0
            if margin < 15.0 or margin > free_s:
                continue
            free_s -= margin
            reserved_s += margin
            rem_after = max(0.0, free_s)
            need_pyr = 0.50 * margin
            if e["pyr"] and rem_after >= 10.0:
                pyr_scale = min(1.0, (rem_after * 0.90) / max(need_pyr, 1e-8))
            else:
                pyr_scale = 0.0
            open_pos.append({
                "e": e,
                "exit_ts": int(e["exit_ts"]),
                "margin": margin,
                "pyr_scale": pyr_scale,
                "eff_lev": 7.0,
            })
            busy.add(e["sym"])

    final = free_s + reserved_s + eq_sleeve
    wins = sorted((x for x in trades if x > 0), reverse=True)
    win_sum = sum(wins) or 1.0
    dd = max_dd * 100.0
    ret = (final / 2000.0 - 1.0) * 100.0
    return {
        "n": len(trades),
        "ret": round(ret, 2),
        "dd": round(dd, 1),
        "final": round(final, 2),
        "calmar": round(ret / dd, 2) if dd > 0 else None,
        "top5": round(sum(wins[:5]) / win_sum, 3),
        "curve": curve,
    }


def evaluate_all() -> None:
    series = load_all_series()
    majors_syms = [s for s in series.keys() if s != "BTCUSDT"]
    alts7 = sp.ALTS_7

    # 1. Base candidates (exact same 790 candidates as schwartz_production, with 3 bps slip per leg)
    base_cands_raw, _ = sp.build_tranche_candidates()
    base_cands = []
    for e in base_cands_raw:
        r = dict(e)
        r["net"] = float(e["net"]) - 2.0 * SLIP
        r["pyr_net"] = float(e.get("pyr_net") or 0.0) - SLIP if e.get("pyr") else 0.0
        base_cands.append(r)
    base_cands = add_causal_meta(base_cands, series, alts7)

    # 2. Expanded generator on 7 alts (Reclaim + Coil Breakout)
    coil7_cands = generate_coil_and_reclaim(series, alts7, include_coil=True)

    # 3. Expanded generator on 25 Majors (Reclaim + Coil Breakout)
    majors_reclaim = generate_coil_and_reclaim(series, majors_syms, include_coil=False)
    majors_coil = generate_coil_and_reclaim(series, majors_syms, include_coil=True)

    # 4. Carry + Cross-Sectional Momentum 168h sleeve across all available majors
    sleeve_rets = build_carry_mom_sleeve(series, list(series.keys()), hold_h=24, leverage=2.0)

    configs = [
        ("V1_base", base_cands, {"side_asym": "none", "tie_break": "default", "mode": "pure"}),
        ("G1_asym_eth_only", base_cands, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "mode": "pure"}),
        ("G1b_asym_top2", base_cands, {"side_asym": "eth_btc_top2", "tie_break": "vol_surge", "mode": "pure"}),
        ("G2_coil_7alts", coil7_cands, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "mode": "pure"}),
        ("G3_majors25_reclaim", majors_reclaim, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "mode": "pure"}),
        ("G3b_majors25_coil", majors_coil, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "mode": "pure"}),
        ("H1_split_65_35", base_cands, {"side_asym": "none", "tie_break": "default", "sleeve_rets": sleeve_rets, "mode": "split", "split_schwartz": 0.65}),
        ("H2_idle_overlay", base_cands, {"side_asym": "none", "tie_break": "default", "sleeve_rets": sleeve_rets, "mode": "idle"}),
        ("H3_combo_asym_idle", base_cands, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "sleeve_rets": sleeve_rets, "mode": "idle"}),
        ("H4_combo_asym_split", base_cands, {"side_asym": "eth_btc_only", "tie_break": "vol_surge", "sleeve_rets": sleeve_rets, "mode": "split", "split_schwartz": 0.65}),
    ]

    report = {}
    curves = {}
    for name, cands, kw in configs:
        report[name] = {}
        curves[name] = {}
        for label, a, b in WINDOWS:
            res = simulate_custom(cands, a, b, **kw)
            curves[name][label] = res.pop("curve")
            report[name][label] = res
        f = report[name]["full"]
        print(
            f"{name:20} | 2022:{report[name]['2022']['ret']:6.1f}% | 2023:{report[name]['2023']['ret']:6.1f}% | "
            f"2024:{report[name]['2024']['ret']:6.1f}% | 2025:{report[name]['2025']['ret']:6.1f}% | "
            f"2026H1:{report[name]['2026H1']['ret']:6.1f}% | 2026Q:{report[name]['2026Q']['ret']:6.1f}% | "
            f"FULL:${f['final']:9.0f} DD:{f['dd']:4.1f}% top5:{f['top5']:.3f}",
            flush=True,
        )

    OUT_JSON.write_text(json.dumps({"report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_CURVES.write_text(json.dumps(curves), encoding="utf-8")
    print("wrote", OUT_JSON)


if __name__ == "__main__":
    evaluate_all()
