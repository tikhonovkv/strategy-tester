# -*- coding: utf-8 -*-
"""Local stand for the four stabilization circuits. No upload, no live orders."""
import json
from pathlib import Path
import numpy as np
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

START = 2000.0


def atr_rank(s, i):
    if i < 720:
        return 0.0
    window = s["atr1h"][i - 719:i + 1]
    return float(np.mean(window <= float(s["atr1h"][i])))


def partial_net(s, ohlc, i, side, full, slip):
    """10% locked when close move first reaches +4 ATR (+5 if pyramid already on)."""
    entry = float(s["px"][i]) * (1.0 + side * slip)
    atr = max(float(s["atr1h"][i]), 0.002)
    pyr = False
    lock_move = None
    lock_ts = int(s["close_ts"][i])
    exit_ts = int(full["exit_ts"])
    for j in range(i + 1, min(len(s["px"]), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
            break
        if int(s["close_ts"][j]) > exit_ts:
            break
        c = float(s["px"][j])
        move = side * (c / entry - 1.0)
        if (not pyr) and move >= 2.0 * atr:
            pyr = True
        arm = 5.0 * atr if pyr else 4.0 * atr
        if move >= arm:
            px = c * (1.0 - side * slip)
            lock_move = side * (px / entry - 1.0)
            lock_ts = int(s["close_ts"][j])
            break
    if lock_move is None:
        return full["net"], full["pyr_net"]
    f_full = L.fund_adj(s, side, int(s["close_ts"][i]), int(full["exit_ts"]))
    f_lock = L.fund_adj(s, side, int(s["close_ts"][i]), lock_ts)
    raw_end = side * (float(full["exit_px"]) / entry - 1.0)
    net = 0.10 * (lock_move + f_lock - sp.TAKER_FEE) + 0.90 * (raw_end + f_full - sp.TAKER_FEE)
    if full["pyr"]:
        pyr_move = 2.0 * atr
        pyr_net = 0.50 * (
            0.10 * (lock_move - pyr_move) + 0.90 * (raw_end - pyr_move) + 0.5 * (0.10 * f_lock + 0.90 * f_full) - sp.TAKER_FEE
        )
    else:
        pyr_net = 0.0
    return float(net), float(pyr_net)


def load_rows():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    rows = []
    for e in entries:
        s = series[e["sym"]]
        i = e["i"]
        if i < 96:
            continue
        ex = L.apply_exit(s, ohlc[e["sym"]], i, e["side"], close_pol, L.SLIP, False)
        if ex is None:
            continue
        pnet, ppyr = partial_net(s, ohlc, i, e["side"], ex, L.SLIP)
        rows.append({
            "t": e["t"], "sym": e["sym"], "side": e["side"],
            "vol_rank": atr_rank(s, i),
            "net_full": ex["net"], "pyr_full": ex["pyr_net"],
            "net_p10": pnet, "pyr_p10": ppyr,
            **ex,
        })
    return L.with_hot(rows)


def simulate(part, vol_half=False, no_win_boost=False, ratchet=False, partial10=False, start_eq=START):
    by_t, times = {}, set()
    for e in part:
        if e["sym"] not in sp.CORE_4 and (e["hot_n"] < 4 or e["hot_avg"] < 0.015):
            continue
        ee = dict(e)
        if partial10:
            ee["net"] = e["net_p10"]
            ee["pyr_net"] = e["pyr_p10"]
        else:
            ee["net"] = e["net_full"]
            ee["pyr_net"] = e["pyr_full"]
        by_t.setdefault(e["t"], []).append(ee)
        times.add(e["t"])
        times.add(e["exit_ts"])
    free = start_eq
    reserved = 0.0
    open_pos = []
    curve = []
    underwater = []
    peak = free
    max_dd = 0.0
    loss_streak = 0
    win_streak = 0
    base_capital = start_eq
    risk_pct = 0.075
    lev = 7.0
    for t in sorted(times):
        prev_peak = peak
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                tot = e["net"] + p["pyr_scale"] * e["pyr_net"]
                if e["min_move"] * lev <= -1.0:
                    pnl = -p["margin"]
                else:
                    pnl = max(-p["margin"], p["margin"] * lev * tot)
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
                curve.append((int(t), round(eq, 2)))
                underwater.append((int(t), round(-(peak - eq) / peak * 100.0, 2) if peak else 0.0))
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break
        if ratchet and eq > prev_peak and eq > base_capital:
            base_capital = base_capital + 0.50 * (eq - base_capital)
        peak = max(peak, eq)
        batch = by_t.get(t, [])
        if not batch or len(open_pos) >= 2:
            continue
        batch = sorted(batch, key=lambda x: (1 if x["sym"] in sp.CORE_4 else 0, x["hot_avg"]), reverse=True)
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= 2 or e["sym"] in busy:
                continue
            if loss_streak > 0:
                mult = 0.45 ** min(loss_streak, 2)
            elif (not no_win_boost) and win_streak >= 2:
                mult = 1.80
            elif (not no_win_boost) and win_streak == 1:
                mult = 1.25
            elif no_win_boost and win_streak >= 1:
                mult = 1.15
            else:
                mult = 1.0
            if e["c_lstreak"] >= 4:
                mult *= 0.50
            elif e["hot_avg"] > 0.008:
                mult *= 1.15
            if vol_half and e.get("vol_rank", 0.0) > 0.80:
                mult *= 0.50
            if ratchet:
                sizing_eq = base_capital + 0.50 * max(0.0, eq - base_capital)
                dd = (peak - eq) / peak if peak > 0 else 0.0
                risk = 0.040 if dd >= 0.12 else risk_pct
            else:
                sizing_eq = eq
                risk = risk_pct
            stop_dist = max(1.6 * e["atr"], 0.008)
            target_notional = (sizing_eq * risk * mult) / stop_dist
            raw_margin = min(target_notional / lev, eq * 0.60, free * 0.90)
            if raw_margin < 15.0:
                continue
            qty = sp.fit_lot_qty(e["sym"], (raw_margin * lev) / max(e["entry_px"], 1e-8))
            if qty <= 0:
                continue
            margin = (qty * e["entry_px"]) / lev
            if margin < 15.0 or margin > free:
                continue
            free -= margin
            reserved += margin
            rem = max(0.0, free)
            need = 0.50 * margin
            pyr_scale = min(1.0, (rem * 0.90) / max(need, 1e-8)) if e["pyr"] and rem >= 10.0 else 0.0
            open_pos.append({"e": e, "exit_ts": e["exit_ts"], "qty": qty, "margin": margin, "pyr_scale": pyr_scale, "eq_entry": eq})
            busy.add(e["sym"])
    final = free + reserved
    return {
        "ret": round((final / start_eq - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "final": round(final, 2),
        "calmar": round(((final / start_eq - 1.0) * 100.0) / max(max_dd * 100.0, 0.1), 2),
        "curve": curve,
        "underwater": underwater,
    }


def slice_book(rows, a, b, **flags):
    part = [r for r in rows if a <= r["t"] < b]
    return simulate(part, **flags)


def main():
    rows = load_rows()
    print("rows", len(rows), flush=True)
    windows = [
        ("2022", M.ms(2022, 1, 1), M.ms(2023, 1, 1)),
        ("2023", M.ms(2023, 1, 1), M.ms(2024, 1, 1)),
        ("2024", M.ms(2024, 1, 1), M.ms(2025, 1, 1)),
        ("2025", M.ms(2025, 1, 1), M.ms(2026, 1, 1)),
        ("2026H1", M.ms(2026, 1, 1), M.ms(2026, 7, 22)),
        ("2026Q", M.ms(2026, 7, 22), M.ms(2026, 11, 2)),
        ("full", M.ms(2022, 1, 1), M.ms(2026, 11, 2)),
    ]
    variants = {
        "rules": {},
        "vol_half": {"vol_half": True},
        "no_boost": {"no_win_boost": True},
        "ratchet": {"ratchet": True},
        "partial10": {"partial10": True},
        "all4": {"vol_half": True, "no_win_boost": True, "ratchet": True, "partial10": True},
    }
    report = {}
    curves = {}
    for name, flags in variants.items():
        report[name] = {}
        for label, a, b in windows:
            res = slice_book(rows, a, b, **flags)
            report[name][label] = {k: res[k] for k in ("ret", "dd", "final", "calmar")}
            if label == "full":
                curves[name] = {"curve": res["curve"], "underwater": res["underwater"]}
        print(name, "full", report[name]["full"], flush=True)
    out = Path(__file__).resolve().parent / "_stabilize_audit.json"
    out.write_text(json.dumps({"report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(__file__).resolve().parent.joinpath("_stabilize_curves.json").write_text(
        json.dumps(curves), encoding="utf-8"
    )
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
