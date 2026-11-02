# -*- coding: utf-8 -*-
"""Strict Carry/Momentum sleeve beside the current Schwartz book.

The sleeve is 1x gross: three longs and three shorts, rebalanced every 48 hours,
with a -3.0% basket stop-loss checked every hour.
A fee of 0.055% plus 3 bps is charged on the notional that actually trades.
Each dollar sits in one place, and leg PnL sums exactly to the equity change.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

ONE_WAY = 0.00055 + 0.0003  # half of the 0.11% round trip, plus 3 bps
HOLD_H = 48
BASKET_STOP = 0.030  # -3.0% basket stop-loss on the 6-leg sleeve
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


class SleeveBook:
    """One gross-1 basket with -3.0% basket stop and exact leg-level PnL reconciliation."""

    def __init__(self, basket_stop: float = BASKET_STOP):
        self.basket_stop = basket_stop
        self.positions = []
        self.cash = 0.0
        self.closed = []
        self.events = []

    def value(self, series, t):
        val = self.cash
        for pos in self.positions:
            r = leg_ret(series[pos["sym"]], pos["t0"], t, pos["side"])
            val += pos["notional"] * (1.0 + r)
        return val

    def absorb(self, amount):
        if amount > 0:
            self.cash += amount

    def _mark_to(self, series, t):
        for pos in self.positions:
            if int(t) <= int(pos["t0"]):
                continue
            r = leg_ret(series[pos["sym"]], pos["t0"], t, pos["side"])
            profit = pos["notional"] * r
            pos["cum_pnl"] += profit
            pos["notional"] = max(0.0, pos["notional"] + profit)
            pos["t0"] = int(t)

    def _emit_close(self, series, pos, t, reason: str, eq_now: float):
        sym = pos["sym"]
        p0 = float(pos["entry_px"])
        p1 = price_at(series[sym], t) or p0
        base = max(1.0, float(pos["base_notional"]))
        pnl = float(pos["cum_pnl"])
        ret_pct = pnl / base * 100.0
        self.events.append(pnl)
        self.closed.append({
            "path": chart_path(series[sym], pos["entry_t"], t),
            "sym": sym,
            "coin": sym.replace("USDT", ""),
            "side": int(pos["side"]),
            "t": int(pos["entry_t"]),
            "exit_ts": int(t),
            "entry_price": round(p0, 6),
            "exit_price": round(float(p1), 6),
            "qty": round(base / max(p0, 1e-8), 6),
            "margin": round(base, 2),
            "eff_lev": 1.0,
            "pnl_usd": round(pnl, 2),
            "pnl_exact": pnl,
            "raw_net_pct": round(ret_pct, 2),
            "pnl_pct": round(ret_pct, 2),
            "eq": round(float(eq_now), 2),
            "reason": reason,
            "book": "sleeve",
            "pyr": False,
            "hold_h": max(1, int((int(t) - int(pos["entry_t"])) / sp.HOUR)),
        })

    def check_stops(self, series, t, outside_eq: float) -> bool:
        """Close all 6 legs if total basket PnL reaches -basket_stop of base notional."""
        if not self.positions or not self.basket_stop:
            return False
        total_base = sum(p["base_notional"] for p in self.positions)
        if total_base <= 0:
            return False
        unreal = 0.0
        for pos in self.positions:
            r = leg_ret(series[pos["sym"]], pos["t0"], t, pos["side"])
            unreal += pos["cum_pnl"] + pos["notional"] * r
        if (unreal / total_base) > -self.basket_stop:
            return False
        self._mark_to(series, t)
        total_after_fee = self.cash + sum(max(0.0, p["notional"] * (1.0 - ONE_WAY)) for p in self.positions)
        eq_now = outside_eq + total_after_fee
        for pos in self.positions:
            fee = pos["notional"] * ONE_WAY
            pos["cum_pnl"] -= fee
            self.cash += max(0.0, pos["notional"] - fee)
            self._emit_close(series, pos, t, "basket_stop", eq_now)
        self.positions = []
        return True

    def rebalance(self, series, t, weights, outside_eq: float = 0.0):
        self._mark_to(series, t)
        current = {p["sym"]: p["side"] * p["notional"] for p in self.positions}
        equity = sum(abs(v) for v in current.values()) + self.cash
        if equity < 50.0 or not weights:
            total_after_fee = self.cash + sum(max(0.0, p["notional"] * (1.0 - ONE_WAY)) for p in self.positions)
            eq_now = outside_eq + total_after_fee
            for pos in self.positions:
                fee = pos["notional"] * ONE_WAY
                pos["cum_pnl"] -= fee
                self.cash += max(0.0, pos["notional"] - fee)
                self._emit_close(series, pos, t, "rebalance_48h", eq_now)
            self.positions = []
            return

        targets = {sym: w * equity for sym, w in weights.items()}
        names = set(current) | set(targets)
        turnover = sum(abs(targets.get(sym, 0.0) - current.get(sym, 0.0)) for sym in names)
        total_fee = turnover * ONE_WAY
        equity2 = max(0.0, equity - total_fee)
        eq_now = outside_eq + equity2

        used_fee = 0.0
        for pos in self.positions:
            sym = pos["sym"]
            old_signed = pos["side"] * pos["notional"]
            new_signed = targets.get(sym, 0.0)
            close_turnover = abs(old_signed - new_signed) if sym in targets else abs(old_signed)
            fee_sym = close_turnover * ONE_WAY * (0.5 if sym in targets else 1.0)
            used_fee += fee_sym
            pos["cum_pnl"] -= fee_sym
            self._emit_close(series, pos, t, "rebalance_48h", eq_now)

        rem_fee = max(0.0, total_fee - used_fee)
        self.positions = []
        if equity2 >= 50.0:
            w_sum = sum(abs(x) for x in weights.values()) or 1.0
            for sym, w in weights.items():
                notion = abs(w) * equity2
                if notion > 1.0:
                    p0 = price_at(series[sym], t) or 0.0
                    open_fee = rem_fee * (abs(w) / w_sum)
                    self.positions.append({
                        "sym": sym,
                        "side": 1 if w > 0 else -1,
                        "notional": notion,
                        "base_notional": notion,
                        "t0": int(t),
                        "entry_t": int(t),
                        "entry_px": p0,
                        "cum_pnl": -open_fee,
                    })
            self.cash = 0.0
        else:
            self.cash = equity2

    def scale_in(self, series, t):
        if self.cash <= 1.0 or not self.positions:
            return
        self._mark_to(series, t)
        base = sum(p["notional"] for p in self.positions)
        add = self.cash
        if base <= 1.0:
            return
        fee = add * ONE_WAY
        scale = (base + add - fee) / base
        if scale <= 0:
            return
        for pos in self.positions:
            share = pos["notional"] / base
            added_notional = (add - fee) * share
            pos["notional"] *= scale
            pos["base_notional"] += added_notional
            pos["cum_pnl"] -= fee * share
        self.cash = 0.0

    def recall(self, series, t, need, outside_eq: float = 0.0):
        if need <= 0:
            return 0.0
        if self.cash >= need:
            self.cash -= need
            return need
        got = self.cash
        self.cash = 0.0
        self._mark_to(series, t)
        equity = sum(p["notional"] for p in self.positions)
        if equity <= 1.0:
            return got
        f = min(1.0, (need - got) / (equity * (1.0 - ONE_WAY)))
        fee = equity * f * ONE_WAY
        released = equity * f - fee
        eq_now = outside_eq + got + max(0.0, equity - fee)
        keep = []
        for pos in self.positions:
            share = pos["notional"] / equity
            pos["cum_pnl"] -= fee * share
            pos["notional"] *= 1.0 - f
            if pos["notional"] > 1.0:
                keep.append(pos)
            else:
                self._emit_close(series, pos, t, "recall_schwartz", eq_now)
        self.positions = keep
        return got + released

    def finish(self, series, t, outside_eq: float = 0.0):
        self._mark_to(series, t)
        eq_now = outside_eq + self.cash + sum(p["notional"] for p in self.positions)
        for pos in self.positions:
            self.cash += pos["notional"]
            self._emit_close(series, pos, t, "rebalance_48h", eq_now)
        self.positions = []


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


def _pyramid_buffer(open_pos):
    total = 0.0
    for p in open_pos:
        if p.get("pyr_locked"):
            continue
        if not p.get("pyr_checked"):
            total += 0.50 * p["margin"]
        else:
            total += 0.50 * p["margin"] * float(p.get("pyr_scale") or 0.0)
    return total


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def simulate(cands, series, syms, start, end, mode):
    btc = series["BTCUSDT"]
    rebalance = set(rebalance_times(btc, start, end))
    cash_mode = "idle" if mode == "nox" else mode
    excluded = tuple(list(sp.ALTS_7) + ["BTCUSDT"]) if mode == "nox" else ()
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
    book = SleeveBook(basket_stop=BASKET_STOP)
    if mode == "split":
        free = 2000.0 * 0.70
        book.absorb(2000.0 * 0.30)
    else:
        free = 2000.0
    reserved = 0.0
    open_pos = []
    schwartz = []
    schwartz_trades = []
    curve = [(start, 2000.0)]
    loss_streak = 0
    win_streak = 0

    def equity(t):
        return free + reserved + book.value(series, t)

    sorted_times = sorted(times)
    for t in sorted_times:
        if book.check_stops(series, t, free + reserved):
            if cash_mode in ("idle", "flat"):
                free += book.cash
                book.cash = 0.0

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
                schwartz.append(float(pnl))
                eq_after = equity(t)
                contrib = pnl / max(float(p.get("eq_entry") or 2000.0), 1.0)
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

        if cash_mode in ("idle", "flat"):
            for p in open_pos:
                due = int(p.get("pyr_ts") or 0)
                if not due or due > t or p.get("pyr_checked"):
                    continue
                p["pyr_checked"] = True
                need = 0.50 * p["margin"]
                if need > free:
                    free += book.recall(series, t, need - free, free + reserved)
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
                held = book.value(series, t)
                total = free + reserved + held
                sizing_eq = total if cash_mode in ("idle", "flat") else free + reserved
                avail = free + held if cash_mode in ("idle", "flat") else free
                mult = sp.size_multiplier(
                    loss_streak, win_streak,
                    int(e.get("c_lstreak") or 0),
                    float(e.get("hot_avg") or 0.0),
                    win_cap=1.15,
                )
                stop_dist = max(1.6 * e["atr"], 0.008)
                target = (sizing_eq * 0.075 * mult) / stop_dist
                raw_margin = min(target / 7.0, sizing_eq * 0.60, avail * 0.90)
                if raw_margin < 15.0:
                    continue
                if cash_mode in ("idle", "flat"):
                    buffer = 0.50 * raw_margin
                    need = min(avail, raw_margin + buffer)
                    if need > free:
                        free += book.recall(series, t, need - free, free + reserved)
                    raw_margin = min(raw_margin, max(0.0, free - buffer), free * 0.90)
                qty = sp.fit_lot_qty(e["sym"], (raw_margin * 7.0) / max(e["entry_px"], 1e-8))
                if qty <= 0:
                    continue
                margin = (qty * e["entry_px"]) / 7.0
                if margin < 15.0 or margin > free:
                    continue
                free -= margin
                reserved += margin
                rem = max(0.0, free)
                need_pyr = 0.50 * margin
                if cash_mode in ("idle", "flat"):
                    pyr_scale = 0.0
                else:
                    pyr_scale = min(1.0, (rem * 0.90) / max(need_pyr, 1e-8)) if e["pyr"] and rem >= 10.0 else 0.0
                open_pos.append({
                    "e": e,
                    "qty": qty,
                    "eq_entry": sizing_eq,
                    "exit_ts": int(e.get("exit_ts_nopyr") or e["exit_ts"]),
                    "use_pyr": False,
                    "margin": margin,
                    "pyr_scale": pyr_scale,
                    "pyr_ts": int(e.get("pyr_ts") or 0),
                    "pyr_locked": 0.0,
                    "pyr_checked": False,
                })
                busy.add(e["sym"])

        if cash_mode == "idle":
            deployable = free - _pyramid_buffer(open_pos)
            if t in rebalance:
                if deployable > 0:
                    book.absorb(deployable)
                    free -= deployable
                book.rebalance(series, t, target_weights(series, syms, t, excluded), free + reserved)
                if book.cash > 0:
                    free += book.cash
                    book.cash = 0.0
            elif deployable > 50.0 and book.positions:
                book.absorb(deployable)
                free -= deployable
                book.scale_in(series, t)
        elif mode == "flat":
            if open_pos:
                held_now = book.value(series, t)
                if held_now > 1.0:
                    free += book.recall(series, t, held_now, free + reserved)
            else:
                deployable = free
                if t in rebalance or (deployable > 50.0 and (book.positions or book.cash > 0)):
                    if deployable > 0:
                        book.absorb(deployable)
                        free -= deployable
                    if t in rebalance or not book.positions:
                        book.rebalance(series, t, target_weights(series, syms, t, excluded), free + reserved)
                    else:
                        book.scale_in(series, t)
        elif t in rebalance:
            book.rebalance(series, t, target_weights(series, syms, t, excluded), free + reserved)

        eq_now = equity(t)
        if t in rebalance or t in by_t or (t - start) % (6 * sp.HOUR) == 0:
            curve.append((int(t), round(eq_now, 2)))
        if eq_now <= 0:
            break

    last_t = sorted_times[-1] if sorted_times else end
    book.finish(series, last_t, free + reserved)
    if cash_mode in ("idle", "flat"):
        free += book.cash
        book.cash = 0.0
    final_eq = equity(last_t)
    if curve[-1][0] != last_t:
        curve.append((last_t, round(final_eq, 2)))
    else:
        curve[-1] = (curve[-1][0], round(final_eq, 2))
    gap = final_eq - 2000.0 - sum(schwartz) - sum(book.events)
    out = pack(schwartz + book.events, curve)
    out["schwartz_n"] = len(schwartz)
    out["sleeve_pnl"] = round(sum(book.events), 2)
    out["gap"] = round(gap, 2)
    out["sleeve_trades"] = book.closed
    out["schwartz_trades"] = schwartz_trades
    return out


def baseline_window(cands, start, end):
    part = [e for e in cands if start <= int(e["t"]) < end]
    res = sp.simulate_window(part)
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


def passes(mode_row, base):
    return (
        mode_row["2024"]["ret"] >= base["2024"]["ret"]
        and mode_row["2025"]["ret"] >= base["2025"]["ret"]
        and mode_row["2026Q"]["ret"] >= base["2026Q"]["ret"]
        and mode_row["full"]["dd"] < base["full"]["dd"]
    )
