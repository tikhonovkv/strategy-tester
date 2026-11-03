# -*- coding: utf-8 -*-
"""Drought-triggered 3x Carry/Momentum sleeve beside Schwartz 7x.

The sleeve turns ON only when Schwartz is in a flat-market drought:
  - 0 open Schwartz positions for >= 5 days, AND
  - at most 1 Schwartz trade in the last 14 days.
When active, the sleeve deploys at 3x gross leverage (0.5x equity per leg across
3 longs and 3 shorts, rebalanced every 48 hours) with a -2.5% unlevered spread stop
(-7.5% of sleeve equity) checked every hour. As soon as a Schwartz signal fires,
the sleeve closes immediately and hands 100% of equity back to Schwartz.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

ONE_WAY = 0.00055 + 0.0003  # half of the 0.11% round trip, plus 3 bps
HOLD_H = 48
SLEEVE_LEV = 3.0            # 3x gross (0.5x equity per leg across 6 legs)
UNLEV_STOP = 0.025          # -2.5% unlevered basket move = -7.5% of sleeve equity at 3x
BASKET_STOP = UNLEV_STOP * SLEEVE_LEV
QUIET_DAYS = 5.0            # must be flat in Schwartz for >= 5 days
MAX_RECENT_TRADES = 1       # and <= 1 Schwartz entry in the last 14 days
RECENT_WINDOW_D = 14.0
STOP_STREAK_LIMIT = 2       # after 2 consecutive basket stops, pause basket
STOP_COOLDOWN_H = 144.0     # for 6 days (144 hours)
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
OUT = sp.LAB / "_sleeve_recheck.json"


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


def idx_at(s, t):
    ts = s["close_ts"]
    j = int(np.searchsorted(ts, int(t), side="right")) - 1
    if j < 0:
        return None
    if int(t) - int(ts[j]) > 6 * sp.HOUR:
        return None
    return j


def price_at(s, t):
    j = idx_at(s, t)
    if j is None:
        return None
    return float(s["px"][j])


def chart_path(s, t0, t1, limit=24):
    """Hourly closes from two bars before entry through two bars after exit."""
    ts = s["close_ts"]
    px = s["px"]
    i0 = int(np.searchsorted(ts, int(t0)))
    i1 = int(np.searchsorted(ts, int(t1), side="right")) - 1
    if i0 < 0 or i0 >= len(px) or i1 < i0:
        return []
    lo = max(0, i0 - 2)
    hi = min(len(px) - 1, i1 + 2)
    idx = list(range(lo, hi + 1))
    if len(idx) > limit:
        head, tail = idx[:2], idx[-2:]
        mid = idx[2:-2]
        keep = limit - 4
        step = (len(mid) - 1) / max(keep - 1, 1) if mid else 1
        picked = [mid[min(len(mid) - 1, int(round(k * step)))] for k in range(keep)] if mid else []
        idx = head + picked + tail
    return [round(float(px[i]), 6) for i in idx]


def funding_paid(s, t0, t1):
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, t0, side="right"))
    hi = int(np.searchsorted(f_ts, t1, side="right"))
    if hi <= lo:
        return 0.0
    return float(f_rate[lo:hi].sum())


def leg_ret(s, t0, t1, side):
    p0, p1 = price_at(s, t0), price_at(s, t1)
    if p0 is None or p1 is None or p0 <= 0:
        return 0.0
    raw = p1 / p0 - 1.0
    paid = funding_paid(s, t0, t1)
    ret = raw - paid if side == 1 else -raw + paid
    return max(ret, -1.0)


def target_weights(series, syms, t, exclude=()):
    rows = []
    for sym in syms:
        s = series.get(sym)
        if s is None or sym in exclude:
            continue
        i = idx_at(s, t)
        if i is None or i < 336:
            continue
        px = float(s["px"][i])
        base = float(s["px"][i - 336])
        atr = max(float(s["atr1h"][i]), 0.002)
        if base <= 0 or px <= 0:
            continue
        mom = (px / base - 1.0) / (atr * np.sqrt(336.0))
        fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
        if fi < 21:
            continue
        window = s["f_rate"][fi - 20:fi + 1]
        carry = -float(np.mean(window))
        rows.append((sym, mom, carry))
    if len(rows) < 8:
        return {}
    moms = np.array([r[1] for r in rows])
    carries = np.array([r[2] for r in rows])
    mz = (moms - moms.mean()) / (moms.std() + 1e-8)
    cz = (carries - carries.mean()) / (carries.std() + 1e-8)
    alpha = 0.75 * mz + 0.25 * cz
    order = np.argsort(alpha)
    weights = {}
    for k in order[:3]:
        weights[rows[int(k)][0]] = -1.0 / 6.0
    for k in order[-3:]:
        weights[rows[int(k)][0]] = 1.0 / 6.0
    return weights


def rebalance_times(btc, start, end):
    ts = btc["close_ts"]
    anchor = stamp(2022, 1, 1)
    out = []
    t = anchor
    while t < end:
        if t >= start:
            j = int(np.searchsorted(ts, t))
            if j < len(ts):
                bar = int(ts[j])
                if start <= bar < end and (not out or bar != out[-1]):
                    out.append(bar)
        t += HOLD_H * sp.HOUR
    return out


def pack(trades, curve, start_eq=2000.0):
    final = curve[-1][1] if curve else start_eq
    peak = start_eq
    max_dd = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    wins = sorted((x for x in trades if x > 0), reverse=True)
    return {
        "n": len(trades),
        "ret": round((final / start_eq - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "final": round(final, 2),
        "top5": round(sum(wins[:5]) / (sum(wins) or 1.0), 3),
        "curve": curve,
    }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


class DroughtSleeveBook:
    """3x sleeve book active only during Schwartz droughts, with exact leg PnL reconciliation."""

    def __init__(self, lev: float = SLEEVE_LEV, unlev_stop: float = UNLEV_STOP):
        self.lev = lev
        self.unlev_stop = unlev_stop
        self.equity = 0.0
        self.legs = []  # sym, side, notional, margin, entry_t, entry_px, open_fee
        self.closed = []
        self.events = []

    def value(self, series, t) -> float:
        if not self.legs and self.equity <= 0:
            return 0.0
        unreal = sum(
            leg["notional"] * leg_ret(series[leg["sym"]], leg["entry_t"], t, leg["side"])
            for leg in self.legs
        )
        return max(0.0, self.equity + unreal)

    def _emit_leg(self, series, leg, t, pnl: float, eq_now: float, reason: str):
        sym = leg["sym"]
        p0 = float(leg["entry_px"])
        p1 = price_at(series[sym], t) or p0
        margin = max(1.0, float(leg["margin"]))
        ret_pct = pnl / margin * 100.0
        self.events.append(pnl)
        self.closed.append({
            "path": chart_path(series[sym], leg["entry_t"], t),
            "sym": sym,
            "coin": sym.replace("USDT", ""),
            "side": int(leg["side"]),
            "t": int(leg["entry_t"]),
            "exit_ts": int(t),
            "entry_price": round(p0, 6),
            "exit_price": round(float(p1), 6),
            "qty": round(float(leg["notional"]) / max(p0, 1e-8), 6),
            "margin": round(margin, 2),
            "eff_lev": round(self.lev, 1),
            "pnl_usd": round(pnl, 2),
            "pnl_exact": float(pnl),
            "raw_net_pct": round(ret_pct / max(self.lev, 1.0), 2),
            "pnl_pct": round(ret_pct, 2),
            "eq": round(float(eq_now), 2),
            "reason": reason,
            "book": "sleeve",
            "pyr": False,
            "hold_h": max(1, int((int(t) - int(leg["entry_t"])) / sp.HOUR)),
        })

    def close_all(self, series, t, outside_eq: float, reason: str) -> float:
        if not self.legs:
            ret_cash = self.equity
            self.equity = 0.0
            return ret_cash
        leg_data = []
        unreal_total = 0.0
        close_notional = 0.0
        for leg in self.legs:
            r = leg_ret(series[leg["sym"]], leg["entry_t"], t, leg["side"])
            u = leg["notional"] * r
            c_not = max(0.0, leg["notional"] * (1.0 + r))
            unreal_total += u
            close_notional += c_not
            leg_data.append((leg, u, c_not))
        close_fee = close_notional * ONE_WAY
        net_val = max(0.0, self.equity + unreal_total - close_fee)
        eq_now = outside_eq + net_val
        n = len(leg_data)
        for leg, u, c_not in leg_data:
            fee_share = (c_not / close_notional * close_fee) if close_notional > 0 else (close_fee / n)
            pnl = u - leg["open_fee"] - fee_share
            self._emit_leg(series, leg, t, pnl, eq_now, reason)
        self.legs = []
        self.equity = 0.0
        return net_val

    def check_stop(self, series, t, outside_eq: float) -> float | None:
        if not self.legs or self.unlev_stop <= 0 or self.equity <= 0:
            return None
        unreal = sum(
            leg["notional"] * leg_ret(series[leg["sym"]], leg["entry_t"], t, leg["side"])
            for leg in self.legs
        )
        if (unreal / self.equity) <= -self.unlev_stop * self.lev:
            return self.close_all(series, t, outside_eq, "basket_stop")
        return None

    def rebalance(self, series, t, cash_in: float, weights: dict, outside_eq: float) -> float:
        current = {}
        old_legs = []
        if self.legs:
            unreal_total = 0.0
            for leg in self.legs:
                r = leg_ret(series[leg["sym"]], leg["entry_t"], t, leg["side"])
                u = leg["notional"] * r
                unreal_total += u
                end_notional = max(0.0, leg["notional"] * (1.0 + r))
                current[leg["sym"]] = leg["side"] * end_notional
                old_legs.append((leg, u, leg["side"] * end_notional))
            self.equity = max(0.0, self.equity + unreal_total)
            self.legs = []
        if cash_in > 0:
            self.equity += cash_in

        if self.equity < 50.0 or not weights:
            turnover = sum(abs(v) for v in current.values())
            fee = turnover * ONE_WAY
            net_val = max(0.0, self.equity - fee)
            eq_now = outside_eq + net_val
            for leg, u, signed_end in old_legs:
                fee_share = (abs(signed_end) / turnover * fee) if turnover > 0 else 0.0
                pnl = u - leg["open_fee"] - fee_share
                self._emit_leg(series, leg, t, pnl, eq_now, "rebalance_48h")
            self.equity = 0.0
            return net_val

        targets = {sym: w * self.lev * self.equity for sym, w in weights.items()}
        names = set(current) | set(targets)
        turnover = sum(abs(targets.get(sym, 0.0) - current.get(sym, 0.0)) for sym in names)
        total_fee = turnover * ONE_WAY

        # Attribute exit portion of turnover fee to closing legs, remainder to opening legs
        used_close_fee = 0.0
        eq_before_new = outside_eq + max(0.0, self.equity - total_fee)
        for leg, u, signed_end in old_legs:
            sym = leg["sym"]
            new_signed = targets.get(sym, 0.0)
            close_turn = abs(signed_end - new_signed) if sym in targets else abs(signed_end)
            fee_sym = close_turn * ONE_WAY * (0.5 if sym in targets else 1.0)
            used_close_fee += fee_sym
            pnl = u - leg["open_fee"] - fee_sym
            self._emit_leg(series, leg, t, pnl, eq_before_new, "rebalance_48h")

        rem_open_fee = max(0.0, total_fee - used_close_fee)
        self.equity = max(0.0, self.equity - total_fee)
        if self.equity < 50.0:
            ret_cash = self.equity
            self.equity = 0.0
            return ret_cash

        w_sum = sum(abs(x) for x in weights.values()) or 1.0
        for sym, w in weights.items():
            share = abs(w) / w_sum
            margin = self.equity * share
            notional = margin * self.lev
            p0 = price_at(series[sym], t) or 0.0
            self.legs.append({
                "sym": sym,
                "side": 1 if w > 0 else -1,
                "notional": notional,
                "margin": margin,
                "entry_t": int(t),
                "entry_px": p0,
                "open_fee": rem_open_fee * share,
            })
        return 0.0


def simulate(cands, series, syms, start, end, mode="nox", start_eq=2000.0):
    btc = series["BTCUSDT"]
    rebalance = set(rebalance_times(btc, start, end))
    excluded = tuple(list(sp.ALTS_7) + ["BTCUSDT"])
    by_t = {}
    times = set(rebalance)
    for ts_bar in btc["close_ts"]:
        tb = int(ts_bar)
        if start <= tb < end:
            times.add(tb)
    for e in cands:
        if start <= int(e["t"]) < end:
            by_t.setdefault(int(e["t"]), []).append(e)
            times.add(int(e["t"]))
            times.add(int(e["exit_ts"]))
            times.add(int(e.get("exit_ts_nopyr") or e["exit_ts"]))
            pyr_ts = int(e.get("pyr_ts") or 0)
            if start <= pyr_ts < end:
                times.add(pyr_ts)

    book = DroughtSleeveBook(lev=SLEEVE_LEV, unlev_stop=UNLEV_STOP)
    start_eq = float(start_eq)
    free = start_eq
    reserved = 0.0
    open_pos = []
    schwartz = []
    schwartz_trades = []
    curve = [(start, start_eq)]
    loss_streak = 0
    win_streak = 0
    prior_entries = [
        e for e in cands
        if start - int(RECENT_WINDOW_D * sp.DAY) <= int(e["t"]) < start
        and not (int(e.get("side") or 0) == 1 and e["sym"] not in {"BTCUSDT", "ETHUSDT"} and int(e.get("rank24", 1)) > 2)
        and (e["sym"] in sp.CORE_4 or (e["hot_n"] >= 4 and e["hot_avg"] >= 0.015))
    ]
    schwartz_entry_times = sorted({int(e["t"]) for e in prior_entries})
    last_schwartz_active_ts = max([start] + [int(e.get("exit_ts_nopyr") or e["exit_ts"]) for e in prior_entries])
    stop_streak = 0
    cooldown_until = -10**18

    def equity(t):
        return free + reserved + book.value(series, t)

    sorted_times = sorted(times)
    for t in sorted_times:
        if open_pos:
            last_schwartz_active_ts = t

        # 1. Hourly check of -2.5% unlevered (-7.5% at 3x) basket stop
        stopped_cash = book.check_stop(series, t, free + reserved)
        if stopped_cash is not None:
            free += stopped_cash
            stop_streak += 1
            if STOP_STREAK_LIMIT > 0 and stop_streak >= STOP_STREAK_LIMIT:
                cooldown_until = t + int(STOP_COOLDOWN_H * sp.HOUR)

        # 2. Close finished Schwartz trades
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                use_pyr = bool(p.get("use_pyr")) and float(p.get("pyr_scale") or 0.0) > 0.0
                if use_pyr:
                    tot = e["net"] + p["pyr_scale"] * e["pyr_net"]
                    min_move = e["min_move"]
                    reason_live = e["reason"]
                    exit_px_live = e["exit_px"]
                else:
                    tot = e.get("net_nopyr", e["net"])
                    min_move = e.get("min_move_nopyr", e["min_move"])
                    reason_live = e.get("reason_nopyr", e["reason"])
                    exit_px_live = price_at(series[e["sym"]], p["exit_ts"]) or e["exit_px"]
                locked = float(p.get("pyr_locked") or 0.0)
                loss_cap = -(p["margin"] + locked)
                if min_move * 7.0 <= -1.0:
                    pnl = loss_cap
                else:
                    pnl = max(loss_cap, p["margin"] * 7.0 * tot)
                free += p["margin"] + locked + pnl
                reserved -= p["margin"] + locked
                if pnl < 0:
                    loss_streak, win_streak = loss_streak + 1, 0
                else:
                    loss_streak, win_streak = 0, win_streak + 1
                last_schwartz_active_ts = t
                schwartz.append(float(pnl))
                eq_after = equity(t)
                contrib = pnl / max(float(p.get("eq_entry") or start_eq), 1.0)
                hold_h = max(1, int((int(p["exit_ts"]) - int(e["t"])) / sp.HOUR))
                schwartz_trades.append({
                    "t": int(e["t"]),
                    "ts": int(e["t"]),
                    "exit_ts": int(p["exit_ts"]),
                    "time": _iso(int(e["t"])),
                    "exit_time": _iso(int(p["exit_ts"])),
                    "hold_h": hold_h,
                    "sym": e["sym"],
                    "coin": e["coin"],
                    "side": int(e["side"]),
                    "entry_price": e["entry_px"],
                    "exit_price": round(float(exit_px_live), 6),
                    "qty": round(float(p["qty"]), 6),
                    "margin": round(float(p["margin"]), 2),
                    "eff_lev": 7.0,
                    "raw_net_pct": round(float(tot) * 100.0, 2),
                    "pnl_usd": round(float(pnl), 2),
                    "pnl_exact": float(pnl),
                    "pnl_pct": round(float(contrib) * 100.0, 2),
                    "eq": round(float(eq_after), 2),
                    "reason": reason_live,
                    "pyr": bool(use_pyr),
                    "book": "schwartz",
                    "path": e.get("path"),
                })
            else:
                still.append(p)
        open_pos = still

        # 3. Pyramid adds
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

        # 4. New Schwartz entries (immediately close drought basket if open)
        batch = by_t.get(t, [])
        if batch and len(open_pos) < 2:
            batch = sorted(
                batch,
                key=lambda x: (
                    1 if x["sym"] in sp.CORE_4 else 0,
                    float(x.get("vol_surge", 1.0)) + 5.0 * float(x.get("hot_avg", 0.0)),
                ),
                reverse=True,
            )
            busy = {p["e"]["sym"] for p in open_pos}
            for e in batch:
                if len(open_pos) >= 2 or e["sym"] in busy:
                    continue
                if int(e.get("side") or 0) == 1 and e["sym"] not in {"BTCUSDT", "ETHUSDT"} and int(e.get("rank24", 1)) > 2:
                    continue
                if e["sym"] not in sp.CORE_4 and (e["hot_n"] < 4 or e["hot_avg"] < 0.015):
                    continue
                if book.legs or book.equity > 0:
                    free += book.close_all(series, t, free + reserved, "schwartz_signal")
                sizing_eq = free + reserved
                mult = sp.size_multiplier(
                    loss_streak, win_streak,
                    int(e.get("c_lstreak") or 0),
                    float(e.get("hot_avg") or 0.0),
                    win_cap=1.15,
                )
                stop_dist = max(1.6 * e["atr"], 0.008)
                target = (sizing_eq * 0.075 * mult) / stop_dist
                raw_margin = min(target / 7.0, sizing_eq * 0.60, free * 0.90)
                if raw_margin < 15.0:
                    continue
                qty = sp.fit_lot_qty(e["sym"], (raw_margin * 7.0) / max(e["entry_px"], 1e-8))
                if qty <= 0:
                    continue
                margin = (qty * e["entry_px"]) / 7.0
                if margin < 15.0 or margin > free:
                    continue
                free -= margin
                reserved += margin
                open_pos.append({
                    "e": e,
                    "qty": qty,
                    "eq_entry": sizing_eq,
                    "exit_ts": int(e.get("exit_ts_nopyr") or e["exit_ts"]),
                    "use_pyr": False,
                    "margin": margin,
                    "pyr_scale": 0.0,
                    "pyr_ts": int(e.get("pyr_ts") or 0),
                    "pyr_locked": 0.0,
                    "pyr_checked": False,
                })
                busy.add(e["sym"])
                last_schwartz_active_ts = t
                schwartz_entry_times.append(t)
                stop_streak = 0

        # 5. Drought basket rebalance on 48h grid
        if t in rebalance and not open_pos:
            if book.legs:
                stop_streak = 0
            quiet_h = (t - last_schwartz_active_ts) / sp.HOUR
            recent_cnt = sum(1 for ts_e in schwartz_entry_times if t - ts_e <= RECENT_WINDOW_D * sp.DAY)
            is_drought = (
                (quiet_h >= QUIET_DAYS * 24.0)
                and (recent_cnt <= MAX_RECENT_TRADES)
                and (t >= cooldown_until)
            )
            if is_drought:
                cash_in = free
                free = 0.0
                returned = book.rebalance(series, t, cash_in, target_weights(series, syms, t, excluded), free + reserved)
                free += returned
            elif book.legs or book.equity > 0:
                free += book.close_all(series, t, free + reserved, "rebalance_48h")

        eq_now = equity(t)
        if t in rebalance or t in by_t or (t - start) % (6 * sp.HOUR) == 0:
            curve.append((int(t), round(eq_now, 2)))
        if eq_now <= 0:
            break

    last_t = sorted_times[-1] if sorted_times else end
    if book.legs or book.equity > 0:
        free += book.close_all(series, last_t, free + reserved, "rebalance_48h")
    final_eq = equity(last_t)
    if curve[-1][0] != last_t:
        curve.append((last_t, round(final_eq, 2)))
    else:
        curve[-1] = (curve[-1][0], round(final_eq, 2))
    gap = final_eq - start_eq - sum(schwartz) - sum(book.events)
    out = pack(schwartz + book.events, curve, start_eq=start_eq)
    out["schwartz_n"] = len(schwartz)
    out["sleeve_pnl"] = round(sum(book.events), 2)
    out["gap"] = round(gap, 2)
    out["sleeve_trades"] = book.closed
    out["schwartz_trades"] = schwartz_trades
    return out


def baseline_window(cands, start, end, start_eq=2000.0):
    part = [e for e in cands if start <= int(e["t"]) < end]
    res = sp.simulate_window(part, start_eq=float(start_eq))
    wins = sorted((t["pnl_usd"] for t in res["trades"] if t["pnl_usd"] > 0), reverse=True)
    return {
        "n": res["n"],
        "ret": res["ret"],
        "dd": res["dd"],
        "final": res["final"],
        "top5": round(sum(wins[:5]) / (sum(wins) or 1.0), 3),
        "schwartz_n": res["n"],
        "sleeve_pnl": 0.0,
        "gap": 0.0,
        "curve": res["curve"],
    }
