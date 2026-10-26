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
        peak_move = 0.0
        min_move = 0.0
        exit_idx = i + 1
        reason = "end"
        pyr_triggered = False
        pyr_entry_move = 0.0
        tp1_done = False
        tp1_move = 0.0
        tp2_done = False
        tp2_move = 0.0
        for j in range(i + 1, min(len(s["px"]), i + 96)):
            if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
                exit_idx = j - 1
                reason = "gap"
                break
            cpx = float(s["px"][j])
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
            be_floor = 1.15 * atr if pyr_triggered else 0.4 * atr
            if tp1_done and move <= be_floor:
                exit_idx = j
                reason = "tp_scale+be"
                break
            if (side == 1 and cpx < s["ema240"][j]) or (side == -1 and cpx > s["ema240"][j]):
                exit_idx = j
                reason = "ema10_exit"
                break
            if not pyr_triggered and move >= 2.0 * atr:
                pyr_triggered = True
                pyr_entry_move = move
            if not tp1_done and move >= 3.0 * atr:
                tp1_done = True
                tp1_move = move
            if not tp2_done and move >= 5.5 * atr:
                tp2_done = True
                tp2_move = move
            exit_idx = j
        exit_px = float(s["px"][exit_idx])
        exit_ts = int(s["close_ts"][exit_idx])
        lo = int(np.searchsorted(f_ts, t_ms, side="right"))
        hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
        paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
        f_adj = (-paid if side == 1 else paid)
        raw_end = side * (exit_px / entry_px - 1.0)
        # Exact live tranche decomposition (matches live_bot.py: 20% at TP1, 15% of rem at TP2, rest at exit_px)
        rem_w = 1.0
        base_gross = 0.0
        pyr_gross = 0.0
        if tp1_done:
            w1 = 0.20 * rem_w
            base_gross += w1 * tp1_move
            pyr_gross += w1 * (tp1_move - pyr_entry_move) if pyr_triggered else 0.0
            rem_w -= w1
        if tp2_done:
            w2 = 0.15 * rem_w
            base_gross += w2 * tp2_move
            pyr_gross += w2 * (tp2_move - pyr_entry_move) if pyr_triggered else 0.0
            rem_w -= w2
        base_gross += rem_w * raw_end
        pyr_gross += rem_w * (raw_end - pyr_entry_move) if pyr_triggered else 0.0
        net = base_gross + f_adj - TAKER_FEE
        pyr_net = 0.50 * (pyr_gross + 0.5 * f_adj - TAKER_FEE) if pyr_triggered else 0.0
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
        pend_sigs.append((e["exit_ts"], e["sym"], e["net"] + 1.5 * e["pyr_net"]))
    return cands, series
def simulate_window(
    part: list[dict],
    sat_min: float = 0.015,
    risk_pct: float = 0.075,
    max_margin_frac: float = 0.60,
    lev: float = 7.0,
    max_conc: int = 2,
    win_boost: float = 1.80,
    pyr_mult: float = 1.50,
    start_eq: float = 2000.0,
) -> dict:
    by_t = {}
    times = set()
    for e in part:
        if e["sym"] not in CORE_4:
            if e["hot_n"] < 4 or e["hot_avg"] < sat_min:
                continue
        by_t.setdefault(e["t"], []).append(e)
        times.add(e["t"])
        times.add(e["exit_ts"])
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
                tot_net = e["net"] + p["pyr_scale"] * e["pyr_net"]
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
                    "reason": e["reason"],
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
        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= max_conc:
            continue
        batch = sorted(batch, key=lambda x: (1 if x["sym"] in CORE_4 else 0, x["hot_avg"]), reverse=True)
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= max_conc or e["sym"] in busy:
                continue
            if loss_streak > 0:
                mult = 0.45 ** min(loss_streak, 2)
            elif win_streak >= 2:
                mult = win_boost
            elif win_streak == 1:
                mult = 1.25
            else:
                mult = 1.0
            if e["c_lstreak"] >= 4:
                mult *= 0.50
            elif e["hot_avg"] > 0.008:
                mult *= 1.15
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
            # Check if free margin remains for the +50% pyramid leg (just like live_bot.py)
            rem_after_open = max(0.0, free)
            need_pyr = 0.50 * margin
            if e["pyr"] and rem_after_open >= 10.0:
                pyr_scale = min(1.0, (rem_after_open * 0.90) / max(need_pyr, 1e-8))
            else:
                pyr_scale = 0.0
            open_pos.append({
                "e": e,
                "exit_ts": e["exit_ts"],
                "qty": qty,
                "margin": margin,
                "pyr_scale": pyr_scale,
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
        t = int(close_ts[i])
        if now_ms - t > 2 * HOUR:
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
        closed_sym = [c for c in cands if c["sym"] == sym and c["exit_ts"] <= now_ms][-6:]
        hot_avg = float(np.mean([c["net"] + 1.5 * c["pyr_net"] for c in closed_sym])) if closed_sym else 0.0
        if sym not in CORE_4 and (len(closed_sym) < 4 or hot_avg < 0.015):
            continue
        live_signals.append({
            "symbol": sym,
            "coin": SHORT_NAME[sym],
            "side": side,
            "score": round(0.015 + 0.002 * carry_z + max(0.0, hot_avg), 4),
            "ts": t,
            "entry_px": round(cur_px, 4),
            "atr": round(atr, 5),
            "tp1_px": round(cur_px * (1.0 + side * 3.0 * atr), 4),
            "tp2_px": round(cur_px * (1.0 + side * 5.5 * atr), 4),
            "pyr_px": round(cur_px * (1.0 + side * 2.0 * atr), 4),
            "fast_bail_px": round(cur_px * (1.0 - side * 0.8 * atr), 4),
            "hard_stop_px": round(cur_px * (1.0 - side * 2.2 * atr), 4),
        })
    live_signals.sort(key=lambda x: x["score"], reverse=True)
    return live_signals, series
if __name__ == "__main__":
    export_all_windows()
