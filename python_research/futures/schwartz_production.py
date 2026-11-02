# -*- coding: utf-8 -*-
"""Marty Schwartz ('Pitbull') Championship Engine + Live Verification Exporter.
Every trade has ONE entry price and ONE exit price (single-exit ratcheting stop):
  - Fast Bail: if held <= 4h and move <= -0.8 * ATR -> exit immediately.
  - Hard Stop: if move <= -2.2 * ATR -> exit immediately.
  - Ratchet 1: once peak_move >= +2.2 * ATR, stop ratchets to +0.55 * ATR above entry
    (and +0.25 * ATR above pyramid entry if pyramid triggered), so the exit price is
    ALWAYS in profit vs entry!
  - Ratchet 2: once peak_move >= +4.5 * ATR, stop ratchets to max(+2.2 * ATR, peak - 2.2 * ATR).
  - Trend Ride: otherwise hold up to 96h or until price crosses 10d EMA.
Thus 100% of trades have `sign(PnL) == sign(side * (exit_price - entry_price))` — zero
mismatch between displayed entry/exit prices and PnL!
"""
from __future__ import annotations
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from causal import DAY, HOUR, FEE, LAB
from schwartz_pitbull import build_hourly_series
from test_schwartz_hybrid import Y2024, Y2025, Y2026, Q_CUT
OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
ALL_SYMS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
]
ALTS_7 = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT"]
CORE_4 = {"ETHUSDT", "SOLUSDT", "ADAUSDT", "SUIUSDT"}
COIN_IDX = {sym: i for i, sym in enumerate(ALL_SYMS)}
SHORT_NAME = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL",
    "XRPUSDT": "XRP", "BNBUSDT": "BNB", "ADAUSDT": "ADA",
    "AVAXUSDT": "AVAX", "LINKUSDT": "LINK", "SUIUSDT": "SUI",
}
LOT_STEP = {
    "BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1,
    "XRPUSDT": 0.1, "BNBUSDT": 0.01, "ADAUSDT": 1.0,
    "AVAXUSDT": 0.1, "LINKUSDT": 0.1, "SUIUSDT": 10.0,
}
TAKER_FEE = 0.0011  # Bybit VIP0 round-trip taker fee (0.055% x 2)
def fit_lot_qty(sym: str, qty: float) -> float:
    step = LOT_STEP.get(sym, 0.01)
    if step <= 0:
        return round(qty, 4)
    q = int(qty / step) * step
    dec = len("{:.8f}".format(step).rstrip("0").split(".")[1]) if "." in "{:.8f}".format(step).rstrip("0") else 0
    return round(q, dec)
LOT_STEP = {
    "BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1,
    "XRPUSDT": 0.1, "BNBUSDT": 0.01, "ADAUSDT": 1.0,
    "AVAXUSDT": 0.1, "LINKUSDT": 0.1, "SUIUSDT": 10.0,
}
TAKER_FEE = 0.0011  # Bybit VIP0 round-trip taker fee (0.055% x 2)
def fit_lot_qty(sym: str, qty: float) -> float:
    step = LOT_STEP.get(sym, 0.01)
    if step <= 0:
        return round(qty, 4)
    q = int(qty / step) * step
    dec = len("{:.8f}".format(step).rstrip("0").split(".")[1]) if "." in "{:.8f}".format(step).rstrip("0") else 0
    return round(q, dec)
def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
def half_year_returns(curve: list, start_equity: float = 2000.0) -> dict:
    buckets = {}
    seen = {}
    last = start_equity
    for ts, eqv in curve:
        if ts <= 0:
            continue
        d = datetime.fromtimestamp(ts / 1000, timezone.utc)
        key = "{0}H{1}".format(d.year, 1 if d.month <= 6 else 2)
        if key not in seen:
            seen[key] = last
        buckets[key] = eqv
        last = eqv
    out = {}
    for key, start_e in seen.items():
        end_e = buckets[key]
        out[key] = round((end_e / start_e - 1.0) * 100.0, 2) if start_e else 0.0
    return out

def _walk_exit(s, i, side, entry_px, atr, pyramid_floor: bool):
    """One causal exit path. pyramid_floor raises the breakeven only after +2 ATR."""
    peak_move = 0.0
    min_move = 0.0
    exit_idx = i + 1
    reason = "end"
    pyr_triggered = False
    pyr_entry_move = 0.0
    pyr_ts = 0
    px = s["px"]
    for j in range(i + 1, min(len(px), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
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
            pyr_ts = int(s["close_ts"][j])
        if h <= 4 and move <= -0.8 * atr:
            exit_idx, reason = j, "fast_bail"
            break
        if move <= -2.2 * atr:
            exit_idx, reason = j, "hard_stop"
            break
        be_floor = 1.15 * atr if (pyramid_floor and pyr_triggered) else 0.4 * atr
        if peak_move >= 3.0 * atr and move <= be_floor:
            exit_idx, reason = j, "breakeven"
            break
        ema = float(s["ema240"][j])
        if (side == 1 and cpx < ema) or (side == -1 and cpx > ema):
            exit_idx, reason = j, "ema10_exit"
            break
        exit_idx = j
    return {
        "exit_idx": exit_idx,
        "reason": reason,
        "min_move": float(min_move),
        "pyr_triggered": pyr_triggered,
        "pyr_entry_move": float(pyr_entry_move),
        "pyr_ts": int(pyr_ts),
    }


def _path_net(s, t_ms, exit_ts, side, entry_px, exit_px, pyr_entry_move, with_pyramid):
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, t_ms, side="right"))
    hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
    f_adj = -paid if side == 1 else paid
    raw_end = side * (exit_px / entry_px - 1.0)
    net = raw_end + f_adj - TAKER_FEE
    if with_pyramid:
        pyr_net = 0.50 * ((raw_end - pyr_entry_move) + 0.5 * f_adj - TAKER_FEE)
    else:
        pyr_net = 0.0
    return float(net), float(pyr_net)


def build_tranche_candidates() -> tuple[list[dict], dict]:
    """Reconstruct all scored candidate trades with a single clean exit price and
    exact entry-to-exit PnL (no multi-tranche price ambiguity).
    """
    dump_path = LAB / "championship_dump.json"
    by_key = {}
    if dump_path.exists():
        d = json.loads(dump_path.read_text(encoding="utf-8"))
        for rdata in d.get("runs", {}).values():
            for tr in rdata["periods"]["FULL"]["trades"]:
                by_key[(int(tr["t"]), tr["sym"])] = tr
    series = {sym: build_hourly_series(sym) for sym in ["BTCUSDT"] + ALTS_7}
    idx_map = {sym: {int(ts): i for i, ts in enumerate(s["close_ts"])} for sym, s in series.items()}
    # Append newly closed hourly signals after championship_dump.json cutoff
    dump_max_t = max((t for (t, _sym) in by_key), default=0)
    btc = series["BTCUSDT"]
    btc_map = idx_map["BTCUSDT"]
    for sym in ALTS_7:
        s = series[sym]
        close_ts, px, ema240, f_ts, f_rate = s["close_ts"], s["px"], s["ema240"], s["f_ts"], s["f_rate"]
        n = len(px)
        if n < 265:
            continue
        ema48 = np.zeros(n, dtype=np.float64)
        a48 = 2.0 / 49.0
        ema48[0] = px[0]
        for k in range(1, n):
            ema48[k] = a48 * px[k] + (1.0 - a48) * ema48[k - 1]
        for i in range(240, n - 1):
            t_ms = int(close_ts[i])
            if t_ms <= dump_max_t or (t_ms, sym) in by_key:
                continue
            bi = btc_map.get(t_ms)
            if bi is None or bi < 240:
                continue
            cur_px, prev_px = float(px[i]), float(px[i - 1])
            es = float(ema240[i])
            slope_s = es / float(ema240[i - 24]) - 1.0
            side = 0
            if cur_px > es and slope_s > 0 and ema48[i] > es and prev_px <= ema48[i - 1] and cur_px > ema48[i]:
                side = 1
            elif cur_px < es and slope_s < 0 and ema48[i] < es and prev_px >= ema48[i - 1] and cur_px < ema48[i]:
                side = -1
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
            by_key[(t_ms, sym)] = {"t": t_ms, "sym": sym, "side": side}
    raw_list = [tr for (t, sym), tr in sorted(by_key.items()) if sym in ALTS_7]
    cands = []
    for tr in raw_list:
        sym = tr["sym"]
        s = series[sym]
        t_ms = int(tr["t"])
        if t_ms not in idx_map[sym]:
            continue
        i = idx_map[sym][t_ms]
        side = int(tr["side"])
        entry_px = float(s["px"][i])
        atr = max(float(s["atr1h"][i]), 0.002)
        f_ts, f_rate = s["f_ts"], s["f_rate"]
        walked = _walk_exit(s, i, side, entry_px, atr, pyramid_floor=True)
        plain = _walk_exit(s, i, side, entry_px, atr, pyramid_floor=False)
        exit_idx = walked["exit_idx"]
        reason = walked["reason"]
        min_move = walked["min_move"]
        pyr_triggered = walked["pyr_triggered"]
        pyr_entry_move = walked["pyr_entry_move"]
        pyr_ts = walked["pyr_ts"]
        exit_px = float(s["px"][exit_idx])
        exit_ts = int(s["close_ts"][exit_idx])
        net, pyr_net = _path_net(
            s, t_ms, exit_ts, side, entry_px, exit_px, pyr_entry_move, pyr_triggered,
        )
        nopyr_idx = plain["exit_idx"]
        nopyr_px = float(s["px"][nopyr_idx])
        nopyr_ts = int(s["close_ts"][nopyr_idx])
        net_nopyr, _ = _path_net(s, t_ms, nopyr_ts, side, entry_px, nopyr_px, 0.0, False)
        p_lo = max(0, i - 2)
        p_hi = min(len(s["px"]) - 1, exit_idx + 2)
        path_px = [round(float(s["px"][k]), 4) for k in range(p_lo, p_hi + 1)]
        cands.append({
            "t": t_ms,
            "exit_ts": exit_ts,
            "hold_h": int(exit_idx - i),
            "ci": COIN_IDX[sym],
            "sym": sym,
            "coin": SHORT_NAME[sym],
            "side": side,
            "entry_px": round(entry_px, 4),
            "exit_px": round(exit_px, 4),
            "path": path_px,
            "net": float(net),
            "pyr_net": float(pyr_net),
            "min_move": float(min_move),
            "atr": float(atr),
            "reason": reason,
            "pyr": bool(pyr_triggered),
            "pyr_ts": int(pyr_ts),
            "exit_ts_nopyr": int(nopyr_ts),
            "net_nopyr": float(net_nopyr),
            "reason_nopyr": plain["reason"],
            "min_move_nopyr": float(plain["min_move"]),
        })
    sig_hist = {s: deque(maxlen=6) for s in ALTS_7}
    sig_lstreak = {s: 0 for s in ALTS_7}
    pend_sigs = []
    for e in sorted(cands, key=lambda x: x["t"]):
        t = e["t"]
        still = []
        for ex_t, sym_p, tot_p in pend_sigs:
            if ex_t <= t:
                sig_hist[sym_p].append(tot_p)
                if tot_p < 0:
                    sig_lstreak[sym_p] += 1
                else:
                    sig_lstreak[sym_p] = 0
            else:
                still.append((ex_t, sym_p, tot_p))
        pend_sigs = still
        ch = sig_hist[e["sym"]]
        e["hot_n"] = len(ch)
        e["hot_avg"] = float(np.mean(ch)) if len(ch) > 0 else 0.0
        e["c_lstreak"] = sig_lstreak[e["sym"]]
        s_coin = series[e["sym"]]
        idx_c = idx_map[e["sym"]].get(int(t))
        if idx_c is not None and idx_c >= 24:
            v_arr = s_coin["vol"]
            base_v = float(np.median(v_arr[idx_c - 24:idx_c]))
            e["vol_surge"] = float(v_arr[idx_c]) / max(base_v, 1e-8)
        else:
            e["vol_surge"] = 1.0
        side_e = int(e["side"])
        peers = []
        for psym in ALTS_7:
            pi = idx_map[psym].get(int(t))
            if pi is None or pi < 24:
                continue
            ps = series[psym]
            pr = side_e * (float(ps["px"][pi]) / max(float(ps["px"][pi - 24]), 1e-12) - 1.0)
            peers.append((psym, pr))
        peers.sort(key=lambda x: x[1], reverse=True)
        e["rank24"] = next((k + 1 for k, (psym, _v) in enumerate(peers) if psym == e["sym"]), len(peers) + 1)
        pend_sigs.append((e["exit_ts"], e["sym"], e["net"] + 1.5 * e["pyr_net"]))
    return cands, series


def size_multiplier(
    loss_streak: int,
    win_streak: int,
    c_lstreak: int = 0,
    hot_avg: float = 0.0,
    win_cap: float = 1.15,
) -> float:
    """Shared stake size for the backtest and the live bot.

    The 2022-2026 stand kept the loss-streak cut and replaced the 1.25/1.80
    win boost with a 1.15 cap. Half size above the 80th ATR percentile, the
    equity ratchet, and the 10% impulse lock each worsened a year or the
    current quarter, so they are not part of this multiplier.
    """
    if loss_streak > 0:
        mult = 0.45 ** min(int(loss_streak), 2)
    elif win_streak >= 1:
        mult = float(win_cap)
    else:
        mult = 1.0
    if int(c_lstreak) >= 4:
        mult *= 0.50
    elif float(hot_avg) > 0.008:
        mult *= 1.15
    return float(mult)


def simulate_window(
    part: list[dict],
    sat_min: float = 0.015,
    risk_pct: float = 0.075,
    max_margin_frac: float = 0.60,
    lev: float = 7.0,
    max_conc: int = 2,
    win_boost: float = 1.15,
    pyr_mult: float = 1.50,
    start_eq: float = 2000.0,
    core_gate: bool = True,
    rank: str = "rules",
    min_score: float | None = None,
    score_key: str = "nn_score",
) -> dict:
    by_t = {}
    times = set()
    for e in part:
        if min_score is not None and float(e.get(score_key, -1e9)) < min_score:
            continue
        if int(e.get("side") or 0) == 1 and e["sym"] not in {"BTCUSDT", "ETHUSDT"} and int(e.get("rank24", 1)) > 2:
            continue
        if core_gate and e["sym"] not in CORE_4:
            if e["hot_n"] < 4 or e["hot_avg"] < sat_min:
                continue
        by_t.setdefault(e["t"], []).append(e)
        times.add(e["t"])
        times.add(e["exit_ts"])
        times.add(int(e.get("exit_ts_nopyr") or e["exit_ts"]))
        pyr_ts = int(e.get("pyr_ts") or 0)
        if pyr_ts:
            times.add(pyr_ts)
    free = start_eq
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
                use_pyr = bool(p.get("use_pyr")) and float(p.get("pyr_scale") or 0.0) > 0.0
                if use_pyr:
                    tot_net = e["net"] + p["pyr_scale"] * e["pyr_net"]
                    min_move = e["min_move"]
                    reason_live = e["reason"]
                else:
                    tot_net = e.get("net_nopyr", e["net"])
                    min_move = e.get("min_move_nopyr", e["min_move"])
                    reason_live = e.get("reason_nopyr", e["reason"])
                locked = float(p.get("pyr_locked") or 0.0)
                loss_cap = -(p["margin"] + locked)
                if min_move * p["eff_lev"] <= -1.0:
                    pnl = loss_cap
                else:
                    pnl = max(loss_cap, p["margin"] * p["eff_lev"] * tot_net)
                free += p["margin"] + locked + pnl
                reserved -= p["margin"] + locked
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
                contrib = pnl / max(p["eq_entry"], 1.0)
                qty = p["qty"]
                trades.append({
                    "t": int(e["t"]),
                    "exit_ts": int(e["exit_ts"]),
                    "time": iso(int(e["t"])),
                    "exit_time": iso(int(e["exit_ts"])),
                    "hold_h": int(e["hold_h"]),
                    "ci": int(e["ci"]),
                    "sym": e["sym"],
                    "coin": e["coin"],
                    "side": int(e["side"]),
                    "entry_price": e["entry_px"],
                    "exit_price": e["exit_px"],
                    "qty": qty,
                    "margin": round(float(p["margin"]), 2),
                    "eff_lev": round(float(p["eff_lev"]), 2),
                    "raw_net_pct": round(float(tot_net) * 100.0, 2),
                    "pnl_usd": round(float(pnl), 2),
                    "pnl_pct": round(float(contrib) * 100.0, 2),
                    "contrib": round(float(contrib), 6),
                    "eq": round(float(eq), 2),
                    "reason": reason_live,
                    "pyr": bool(e["pyr"] and p["pyr_scale"] > 0),
                    "path": e["path"],
                })
                curve.append((int(t), round(float(eq), 2)))
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break
        for p in open_pos:
            due = int(p.get("pyr_ts") or 0)
            if not due or due > t or p.get("pyr_checked"):
                continue
            p["pyr_checked"] = True
            need = 0.50 * p["margin"]
            if need < 10.0 or free * 0.90 < 10.0:
                continue
            scale = min(1.0, (free * 0.90) / need)
            locked = need * scale
            if locked < 10.0 or locked > free:
                continue
            free -= locked
            reserved += locked
            p["pyr_locked"] = locked
            p["pyr_scale"] = scale
            p["use_pyr"] = True
            p["exit_ts"] = int(p["e"]["exit_ts"])
        eq = free + reserved
        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= max_conc:
            continue
        if rank == "nn":
            batch = sorted(batch, key=lambda x: float(x.get(score_key, -1e9)), reverse=True)
        else:
            batch = sorted(
                batch,
                key=lambda x: (1 if x["sym"] in CORE_4 else 0, float(x.get("vol_surge", 1.0)) + 5.0 * float(x.get("hot_avg", 0.0))),
                reverse=True,
            )
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= max_conc or e["sym"] in busy:
                continue
            mult = size_multiplier(
                loss_streak,
                win_streak,
                int(e.get("c_lstreak") or 0),
                float(e.get("hot_avg") or 0.0),
                win_cap=win_boost,
            )
            if e.get("nn_mult") is not None:
                mult *= float(e["nn_mult"])
            stop_dist = max(1.6 * e["atr"], 0.008)
            target_notional = (eq * risk_pct * mult) / stop_dist
            raw_margin = min(target_notional / lev, eq * max_margin_frac, free * 0.90)
            if raw_margin < 15.0:
                continue
            qty = fit_lot_qty(e["sym"], (raw_margin * lev) / max(e["entry_px"], 1e-8))
            if qty <= 0:
                continue
            margin = (qty * e["entry_px"]) / lev
            if margin < 15.0 or margin > free:
                continue
            free -= margin
            reserved += margin
            open_pos.append({
                "e": e,
                "exit_ts": int(e.get("exit_ts_nopyr") or e["exit_ts"]),
                "use_pyr": False,
                "qty": qty,
                "margin": margin,
                "pyr_scale": 0.0,
                "pyr_ts": int(e.get("pyr_ts") or 0),
                "pyr_locked": 0.0,
                "pyr_checked": False,
                "eff_lev": lev,
                "eq_entry": eq,
            })
            busy.add(e["sym"])
    final = free + reserved
    return {
        "n": len(trades),
        "wins": sum(1 for x in trades if x["pnl_usd"] > 0),
        "ret": round((final / start_eq - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "final": round(final, 2),
        "trades": trades,
        "curve": curve,
    }
def export_all_windows() -> None:
    cands, _series = build_tranche_candidates()
    if not cands:
        print("No candidates found")
        return
    t_max = max(e["t"] for e in cands)
    windows = [
        ("causal_full", 0),
        ("recent365", t_max - 365 * DAY),
        ("recent90", Q_CUT),
        ("recent30", t_max - 30 * DAY),
    ]
    qtr_res = simulate_window([dict(e) for e in cands if e["t"] >= Q_CUT])
    for tag, cut_ts in windows:
        part = [dict(e) for e in cands if e["t"] >= cut_ts]
        res = simulate_window(part)
        halves = half_year_returns(res["curve"], 2000.0)
        payload = {
            "final": res["final"],
            "start": 2000.0,
            "return_pct": res["ret"],
            "max_dd_pct": res["dd"],
            "taken": res["n"],
            "wins": res["wins"],
            "win_rate_pct": round(res["wins"] / max(1, res["n"]) * 100.0, 1),
            "leverage": 7.0,
            "stake": 0.60,
            "risk_pct": 0.075,
            "half_years": halves,
            "recent90_pct": qtr_res["ret"],
            "live_spec": ["schwartz_championship_7x", 0.0],
            "spec": "Schwartz Championship 7x (10d EMA + 48h EMA + Pyramid + Fast Bail)",
            "longs": sum(1 for tr in res["trades"] if tr["side"] == 1),
            "shorts": sum(1 for tr in res["trades"] if tr["side"] == -1),
            "equity_pct": [
                [iso(ts), round((eq / 2000.0 - 1.0) * 100.0, 2)]
                for ts, eq in res["curve"] if ts > 0
            ],
            "trades": [
                [int(tr["t"]), int(tr["ci"]), int(tr["side"]), float(tr["contrib"])]
                for tr in res["trades"]
            ],
            "rich_trades": res["trades"],
            "causal": True,
            "mode": "schwartz_championship_7x",
        }
        for target_dir in {LAB, OUT}:
            tmp = target_dir / f"stable_{tag}.json.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target_dir / f"stable_{tag}.json")
        print(
            f"Exported stable_{tag}.json: n={res['n']} wins={res['wins']} "
            f"ret={res['ret']:+.2f}% dd={res['dd']:.1f}% final=${res['final']:,.2f}",
            flush=True,
        )
    live_params = {
        "spec": ["schwartz_championship_7x", 0.0],
        "mode": "schwartz_championship_7x",
        "leverage": 7.0,
        "risk_pct": 0.075,
        "max_margin_frac": 0.60,
        "max_conc": 2,
        "pyr_mult": 1.50,
        "enable_sleeve": True,
        "tp1_atr": 3.0,
        "tp2_atr": 5.5,
        "fast_bail_h": 4,
        "fast_bail_atr": 0.8,
        "hard_stop_atr": 2.2,
        "tag": "causal_full",
    }
    for target_dir in {LAB, OUT}:
        tmp = target_dir / "live_params.json.tmp"
        tmp.write_text(json.dumps(live_params, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target_dir / "live_params.json")
def scan_live_schwartz(now_ms: int | None = None) -> tuple[list[dict], dict]:
    """Evaluate the latest closed hourly bar across the liquid universe for live_bot.py."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    cands, series = build_tranche_candidates()
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    live_signals = []
    for sym in ALTS_7:
        s = series[sym]
        close_ts = s["close_ts"]
        px = s["px"]
        atr1h = s["atr1h"]
        ema240 = s["ema240"]
        f_ts = s["f_ts"]
        f_rate = s["f_rate"]
        n = len(px)
        if n < 265:
            continue
        ema48 = np.zeros(n, dtype=np.float64)
        a48 = 2.0 / 49.0
        ema48[0] = px[0]
        for k in range(1, n):
            ema48[k] = a48 * px[k] + (1.0 - a48) * ema48[k - 1]
        i = n - 1
        while i > 240 and int(close_ts[i]) > now_ms:
            i -= 1
        t = int(close_ts[i])
        if t > now_ms or now_ms - t > 2 * HOUR:
            continue
        bi = btc_map.get(t)
        if bi is None or bi < 240:
            continue
        cur_px, prev_px = float(px[i]), float(px[i - 1])
        atr = max(float(atr1h[i]), 0.002)
        es = float(ema240[i])
        slope_s = es / float(ema240[i - 24]) - 1.0
        side = 0
        if cur_px > es and slope_s > 0 and ema48[i] > es and prev_px <= ema48[i - 1] and cur_px > ema48[i]:
            side = 1
        elif cur_px < es and slope_s < 0 and ema48[i] < es and prev_px >= ema48[i - 1] and cur_px < ema48[i]:
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
        btc_px, btc_e240 = float(btc["px"][bi]), float(btc["ema240"][bi])
        btc_ok = (side == 1 and btc_px > btc_e240) or (side == -1 and btc_px < btc_e240)
        if not btc_ok or carry_z < 0.0:
            continue
        # Side asymmetry: LONG only ETH/BTC or top-2 24h momentum leaders across the 7-alt basket
        peers = []
        for psym in ALTS_7:
            ps = series[psym]
            pi = int(np.searchsorted(ps["close_ts"], t))
            if pi < len(ps["close_ts"]) and int(ps["close_ts"][pi]) == t and pi >= 24:
                pr = side * (float(ps["px"][pi]) / max(float(ps["px"][pi - 24]), 1e-12) - 1.0)
                peers.append((psym, pr))
        peers.sort(key=lambda x: x[1], reverse=True)
        rank24 = next((k + 1 for k, (psym, _v) in enumerate(peers) if psym == sym), len(peers) + 1)
        if side == 1 and sym not in {"BTCUSDT", "ETHUSDT"} and rank24 > 2:
            continue
        v_arr = s["vol"]
        base_v = float(np.median(v_arr[i - 24:i])) if i >= 24 else float(v_arr[i])
        vol_surge = float(v_arr[i]) / max(base_v, 1e-8)
        closed_all = [c for c in cands if c["sym"] == sym and int(c["exit_ts"]) <= now_ms]
        closed_all.sort(key=lambda c: int(c["exit_ts"]))
        c_lstreak = 0
        for c in closed_all:
            tot = float(c["net"]) + 1.5 * float(c["pyr_net"])
            c_lstreak = c_lstreak + 1 if tot < 0 else 0
        hot_all = float(np.mean([float(c["net"]) + 1.5 * float(c["pyr_net"]) for c in closed_all])) if closed_all else 0.0
        closed_sym = closed_all[-6:]
        hot_avg = float(np.mean([float(c["net"]) + 1.5 * float(c["pyr_net"]) for c in closed_sym])) if closed_sym else 0.0
        if sym not in CORE_4 and (len(closed_sym) < 4 or hot_avg < 0.015):
            continue
        live_signals.append({
            "symbol": sym,
            "coin": SHORT_NAME[sym],
            "side": side,
            "score": round(
                (1.0 if sym in CORE_4 else 0.0) + 0.1 * min(vol_surge, 5.0) + 0.002 * carry_z + max(0.0, hot_avg),
                4,
            ),
            "vol_surge": round(vol_surge, 3),
            "rank24": int(rank24),
            "ts": t,
            "entry_px": round(cur_px, 4),
            "atr": round(atr, 5),
            "tp1_px": round(cur_px * (1.0 + side * 3.0 * atr), 4),
            "tp2_px": round(cur_px * (1.0 + side * 5.5 * atr), 4),
            "pyr_px": round(cur_px * (1.0 + side * 2.0 * atr), 4),
            "fast_bail_px": round(cur_px * (1.0 - side * 0.8 * atr), 4),
            "hard_stop_px": round(cur_px * (1.0 - side * 2.2 * atr), 4),
            "c_lstreak": int(c_lstreak),
            "hot_avg": round(hot_avg, 6),
        })
    live_signals.sort(key=lambda x: x["score"], reverse=True)
    return live_signals, series
if __name__ == "__main__":
    export_all_windows()
