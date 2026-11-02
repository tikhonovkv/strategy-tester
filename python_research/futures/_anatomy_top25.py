# -*- coding: utf-8 -*-
"""Anatomy of the 25 largest Schwartz trades versus the rest, 2022-2026.
Week before entry, the hourly path, and how many sprint waves sit inside one hold.
Local only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp

OUT = sp.LAB / "_anatomy_top25.json"
HORIZON = 48


def med(vals):
    clean = [float(v) for v in vals if v is not None and np.isfinite(v)]
    if not clean:
        return None
    return round(float(np.median(clean)), 3)


def mean(vals):
    clean = [float(v) for v in vals if v is not None and np.isfinite(v)]
    if not clean:
        return None
    return round(float(np.mean(clean)), 3)


def share(flags):
    if not flags:
        return None
    return round(float(np.mean(flags)), 3)


def carry_z(s, i, side):
    t = int(s["close_ts"][i])
    fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
    if fi < 90:
        return 0.0
    hist = s["f_rate"][fi - 89:fi + 1]
    z = (float(s["f_rate"][fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
    return float(-side * z)


def trend_age(px, ema, i, side):
    age = 0
    k = i
    while k > 0 and age < 720:
        if side * (float(px[k]) - float(ema[k])) <= 0:
            break
        age += 1
        k -= 1
    return age


def breadth_at(series, ts, side):
    n = 0
    for sym in sp.ALTS_7:
        s = series[sym]
        j = int(np.searchsorted(s["close_ts"], ts))
        if j >= len(s["close_ts"]) or int(s["close_ts"][j]) != int(ts):
            continue
        above = float(s["px"][j]) > float(s["ema240"][j])
        if (side == 1 and above) or (side == -1 and not above):
            n += 1
    return n


def forward(s, i, side, atr, n=HORIZON):
    px = s["px"]
    ts = s["close_ts"]
    entry = float(px[i])
    moves = []
    for h in range(1, n + 1):
        j = i + h
        if j >= len(px):
            break
        if int(ts[j]) != int(ts[j - 1]) + sp.HOUR:
            break
        moves.append(side * (float(px[j]) / entry - 1.0) / atr)
    return moves


def first_hour(moves, level):
    for h, m in enumerate(moves, start=1):
        if m >= level:
            return h
    return None


def waves_of(moves, pull=1.0):
    """Count a new wave each time price gives back `pull` ATR and then makes a new high."""
    if not moves:
        return 0, 0, 0.0
    peak = moves[0]
    armed = False
    n_waves = 1
    n_pull = 0
    deepest = 0.0
    for m in moves:
        if m >= peak:
            if armed:
                n_waves += 1
                armed = False
            peak = m
        give = peak - m
        deepest = max(deepest, give)
        if give >= pull and not armed:
            n_pull += 1
            armed = True
    return n_waves, n_pull, deepest


def dist_atr(s, i, side, atr, back):
    j = i - back
    if j < 1:
        return None
    ema = float(s["ema240"][j])
    if ema <= 0:
        return None
    return side * (float(s["px"][j]) / ema - 1.0) / atr


def dissect(cands, series):
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    rows = []
    for e in cands:
        s = series[e["sym"]]
        i = idx[e["sym"]].get(int(e["t"]))
        if i is None or i < 168:
            continue
        side = int(e["side"])
        atr = max(float(s["atr1h"][i]), 0.002)
        moves = forward(s, i, side, atr, 96)
        hold_moves = moves[: max(1, int(e["hold_h"]))]
        peak = max(moves) if moves else 0.0
        exit_atr = hold_moves[-1] if hold_moves else 0.0
        peak_held = max(hold_moves) if hold_moves else 0.0
        n_waves, n_pull, deepest = waves_of(hold_moves, 1.0)
        w2, p2, _d2 = waves_of(hold_moves, 2.0)
        w3, p3, _d3 = waves_of(hold_moves, 3.0)
        vol = s["vol"]
        base_vol = float(np.median(vol[i - 24:i])) if i >= 24 else float(vol[i])
        atr_week = float(np.median(s["atr1h"][i - 167:i + 1]))
        tight = int(np.sum(np.abs(s["ret1"][i - 167:i + 1]) < 0.5 * atr))
        h1 = first_hour(moves, 1.0)
        h2 = first_hour(moves, 2.0)
        h3 = first_hour(moves, 3.0)
        min6 = min(moves[:6]) if len(moves) >= 1 else None
        rows.append({
            "t": int(e["t"]),
            "time": sp.iso(int(e["t"])),
            "sym": e["sym"],
            "coin": e["coin"],
            "side": "LONG" if side == 1 else "SHORT",
            "side_n": side,
            "i": int(i),
            "hold_h": int(e["hold_h"]),
            "reason": e["reason"],
            "net_pct": round(float(e["net"]) * 100.0, 2),
            "net": float(e["net"]),
            "atr": atr,
            "age_h": trend_age(s["px"], s["ema240"], i, side),
            "compression": round(atr / max(atr_week, 1e-8), 3),
            "tight_h": tight,
            "breadth": breadth_at(series, int(e["t"]), side),
            "carry": round(carry_z(s, i, side), 3),
            "dist0": None if dist_atr(s, i, side, atr, 0) is None else round(dist_atr(s, i, side, atr, 0), 3),
            "dist24": None if dist_atr(s, i, side, atr, 24) is None else round(dist_atr(s, i, side, atr, 24), 3),
            "dist72": None if dist_atr(s, i, side, atr, 72) is None else round(dist_atr(s, i, side, atr, 72), 3),
            "dist168": None if dist_atr(s, i, side, atr, 168) is None else round(dist_atr(s, i, side, atr, 168), 3),
            "vol_ratio": round(float(vol[i]) / max(base_vol, 1e-8), 3),
            "h1": h1,
            "h2": h2,
            "h3": h3,
            "min6": None if min6 is None else round(min6, 3),
            "peak_atr": round(peak_held, 3),
            "fwd_peak_atr": round(peak, 3),
            "exit_atr": round(exit_atr, 3),
            "give_atr": round(peak_held - exit_atr, 3),
            "waves": n_waves,
            "pulls": n_pull,
            "waves2": w2,
            "pulls2": p2,
            "waves3": w3,
            "pulls3": p3,
            "deepest_pull": round(deepest, 3),
            "path": [round(m, 3) for m in moves[:HORIZON]],
        })
    return rows


def path_median(rows, n=36):
    out = []
    for h in range(n):
        vals = [r["path"][h] for r in rows if len(r["path"]) > h]
        out.append(0.0 if not vals else round(float(np.median(vals)), 3))
    return out


def pack(rows):
    return {
        "n": len(rows),
        "age_h": med([r["age_h"] for r in rows]),
        "compression": med([r["compression"] for r in rows]),
        "tight_h": med([r["tight_h"] for r in rows]),
        "breadth": med([r["breadth"] for r in rows]),
        "carry": med([r["carry"] for r in rows]),
        "dist0": med([r["dist0"] for r in rows]),
        "dist24": med([r["dist24"] for r in rows]),
        "dist72": med([r["dist72"] for r in rows]),
        "vol_ratio": med([r["vol_ratio"] for r in rows]),
        "h1": med([r["h1"] for r in rows]),
        "h2": med([r["h2"] for r in rows]),
        "h3": med([r["h3"] for r in rows]),
        "hit1_6h": share([r["h1"] is not None and r["h1"] <= 6 for r in rows]),
        "hit2_12h": share([r["h2"] is not None and r["h2"] <= 12 for r in rows]),
        "never_deep": share([r["min6"] is not None and r["min6"] > -0.5 for r in rows]),
        "min6": med([r["min6"] for r in rows]),
        "peak_atr": med([r["peak_atr"] for r in rows]),
        "give_atr": med([r["give_atr"] for r in rows]),
        "waves": med([r["waves"] for r in rows]),
        "pulls": med([r["pulls"] for r in rows]),
        "waves2": med([r["waves2"] for r in rows]),
        "pulls2": med([r["pulls2"] for r in rows]),
        "waves3": med([r["waves3"] for r in rows]),
        "pulls3": med([r["pulls3"] for r in rows]),
        "hold_h": med([r["hold_h"] for r in rows]),
        "net_pct": med([r["net_pct"] for r in rows]),
        "fresh48": share([r["age_h"] <= 48 for r in rows]),
        "mature168": share([r["age_h"] >= 168 for r in rows]),
    }


def main():
    cands, series = sp.build_tranche_candidates()
    rows = dissect(cands, series)
    rows.sort(key=lambda r: r["net"], reverse=True)
    top = rows[:25]
    rest = rows[25:]
    pos = [r["net"] for r in rows if r["net"] > 0]
    top_pos = sum(r["net"] for r in top if r["net"] > 0)
    top5_pos = sum(r["net"] for r in rows[:5] if r["net"] > 0)
    pos_sum = sum(pos) or 1.0
    book = sp.simulate_window(cands)
    taken = sorted(book["trades"], key=lambda t: t["pnl_usd"], reverse=True)
    win_pnl = [t["pnl_usd"] for t in taken if t["pnl_usd"] > 0]
    win_sum = sum(win_pnl) or 1.0
    report = {
        "n": len(rows),
        "top_share_of_positive_net": round(top_pos / pos_sum, 3),
        "top5_share_of_positive_net": round(top5_pos / pos_sum, 3),
        "book_n": book["n"],
        "book_final": book["final"],
        "book_top5_share": round(sum(win_pnl[:5]) / win_sum, 3),
        "top": pack(top),
        "rest": pack(rest),
        "path_top": path_median(top),
        "path_rest": path_median(rest),
        "passport": [
            {
                "time": r["time"],
                "coin": r["coin"],
                "side": r["side"],
                "hold_h": r["hold_h"],
                "reason": r["reason"],
                "net_pct": r["net_pct"],
                "peak_atr": r["peak_atr"],
                "give_atr": r["give_atr"],
                "waves": r["waves"],
                "pulls": r["pulls"],
                "pulls2": r["pulls2"],
                "pulls3": r["pulls3"],
                "age_h": r["age_h"],
                "breadth": r["breadth"],
                "h2": r["h2"],
                "min6": r["min6"],
            }
            for r in top
        ],
    }
    # Drop the long path from the saved feature rows; the report keeps the medians.
    slim = []
    for r in rows:
        item = dict(r)
        item.pop("path", None)
        slim.append(item)
    payload = {"report": report, "rows": slim}
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("n", len(rows), "top5 net share", report["top5_share_of_positive_net"], "book", book["final"], "book top5", report["book_top5_share"])
    print("TOP", json.dumps(report["top"], ensure_ascii=False))
    print("REST", json.dumps(report["rest"], ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
