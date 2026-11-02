# -*- coding: utf-8 -*-
"""Sprint exits and pitbull re-entry versus the current Schwartz hold.
Every fill pays 0.11% taker and 3 bps of slippage. Local only.
"""
from __future__ import annotations

import heapq
import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp

SLIP = 0.0003
SPECS = {
    "rules": {"cut_h": 4, "cut_atr": 0.8, "bail_h": None, "target": None, "arm": None, "fade": None, "be": True, "reentry": False, "max_legs": 1},
    "cut3": {"cut_h": 3, "cut_atr": 0.5, "bail_h": 4, "bail_atr": 0.8, "target": None, "arm": None, "fade": None, "be": True, "reentry": False, "max_legs": 1},
    "sprint3": {"cut_h": 3, "cut_atr": 0.5, "bail_h": None, "target": 3.0, "arm": 2.0, "fade": 0.8, "be": False, "reentry": True, "max_legs": 4},
    "sprint4": {"cut_h": 3, "cut_atr": 0.5, "bail_h": None, "target": 4.0, "arm": 2.5, "fade": 1.0, "be": False, "reentry": True, "max_legs": 4},
}


def stamp(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


WINDOWS = [
    ("2022", stamp(2022, 1, 1), stamp(2023, 1, 1)),
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, stamp(2026, 11, 3)),
    ("full", stamp(2022, 1, 1), stamp(2026, 11, 3)),
]


def trend_ok(s, btc, btc_map, j, side):
    if j < 24 or j >= len(s["px"]):
        return False
    if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
        return False
    px = float(s["px"][j])
    ema = float(s["ema240"][j])
    prev = float(s["ema240"][j - 24])
    if prev <= 0:
        return False
    slope = ema / prev - 1.0
    if side == 1 and not (px > ema and slope > 0):
        return False
    if side == -1 and not (px < ema and slope < 0):
        return False
    bt = btc_map.get(int(s["close_ts"][j]))
    if bt is None or bt < 24:
        return False
    bpx, bema = float(btc["px"][bt]), float(btc["ema240"][bt])
    if side == 1 and bpx <= bema:
        return False
    if side == -1 and bpx >= bema:
        return False
    return True


def walk_leg(s, i, side, deadline, atr, spec):
    px, ts, ema = s["px"], s["close_ts"], s["ema240"]
    entry = float(px[i]) * (1.0 + side * SLIP)
    peak = 0.0
    pyr = False
    pyr_move = 0.0
    exit_i = i
    reason = "end_96h"
    for j in range(i + 1, min(deadline, len(px))):
        if int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            exit_i = max(i, j - 1)
            reason = "gap"
            break
        c = float(px[j])
        move = side * (c / entry - 1.0)
        peak = max(peak, move)
        held = j - i
        if not pyr and move >= 2.0 * atr:
            pyr = True
            pyr_move = move
        if move <= -2.2 * atr:
            exit_i, reason = j, "hard_stop"
            break
        if held <= spec["cut_h"] and move <= -spec["cut_atr"] * atr:
            exit_i, reason = j, "fast_cut"
            break
        bail_h = spec.get("bail_h")
        if bail_h and held <= bail_h and move <= -spec["bail_atr"] * atr:
            exit_i, reason = j, "fast_bail"
            break
        if spec["target"] is not None and move >= spec["target"] * atr:
            exit_i, reason = j, "target"
            break
        if spec["arm"] is not None and peak >= spec["arm"] * atr and move <= peak - spec["fade"] * atr:
            exit_i, reason = j, "fade"
            break
        if spec["be"] and peak >= 3.0 * atr:
            floor = 1.15 * atr if pyr else 0.4 * atr
            if move <= floor:
                exit_i, reason = j, "breakeven"
                break
        if (side == 1 and c < float(ema[j])) or (side == -1 and c > float(ema[j])):
            exit_i, reason = j, "ema10_exit"
            break
        exit_i = j
    else:
        reason = "end_96h"
    if exit_i <= i:
        return None
    exit_px = float(px[exit_i]) * (1.0 - side * SLIP)
    raw = side * (exit_px / entry - 1.0)
    f_adj = 0.0
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, int(ts[i]), side="right"))
    hi = int(np.searchsorted(f_ts, int(ts[exit_i]), side="right"))
    if hi > lo:
        paid = float(f_rate[lo:hi].sum())
        f_adj = -paid if side == 1 else paid
    net = raw + f_adj - sp.TAKER_FEE
    if pyr:
        pyr_net = 0.50 * ((raw - pyr_move) + 0.5 * f_adj - sp.TAKER_FEE)
    else:
        pyr_net = 0.0
    return {
        "entry_i": i,
        "exit_i": exit_i,
        "t": int(ts[i]),
        "exit_ts": int(ts[exit_i]),
        "entry_px": float(px[i]),
        "net": float(net),
        "pyr_net": float(pyr_net),
        "atr": float(atr),
        "reason": reason,
        "win": net > 0,
    }


def cascade(s, btc, btc_map, i, side, spec):
    deadline = min(len(s["px"]), i + 96)
    legs = []
    cursor = i
    while cursor < deadline and len(legs) < spec["max_legs"]:
        atr = max(float(s["atr1h"][cursor]), 0.002)
        leg = walk_leg(s, cursor, side, deadline, atr, spec)
        if leg is None:
            break
        legs.append(leg)
        if not spec["reentry"] or not leg["win"]:
            break
        nxt = None
        for wait in range(1, 4):
            j = leg["exit_i"] + wait
            if j >= deadline:
                break
            if trend_ok(s, btc, btc_map, j, side):
                nxt = j
                break
        if nxt is None:
            break
        cursor = nxt
    return legs


def build_book(cands, series, spec):
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    book = []
    for e in cands:
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None:
            continue
        legs = cascade(series[e["sym"]], btc, btc_map, i, int(e["side"]), spec)
        if not legs:
            continue
        book.append({
            "sym": e["sym"],
            "coin": e["coin"],
            "side": int(e["side"]),
            "hot_n": int(e.get("hot_n") or 0),
            "hot_avg": float(e.get("hot_avg") or 0.0),
            "c_lstreak": int(e.get("c_lstreak") or 0),
            "legs": legs,
        })
    return book


def simulate(book, start, end, start_eq=2000.0):
    first = []
    for b in book:
        leg = b["legs"][0]
        if start <= leg["t"] < end:
            first.append((b, 0))
    by_t = {}
    events = []
    seen = set()

    def wake(t):
        if t not in seen:
            seen.add(t)
            heapq.heappush(events, t)

    for b, k in first:
        t = b["legs"][k]["t"]
        by_t.setdefault(t, []).append((b, k))
        wake(t)
        wake(b["legs"][k]["exit_ts"])
    free = float(start_eq)
    reserved = 0.0
    open_pos = []
    trades = []
    peak = free
    max_dd = 0.0
    loss_streak = 0
    win_streak = 0
    curve = []
    armed = {}
    while events:
        t = heapq.heappop(events)
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["leg"]
                tot = e["net"] + p["pyr_scale"] * e["pyr_net"]
                pnl = max(-p["margin"], p["margin"] * 7.0 * tot)
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
                trades.append({"t": e["t"], "sym": p["sym"], "pnl": pnl, "reason": e["reason"], "re": p["k"] > 0})
                curve.append((int(t), round(eq, 2)))
                nxt = p["k"] + 1
                if pnl > 0 and nxt < len(p["book"]["legs"]):
                    child = p["book"]["legs"][nxt]
                    if child["t"] >= t:
                        armed.setdefault(child["t"], []).append((p["book"], nxt))
                        wake(child["t"])
                        wake(child["exit_ts"])
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break
        batch = list(by_t.get(t, [])) + list(armed.pop(t, []))
        if not batch or len(open_pos) >= 2:
            continue
        def rank(item):
            b, k = item
            return (0 if k else 1, 0 if b["sym"] in sp.CORE_4 else 1, -b["hot_avg"])
        batch.sort(key=rank)
        busy = {p["sym"] for p in open_pos}
        for b, k in batch:
            if len(open_pos) >= 2 or b["sym"] in busy:
                continue
            if k == 0 and b["sym"] not in sp.CORE_4 and (b["hot_n"] < 4 or b["hot_avg"] < 0.015):
                continue
            leg = b["legs"][k]
            mult = sp.size_multiplier(loss_streak, win_streak, b["c_lstreak"], b["hot_avg"])
            stop_dist = max(1.6 * leg["atr"], 0.008)
            target_notional = (eq * 0.075 * mult) / stop_dist
            raw_margin = min(target_notional / 7.0, eq * 0.60, free * 0.90)
            if raw_margin < 15.0:
                continue
            qty = sp.fit_lot_qty(b["sym"], (raw_margin * 7.0) / max(leg["entry_px"], 1e-8))
            if qty <= 0:
                continue
            margin = (qty * leg["entry_px"]) / 7.0
            if margin < 15.0 or margin > free:
                continue
            free -= margin
            reserved += margin
            rem = max(0.0, free)
            need = 0.50 * margin
            pyr_scale = min(1.0, (rem * 0.90) / max(need, 1e-8)) if leg["pyr_net"] != 0 and rem >= 10.0 else 0.0
            open_pos.append({
                "book": b, "k": k, "leg": leg, "sym": b["sym"],
                "exit_ts": leg["exit_ts"], "margin": margin, "pyr_scale": pyr_scale,
            })
            busy.add(b["sym"])
            wake(leg["exit_ts"])
    final = free + reserved
    wins = [tr["pnl"] for tr in trades if tr["pnl"] > 0]
    win_sum = sum(wins) or 1.0
    top5 = sum(sorted(wins, reverse=True)[:5])
    dd = max_dd * 100.0
    ret = (final / start_eq - 1.0) * 100.0
    return {
        "n": len(trades),
        "reentries": sum(1 for tr in trades if tr["re"]),
        "ret": round(ret, 2),
        "dd": round(dd, 1),
        "final": round(final, 2),
        "calmar": round(ret / dd, 2) if dd else None,
        "top5": round(top5 / win_sum, 3),
        "curve": curve,
    }


def main():
    cands, series = sp.build_tranche_candidates()
    report = {}
    curves = {}
    for name, spec in SPECS.items():
        book = build_book(cands, series, spec)
        report[name] = {}
        curves[name] = {}
        for label, a, b in WINDOWS:
            res = simulate(book, a, b)
            curves[name][label] = res.pop("curve")
            report[name][label] = res
            if label == "full":
                print(name, "full", res, flush=True)
    out = sp.LAB / "_sprint_reentry.json"
    out.write_text(json.dumps({"report": report}, ensure_ascii=False), encoding="utf-8")
    (sp.LAB / "_sprint_curves.json").write_text(json.dumps(curves), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
