# -*- coding: utf-8 -*-
"""Strict Carry/Momentum sleeve beside the current Schwartz book.

The sleeve is 1x gross: three longs and three shorts, rebalanced every 48 hours.
A fee of 0.055% plus 3 bps is charged only on the notional that actually trades.
Split mode is two pots, 70% Schwartz and 30% sleeve, compounded apart.
Idle mode parks only cash that is not reserved margin and not the pyramid
buffer. A Schwartz entry pulls that cash back and pays the close fee.
Each dollar sits in one place. Local only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

ONE_WAY = 0.00055 + 0.0003  # half of the 0.11% round trip, plus 3 bps
HOLD_H = 48
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


def position_value(positions, series, t):
    value = 0.0
    for pos in positions:
        ret = leg_ret(series[pos["sym"]], pos["t0"], t, pos["side"])
        value += pos["notional"] * (1.0 + ret)
    return value


class SleeveBook:
    """One gross-1 basket. Cash and positions are the whole sleeve pot."""

    def __init__(self):
        self.positions = []
        self.cash = 0.0
        self.snapshot = 0.0
        self.pending = 0.0
        self.events = []
        self.open_legs = []
        self.closed = []

    def value(self, series, t):
        return self.cash + position_value(self.positions, series, t)

    def absorb(self, amount):
        if amount <= 0:
            return
        self.cash += amount
        self.snapshot += amount

    def _realize(self, series, t):
        marked = position_value(self.positions, series, t)
        interval = marked + self.cash - self.snapshot
        fresh = []
        for pos in self.positions:
            mv = pos["notional"] * (1.0 + leg_ret(series[pos["sym"]], pos["t0"], t, pos["side"]))
            if mv > 1.0:
                fresh.append({"sym": pos["sym"], "side": pos["side"], "notional": mv, "t0": t})
        self.positions = fresh
        self.snapshot = marked + self.cash
        return interval

    def _signed(self):
        book = {}
        for pos in self.positions:
            book[pos["sym"]] = book.get(pos["sym"], 0.0) + pos["side"] * pos["notional"]
        return book

    def _flush(self):
        if abs(self.pending) > 1e-9:
            self.events.append(self.pending)
        self.pending = 0.0

    def _record_open_legs(self, series, t):
        """Close the basket that was opened at the previous rebalance."""
        for pos in self.open_legs:
            sym = pos["sym"]
            p0 = price_at(series[sym], pos["t0"])
            p1 = price_at(series[sym], t)
            if not p0 or not p1 or p0 <= 0:
                continue
            ret = leg_ret(series[sym], pos["t0"], t, pos["side"])
            self.closed.append({
                "sym": sym,
                "coin": sym.replace("USDT", ""),
                "side": int(pos["side"]),
                "t": int(pos["t0"]),
                "exit_ts": int(t),
                "entry_price": round(float(p0), 6),
                "exit_price": round(float(p1), 6),
                "qty": round(float(pos["notional"]) / float(p0), 6),
                "margin": round(float(pos["notional"]), 2),
                "eff_lev": 1.0,
                "pnl_usd": round(float(pos["notional"]) * ret, 2),
                "raw_net_pct": round(ret * 100.0, 2),
                "reason": "rebalance_48h",
                "book": "sleeve",
                "pyr": False,
                "hold_h": max(1, int((int(t) - int(pos["t0"])) / sp.HOUR)),
            })
        self.open_legs = []

    def _remember_open_legs(self):
        self.open_legs = [dict(pos) for pos in self.positions]

    def rebalance(self, series, t, weights):
        self._record_open_legs(series, t)
        self.pending += self._realize(series, t)
        current = self._signed()
        equity = sum(abs(v) for v in current.values()) + self.cash
        if equity < 50.0 or not weights:
            turnover = sum(abs(v) for v in current.values())
            fee = turnover * ONE_WAY
            self.cash = max(0.0, equity - fee)
            self.positions = []
            self.snapshot = self.cash
            self.pending -= fee
            self._flush()
            return
        targets = {sym: w * equity for sym, w in weights.items()}
        names = set(current) | set(targets)
        turnover = sum(abs(targets.get(sym, 0.0) - current.get(sym, 0.0)) for sym in names)
        fee = turnover * ONE_WAY
        equity2 = equity - fee
        self.positions = []
        if equity2 >= 50.0:
            for sym, w in weights.items():
                notion = abs(w) * equity2
                if notion > 1.0:
                    self.positions.append({
                        "sym": sym,
                        "side": 1 if w > 0 else -1,
                        "notional": notion,
                        "t0": t,
                    })
            self.cash = 0.0
            self.snapshot = sum(p["notional"] for p in self.positions)
        else:
            self.cash = max(0.0, equity2)
            self.snapshot = self.cash
        self.pending -= fee
        self._flush()
        self._remember_open_legs()

    def scale_in(self, series, t):
        """Deploy uninvested sleeve cash onto the current names. No new ranking."""
        if self.cash <= 1.0 or not self.positions:
            return
        self.pending += self._realize(series, t)
        base = sum(p["notional"] for p in self.positions)
        add = self.cash
        if base <= 1.0:
            return
        fee = add * ONE_WAY
        scale = (base + add - fee) / base
        if scale <= 0:
            return
        for pos in self.positions:
            pos["notional"] *= scale
        self.cash = 0.0
        self.snapshot = sum(p["notional"] for p in self.positions)
        self.pending -= fee

    def recall(self, series, t, need):
        """Move `need` dollars back toward Schwartz. Returns the cash actually freed."""
        if need <= 0:
            return 0.0
        if self.cash >= need:
            self.cash -= need
            self.snapshot -= need
            return need
        got = self.cash
        self.snapshot -= self.cash
        self.cash = 0.0
        self.pending += self._realize(series, t)
        equity = sum(p["notional"] for p in self.positions)
        if equity <= 1.0:
            self._flush()
            return got
        f = min(1.0, (need - got) / (equity * (1.0 - ONE_WAY)))
        fee = equity * f * ONE_WAY
        released = equity * f - fee
        for pos in self.positions:
            pos["notional"] *= 1.0 - f
        self.positions = [p for p in self.positions if p["notional"] > 1.0]
        self.snapshot = sum(p["notional"] for p in self.positions) + self.cash
        self.pending -= fee
        self._flush()
        return got + released

    def finish(self, series, t):
        self._record_open_legs(series, t)
        self.pending += self._realize(series, t)
        self._flush()


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
    """Cash kept out of the sleeve for a pyramid that has not fired yet.

    The reserve is taken for every open Schwartz trade. It does not depend
    on whether that trade later reaches +2 ATR.
    """
    total = 0.0
    for p in open_pos:
        if p.get("pyr_locked"):
            continue
        if not p.get("pyr_checked"):
            total += 0.50 * p["margin"]
        else:
            total += 0.50 * p["margin"] * float(p.get("pyr_scale") or 0.0)
    return total


def simulate(cands, series, syms, start, end, mode):
    btc = series["BTCUSDT"]
    rebalance = set(rebalance_times(btc, start, end))
    cash_mode = "idle" if mode == "nox" else mode
    excluded = tuple(list(sp.ALTS_7) + ["BTCUSDT"]) if mode == "nox" else ()
    by_t = {}
    times = set(rebalance)
    for e in cands:
        if start <= int(e["t"]) < end:
            by_t.setdefault(int(e["t"]), []).append(e)
            times.add(int(e["t"]))
            times.add(int(e["exit_ts"]))
            times.add(int(e.get("exit_ts_nopyr") or e["exit_ts"]))
            pyr_ts = int(e.get("pyr_ts") or 0)
            if start <= pyr_ts < end:
                times.add(pyr_ts)
    book = SleeveBook()
    if mode == "split":
        free = 2000.0 * 0.70
        book.absorb(2000.0 * 0.30)
    else:
        free = 2000.0
    reserved = 0.0
    open_pos = []
    schwartz = []
    curve = [(start, 2000.0)]
    loss_streak = 0
    win_streak = 0

    def equity(t):
        return free + reserved + book.value(series, t)

    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                use_pyr = bool(p.get("use_pyr")) and float(p.get("pyr_scale") or 0.0) > 0.0
                if use_pyr:
                    tot = e["net"] + p["pyr_scale"] * e["pyr_net"]
                    min_move = e["min_move"]
                else:
                    tot = e.get("net_nopyr", e["net"])
                    min_move = e.get("min_move_nopyr", e["min_move"])
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
            if cash_mode in ("idle", "flat") and need > free:
                free += book.recall(series, t, need - free)
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
                        free += book.recall(series, t, need - free)
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
                book.rebalance(series, t, target_weights(series, syms, t, excluded))
            elif deployable > 50.0 and book.positions:
                book.absorb(deployable)
                free -= deployable
                book.scale_in(series, t)
        elif mode == "flat":
            # Sleeve only while both Schwartz slots are empty.
            if open_pos:
                held_now = book.value(series, t)
                if held_now > 1.0:
                    free += book.recall(series, t, held_now)
            else:
                deployable = free
                if t in rebalance or (deployable > 50.0 and (book.positions or book.cash > 0)):
                    if deployable > 0:
                        book.absorb(deployable)
                        free -= deployable
                    if t in rebalance or not book.positions:
                        book.rebalance(series, t, target_weights(series, syms, t, excluded))
                    else:
                        book.scale_in(series, t)
        elif t in rebalance:
            book.rebalance(series, t, target_weights(series, syms, t, excluded))

        eq_now = equity(t)
        curve.append((int(t), round(eq_now, 2)))
        if eq_now <= 0:
            break

    book.finish(series, sorted(times)[-1] if times else end)
    final_eq = equity(sorted(times)[-1] if times else end)
    if curve[-1][0] != (sorted(times)[-1] if times else end):
        curve.append((sorted(times)[-1] if times else end, round(final_eq, 2)))
    else:
        curve[-1] = (curve[-1][0], round(final_eq, 2))
    gap = final_eq - 2000.0 - sum(schwartz) - sum(book.events)
    out = pack(schwartz + book.events, curve)
    out["schwartz_n"] = len(schwartz)
    out["sleeve_pnl"] = round(sum(book.events), 2)
    out["gap"] = round(gap, 2)
    out["sleeve_trades"] = book.closed
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


def main():
    series = st._load_universe()
    syms = list(series.keys())
    cands, _ = sp.build_tranche_candidates()
    report = {"baseline": {}, "split": {}, "idle": {}, "flat": {}, "nox": {}}
    curves = {"baseline": {}, "split": {}, "idle": {}, "flat": {}, "nox": {}}
    for label, a, b in WINDOWS:
        base = baseline_window(cands, a, b)
        curves["baseline"][label] = base.pop("curve")
        report["baseline"][label] = base
        for mode in ("split", "idle", "flat", "nox"):
            res = simulate(cands, series, syms, a, b, mode)
            curves[mode][label] = res.pop("curve")
            report[mode][label] = res
        bse = report["baseline"][label]
        spl = report["split"][label]
        idl = report["idle"][label]
        nox = report["nox"][label]
        print(
            f"{label:7} base {bse['ret']:7.1f}%/{bse['dd']:4.1f} top5 {bse['top5']:.3f}  "
            f"split {spl['ret']:7.1f}%/{spl['dd']:4.1f} top5 {spl['top5']:.3f}  "
            f"idle {idl['ret']:7.1f}%/{idl['dd']:4.1f} top5 {idl['top5']:.3f}  "
            f"nox {nox['ret']:7.1f}%/{nox['dd']:4.1f} top5 {nox['top5']:.3f} gap {nox['gap']:.2f}",
            flush=True,
        )
    gate = {
        "split": passes(report["split"], report["baseline"]),
        "idle": passes(report["idle"], report["baseline"]),
        "flat": passes(report["flat"], report["baseline"]),
        "nox": passes(report["nox"], report["baseline"]),
    }
    print("GATE", gate, flush=True)
    OUT.write_text(
        json.dumps({"report": report, "curves": curves, "gate": gate}, ensure_ascii=False),
        encoding="utf-8",
    )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
