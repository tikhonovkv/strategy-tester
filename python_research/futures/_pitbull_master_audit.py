# -*- coding: utf-8 -*-
"""One portfolio cycle for rules, sized stakes, hour-1 reaction, dual regime, and the reaction net.
Same fee and slippage on every variant. Re-entry only after a pullback to the 24h EMA.
Local only.
"""
from __future__ import annotations

import heapq
import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp

SLIP = 0.0003
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)


def stamp(y, m, d):
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


def vol_ratio(s, i):
    vol = s["vol"]
    base = float(np.median(vol[i - 24:i])) if i >= 24 else float(vol[i])
    return float(vol[i]) / max(base, 1e-8)


def vol_falling(s, j):
    vol = s["vol"]
    base = float(np.median(vol[max(0, j - 6):j])) if j > 1 else float(vol[j])
    return float(vol[j]) < base


def trend_ok(s, btc, btc_map, j, side):
    if j < 24 or j >= len(s["px"]):
        return False
    if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
        return False
    ema = float(s["ema240"][j])
    prev = float(s["ema240"][j - 24])
    if prev <= 0:
        return False
    slope = ema / prev - 1.0
    px = float(s["px"][j])
    if side == 1 and not (px > ema and slope > 0):
        return False
    if side == -1 and not (px < ema and slope < 0):
        return False
    bt = btc_map.get(int(s["close_ts"][j]))
    if bt is None:
        return False
    bpx, bema = float(btc["px"][bt]), float(btc["ema240"][bt])
    if side == 1 and bpx <= bema:
        return False
    if side == -1 and bpx >= bema:
        return False
    return True


def dist_atr(s, j, side, atr):
    ema = float(s["ema240"][j])
    if ema <= 0:
        return 99.0
    return side * (float(s["px"][j]) / ema - 1.0) / atr


def money(s, i, exit_i, side, atr):
    px, ts = s["px"], s["close_ts"]
    entry = float(px[i]) * (1.0 + side * SLIP)
    exit_px = float(px[exit_i]) * (1.0 - side * SLIP)
    raw = side * (exit_px / entry - 1.0)
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, int(ts[i]), side="right"))
    hi = int(np.searchsorted(f_ts, int(ts[exit_i]), side="right"))
    f_adj = 0.0
    if hi > lo:
        paid = float(f_rate[lo:hi].sum())
        f_adj = -paid if side == 1 else paid
    net = raw + f_adj - sp.TAKER_FEE
    return entry, raw, f_adj, net


def walk(s, i, side, deadline, atr, spec, probs=None):
    px, ts, ema = s["px"], s["close_ts"], s["ema240"]
    entry = float(px[i]) * (1.0 + side * SLIP)
    peak = 0.0
    pyr = False
    pyr_move = 0.0
    tight = False
    champion = False
    exit_i = i
    reason = "end_96h"
    p_cut = probs[0] if probs else None
    p_lock = probs[1] if probs else None
    last = min(deadline, len(px))
    for j in range(i + 1, last):
        if int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            exit_i, reason = max(i, j - 1), "gap"
            break
        h = j - i
        c = float(px[j])
        move = side * (c / entry - 1.0)
        peak = max(peak, move)
        if not pyr and move >= 2.0 * atr:
            pyr, pyr_move = True, move
        if h == 1 and spec.get("vol_ratio", 0) >= 1.6 and move >= 0.8 * atr:
            champion = True
        if move <= -2.2 * atr:
            exit_i, reason = j, "hard_stop"
            break
        if spec.get("tighten") and h <= 3 and peak >= 1.2 * atr:
            tight = True
        if tight and move <= -0.3 * atr:
            exit_i, reason = j, "tight_stop"
            break
        if spec.get("impulse") and h <= 3 and peak < 0.35 * atr and move <= -0.45 * atr and vol_falling(s, j):
            exit_i, reason = j, "instant_cut"
            break
        if spec.get("bail") and h <= 4 and move <= -0.8 * atr:
            exit_i, reason = j, "fast_bail"
            break
        held_champion = champion and spec.get("champion_holds")
        if p_cut is not None and not held_champion and h <= 6 and move < atr and h - 1 < len(p_cut) and p_cut[h - 1] >= spec["thr_cut"]:
            exit_i, reason = j, "model_cut"
            break
        if p_lock is not None and not held_champion and move >= 2.0 * atr and h - 1 < len(p_lock) and p_lock[h - 1] >= spec["thr_lock"]:
            exit_i, reason = j, "lock"
            break
        if spec.get("target") and not held_champion:
            if move >= spec["target"] * atr:
                exit_i, reason = j, "target"
                break
            if peak >= 2.0 * atr and move <= peak - 0.8 * atr:
                exit_i, reason = j, "fade"
                break
        elif spec.get("be") and peak >= 3.0 * atr and move <= (1.15 * atr if pyr else 0.4 * atr):
            exit_i, reason = j, "breakeven"
            break
        if spec.get("ema") and ((side == 1 and c < float(ema[j])) or (side == -1 and c > float(ema[j]))):
            exit_i, reason = j, "ema10_exit"
            break
        exit_i = j
    if exit_i <= i:
        return None
    _entry, raw, f_adj, net = money(s, i, exit_i, side, atr)
    pyr_net = 0.50 * ((raw - pyr_move) + 0.5 * f_adj - sp.TAKER_FEE) if pyr else 0.0
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
        "champion": champion,
        "sprint": bool(spec.get("target")) and not champion,
    }


def pullback(s, btc, btc_map, exit_i, side, deadline, atr):
    px, ts = s["px"], s["close_ts"]
    ema24 = s["ema24"]
    last = min(deadline, exit_i + 18, len(px))
    for j in range(exit_i + 2, last):
        if int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            break
        if dist_atr(s, j, side, atr) > 6.0:
            continue
        if not trend_ok(s, btc, btc_map, j, side):
            continue
        e_prev, e_now = float(ema24[j - 1]), float(ema24[j])
        crossed = (side == 1 and float(px[j - 1]) <= e_prev and float(px[j]) > e_now and float(px[j]) > float(px[j - 1])) or (
            side == -1 and float(px[j - 1]) >= e_prev and float(px[j]) < e_now and float(px[j]) < float(px[j - 1])
        )
        base = float(np.median(s["vol"][max(0, j - 24):j])) if j > 24 else float(s["vol"][j])
        if crossed and float(s["vol"][j]) > 1.2 * max(base, 1e-8):
            return j
    return None


REENTRY_OK = {"breakeven", "target", "fade", "lock", "ema10_exit", "end_96h", "tight_stop"}


def cascade(s, btc, btc_map, i, side, spec, prob_fn=None):
    origin = i
    deadline = min(len(s["px"]), origin + 96)
    legs = []
    cursor = i
    while cursor < deadline and len(legs) < 3:
        atr = max(float(s["atr1h"][cursor]), 0.002)
        probs = prob_fn(s, cursor, side, atr) if prob_fn else None
        leg = walk(s, cursor, side, deadline, atr, spec, probs)
        if leg is None:
            break
        legs.append(leg)
        allow = spec.get("reentry")
        if allow == "sprint_only":
            allow = not leg["champion"]
        reasons = spec.get("reentry_reasons") or REENTRY_OK
        if not allow or not leg["win"] or leg["reason"] not in reasons:
            break
        nxt = pullback(s, btc, btc_map, leg["exit_i"], side, deadline, atr)
        if nxt is None:
            break
        cursor = nxt
    return legs


def stake(loss, win, c_streak, hot, old_boost, reentry):
    if old_boost:
        if loss > 0:
            mult = 0.45 ** min(int(loss), 2)
        elif win >= 2:
            mult = 1.80
        elif win == 1:
            mult = 1.25
        else:
            mult = 1.0
        if int(c_streak) >= 4:
            mult *= 0.50
        elif float(hot) > 0.008:
            mult *= 1.15
    else:
        mult = sp.size_multiplier(loss, win, c_streak, hot, 1.15)
    if reentry:
        mult = min(mult, 1.0) * 0.5
    return mult


def simulate(book, start, end, old_boost=False, start_eq=2000.0):
    first = []
    by_t = {}
    events = []
    seen = set()

    def wake(t):
        if t not in seen:
            seen.add(t)
            heapq.heappush(events, t)

    for b in book:
        leg = b["legs"][0]
        if start <= leg["t"] < end:
            by_t.setdefault(leg["t"], []).append((b, 0))
            wake(leg["t"])
            wake(leg["exit_ts"])
            first.append(b)
    if not first:
        return {"n": 0, "reentries": 0, "ret": 0.0, "dd": 0.0, "final": start_eq, "calmar": None, "top5": 0.0, "curve": []}
    free = float(start_eq)
    reserved = 0.0
    open_pos = []
    trades = []
    peak_eq = free
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
                    loss_streak, win_streak = loss_streak + 1, 0
                else:
                    loss_streak, win_streak = 0, win_streak + 1
                peak_eq = max(peak_eq, eq)
                if peak_eq > 0:
                    max_dd = max(max_dd, (peak_eq - eq) / peak_eq)
                trades.append({"pnl": pnl, "re": p["k"] > 0})
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
            mult = stake(loss_streak, win_streak, b["c_lstreak"], b["hot_avg"], old_boost, k > 0)
            stop_atr = 0.8 if (k > 0 or leg["sprint"]) else 1.6
            stop_dist = max(stop_atr * leg["atr"], 0.008)
            raw_margin = min((eq * 0.075 * mult) / stop_dist / 7.0, eq * 0.60, free * 0.90)
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
            pyr_scale = min(1.0, (rem * 0.90) / max(need, 1e-8)) if leg["pyr_net"] and rem >= 10 else 0.0
            open_pos.append({"book": b, "k": k, "leg": leg, "sym": b["sym"], "exit_ts": leg["exit_ts"], "margin": margin, "pyr_scale": pyr_scale})
            busy.add(b["sym"])
            wake(leg["exit_ts"])
    final = free + reserved
    wins = sorted((tr["pnl"] for tr in trades if tr["pnl"] > 0), reverse=True)
    win_sum = sum(wins) or 1.0
    dd = max_dd * 100.0
    ret = (final / start_eq - 1.0) * 100.0
    return {
        "n": len(trades),
        "reentries": sum(1 for tr in trades if tr["re"]),
        "ret": round(ret, 2),
        "dd": round(dd, 1),
        "final": round(final, 2),
        "calmar": round(ret / dd, 2) if dd else None,
        "top5": round(sum(wins[:5]) / win_sum, 3),
        "curve": curve,
    }


SPEC = {
    "V0_rules": {"bail": True, "be": True, "ema": True, "impulse": False, "tighten": False, "target": None, "reentry": False, "champion_holds": False},
    "V1_sized": {"bail": True, "be": True, "ema": True, "impulse": False, "tighten": False, "target": None, "reentry": False, "champion_holds": False},
    "V2_hour1": {"bail": True, "be": True, "ema": True, "impulse": True, "tighten": True, "target": None, "reentry": True, "champion_holds": False},
    "V3_dual": {"bail": True, "be": True, "ema": True, "impulse": True, "tighten": True, "target": 2.6, "reentry": "sprint_only", "champion_holds": True},
}


def build(cands, series, spec, prob_fn=None):
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    book = []
    for e in cands:
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None or i < 48:
            continue
        spec = dict(spec)
        spec["vol_ratio"] = vol_ratio(series[e["sym"]], i)
        legs = cascade(series[e["sym"]], btc, btc_map, i, int(e["side"]), spec, prob_fn)
        if not legs:
            continue
        book.append({
            "sym": e["sym"], "side": int(e["side"]),
            "hot_n": int(e.get("hot_n") or 0), "hot_avg": float(e.get("hot_avg") or 0.0),
            "c_lstreak": int(e.get("c_lstreak") or 0), "legs": legs, "t": int(e["t"]),
        })
    return book


def run_named(book, old_boost):
    out = {}
    curves = {}
    for label, a, b in WINDOWS:
        res = simulate(book, a, b, old_boost=old_boost)
        curves[label] = res.pop("curve")
        out[label] = res
    return out, curves


def main():
    cands, series = sp.build_tranche_candidates()
    report = {}
    curves = {}
    books = {}
    for name, spec in SPEC.items():
        books[name] = build(cands, series, spec)
        report[name], curves[name] = run_named(books[name], old_boost=(name == "V0_rules"))
        print(name, "full", report[name]["full"], flush=True)
    # V4 is attached by the network module when this file is imported as main via run_v4
    payload = {"report": report}
    (sp.LAB / "_pitbull_master_audit.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (sp.LAB / "_pitbull_master_curves.json").write_text(json.dumps(curves), encoding="utf-8")
    print("partial", len(report))
    return cands, series, report, curves


if __name__ == "__main__":
    main()
