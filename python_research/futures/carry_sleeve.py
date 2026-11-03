# -*- coding: utf-8 -*-
"""3x drought-mode carry/momentum sleeve for the one-way Bybit account.

Turns ON only when Schwartz 7x has been flat for >= 5 days and had <= 1 trade
in the last 14 days (flat-market regime like 2024).
Deploys at 3x gross leverage (0.5x equity per leg across 3 longs and 3 shorts,
refreshed every 48 hours) with a -2.5% unlevered spread stop (-7.5% of margin)
checked every 5 minutes, plus a 15% adverse price stop on Bybit per leg.
Closes immediately as soon as Schwartz 7x opens a trade.
"""
from __future__ import annotations

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

HOLD_MS = 48 * 3600 * 1000
MIN_NOTIONAL = 15.0
SLEEVE_LEV = 3.0
UNLEV_STOP = 0.025  # -2.5% unlevered basket move = -7.5% of margin at 3x
QUIET_DAYS = 5.0
MAX_RECENT_TRADES = 1
RECENT_WINDOW_D = 14.0
STOP_STREAK_LIMIT = 2
STOP_COOLDOWN_H = 144.0
BANNED = set(sp.ALTS_7) | {"BTCUSDT"}


def _idx_at(series_row, t):
    ts = series_row["close_ts"]
    j = int(np.searchsorted(ts, int(t), side="right")) - 1
    if j < 0:
        return None
    if int(t) - int(ts[j]) > 6 * sp.HOUR:
        return None
    return j


def target_weights(series, syms, t, exclude=()):
    """Long the 3 leaders and short the 3 laggards. Weights sum to 1.0 gross before leverage."""
    banned = set(exclude) | BANNED
    rows = []
    for sym in syms:
        s = series.get(sym)
        if s is None or sym in banned:
            continue
        i = _idx_at(s, t)
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
        carry = -float(np.mean(s["f_rate"][fi - 20:fi + 1]))
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


class CarrySleeve:
    def __init__(self, trader):
        self.trader = trader
        self._series = None

    def series(self):
        if self._series is None:
            self._series = st._load_sleeve_universe()
        return self._series

    def book(self, state) -> dict:
        raw = state.get("_sleeve")
        if not isinstance(raw, dict):
            raw = {"rebalance_ts": 0, "stopped_ts": -1, "legs": {}}
            state["_sleeve"] = raw
        raw.setdefault("legs", {})
        raw.setdefault("rebalance_ts", 0)
        raw.setdefault("stopped_ts", -1)
        raw.setdefault("stop_streak", 0)
        raw.setdefault("cooldown_until", 0)
        return raw

    def symbols(self, state) -> set:
        legs = self.book(state)["legs"]
        return {sym for sym in legs if sym not in BANNED}

    def margin(self, state) -> float:
        total = 0.0
        for sym, leg in self.book(state)["legs"].items():
            px = self._price(sym, leg)
            total += abs(float(leg.get("qty") or 0.0)) * px / max(SLEEVE_LEV, 1.0)
        return total

    def is_drought(self, state, schwartz_open_count: int, now_ms: int) -> tuple[bool, float, int]:
        """Return (drought_ok, quiet_days, recent_14d_count)."""
        if schwartz_open_count > 0:
            return False, 0.0, schwartz_open_count
        closes = state.get("_closes")
        if not isinstance(closes, list) or not closes:
            return True, 999.0, 0
        last_ts = max(int(r.get("ts") or 0) for r in closes)
        quiet_d = max(0.0, (int(now_ms) - last_ts) / 86400000.0) if last_ts > 0 else 999.0
        recent_cnt = sum(
            1 for r in closes
            if int(now_ms) - int(r.get("opened_ts") or r.get("ts") or 0) <= RECENT_WINDOW_D * 86400000.0
        )
        ok = (quiet_d >= QUIET_DAYS) and (recent_cnt <= MAX_RECENT_TRADES)
        return ok, round(quiet_d, 1), recent_cnt

    def flatten_all(self, state, reason: str = "schwartz_active") -> bool:
        legs = self.book(state)["legs"]
        if not legs:
            return False
        print(f"  SLEEVE closing all {len(legs)} legs ({reason})", flush=True)
        for sym in list(legs):
            self._flatten(sym, legs)
        return True

    def reconcile(self, state, exchange_pos: dict) -> None:
        """Trust the exchange size and average entry price for legs this sleeve opened."""
        if not self.trader.has_keys():
            return
        legs = self.book(state)["legs"]
        for sym in list(legs):
            if sym in BANNED or sym not in exchange_pos:
                legs.pop(sym, None)
                continue
            ex = exchange_pos[sym]
            legs[sym]["qty"] = float(ex["size"])
            legs[sym]["side"] = int(ex["side"])
            if float(ex.get("entry") or 0) > 0:
                legs[sym]["px"] = float(ex["entry"])

    def check_stop(self, state, now_ms: int) -> bool:
        """Close all sleeve legs if combined unlevered basket move reaches -2.5% (-7.5% of margin at 3x)."""
        book = self.book(state)
        legs = book["legs"]
        if not legs:
            return False
        total_notional = 0.0
        unreal_pnl = 0.0
        for sym, leg in list(legs.items()):
            qty = abs(float(leg.get("qty") or 0.0))
            entry_px = float(leg.get("px") or 0.0)
            cur_px = self._price(sym, leg)
            side = int(leg.get("side") or 1)
            if qty <= 0 or entry_px <= 0 or cur_px <= 0:
                continue
            entry_notional = qty * entry_px
            total_notional += entry_notional
            unreal_pnl += side * qty * (cur_px - entry_px)
        if total_notional <= MIN_NOTIONAL:
            return False
        unlev_ret = unreal_pnl / total_notional
        if unlev_ret > -UNLEV_STOP:
            return False
        print(
            f"  SLEEVE STOP (spread {unlev_ret*100:+.2f}% <= -{UNLEV_STOP*100:.1f}%, eq {unlev_ret*SLEEVE_LEV*100:+.2f}%) closing {len(legs)} legs (pnl=${unreal_pnl:+.2f})",
            flush=True,
        )
        for sym in list(legs):
            self._flatten(sym, legs)
        if self.trader.live:
            book["stopped_ts"] = int(book.get("rebalance_ts") or now_ms)
            streak = int(book.get("stop_streak") or 0) + 1
            book["stop_streak"] = streak
            if streak >= STOP_STREAK_LIMIT:
                book["cooldown_until"] = int(now_ms) + int(STOP_COOLDOWN_H * 3600 * 1000)
                print(
                    f"  SLEEVE cooldown {STOP_COOLDOWN_H:.0f}h after {streak} basket stops",
                    flush=True,
                )
        return True

    def arm_exchange_stops(self, state) -> bool:
        """Put a Bybit stop on each leg at 15% adverse price move (2.5% * 6 legs)."""
        if not self.trader.live or not self.trader.has_keys():
            return False
        legs = self.book(state)["legs"]
        adverse = UNLEV_STOP * 6.0
        changed = False
        for sym, leg in list(legs.items()):
            entry = float(leg.get("px") or 0.0)
            side = int(leg.get("side") or 0)
            if entry <= 0 or side == 0:
                continue
            px = self._round_price(sym, entry * (1.0 - side * adverse))
            if px <= 0 or abs(float(leg.get("stop") or 0.0) - px) < 1e-12:
                continue
            try:
                self.trader.client.private_post(
                    "/v5/position/trading-stop",
                    {
                        "category": "linear",
                        "symbol": sym,
                        "tpslMode": "Full",
                        "positionIdx": 0,
                        "stopLoss": str(px),
                        "slTriggerBy": "MarkPrice",
                    },
                )
            except Exception as exc:
                text = str(exc).lower()
                if any(token in text for token in ("not modified", "34040")):
                    leg["stop"] = px
                    continue
                print(f"  SLEEVE stop {sym} failed: {exc}", flush=True)
                continue
            leg["stop"] = px
            changed = True
            print(f"  SLEEVE stop {sym} {px} ({-adverse * 100:.0f}% цены)", flush=True)
        return changed

    def release(self, state, need: float) -> float:
        """Close all or part of the basket when Schwartz needs margin."""
        if need <= 1.0:
            return 0.0
        freed = self.margin(state)
        self.flatten_all(state, reason="schwartz_margin")
        return freed

    def park(self, state, deployable: float, now_ms: int, schwartz_open_count: int = 0) -> None:
        """Deploy into the 3x basket ONLY when Schwartz is in a flat-market drought."""
        drought_ok, quiet_d, recent_cnt = self.is_drought(state, schwartz_open_count, now_ms)
        book = self.book(state)
        if schwartz_open_count > 0:
            book["stop_streak"] = 0
        if not drought_ok:
            if book["legs"]:
                self.flatten_all(state, reason=f"schwartz_active quiet={quiet_d}d recent14d={recent_cnt}")
            else:
                print(f"  SLEEVE standby (drought=off: quiet={quiet_d}d < {QUIET_DAYS}d or recent14d={recent_cnt} > {MAX_RECENT_TRADES})", flush=True)
            return
        cooldown_until = int(book.get("cooldown_until") or 0)
        if int(now_ms) < cooldown_until:
            left_h = (cooldown_until - int(now_ms)) / 3600000.0
            if book["legs"]:
                self.flatten_all(state, reason=f"stop_cooldown {left_h:.1f}h")
            else:
                print(f"  SLEEVE cooldown {left_h:.1f}h after {STOP_STREAK_LIMIT} basket stops", flush=True)
            return

        series = self.series()
        btc = series.get("BTCUSDT")
        asof = int(now_ms)
        if btc is not None and len(btc["close_ts"]):
            last = int(btc["close_ts"][-1])
            if last < asof:
                asof = last
        stale_h = (int(now_ms) - asof) / sp.HOUR
        weights_now = target_weights(series, list(series.keys()), asof)
        book_txt = ", ".join(f"{sym}:{weight * SLEEVE_LEV:+.2f}" for sym, weight in weights_now.items()) or "empty"
        print(f"  SLEEVE 3x drought-mode lag={stale_h:.1f}h quiet={quiet_d}d {book_txt}", flush=True)
        if stale_h > 6.0:
            print("  SLEEVE skip orders, hourly cache is older than 6 hours", flush=True)
            return
        reb_ts = int(book.get("rebalance_ts") or 0)
        due = int(now_ms) - reb_ts >= HOLD_MS
        if book["legs"] and due:
            book["stop_streak"] = 0
        if not due and reb_ts > 0 and int(book.get("stopped_ts") or -1) == reb_ts:
            print("  SLEEVE waiting for next 48h slot after basket_stop", flush=True)
            return
        if book["legs"] and not due:
            return
        if deployable < MIN_NOTIONAL and not book["legs"]:
            return
        self._rebalance(state, weights_now, max(0.0, deployable), int(now_ms))
        self.arm_exchange_stops(state)

    def _rebalance(self, state, weights: dict, deployable: float, now_ms: int) -> None:
        book = self.book(state)
        legs = book["legs"]
        marked_margin = 0.0
        current = {}
        for sym, leg in list(legs.items()):
            px = self._price(sym, leg)
            notional = abs(float(leg.get("qty") or 0.0)) * px
            signed = int(leg.get("side") or 1) * notional
            current[sym] = signed
            marked_margin += notional / max(SLEEVE_LEV, 1.0)
        equity = marked_margin + deployable * 0.92
        if equity < MIN_NOTIONAL or not weights:
            for sym in list(legs):
                self._flatten(sym, legs)
            if self.trader.live:
                book["rebalance_ts"] = now_ms
                book["stopped_ts"] = -1
            return
        targets = {sym: w * SLEEVE_LEV * equity for sym, w in weights.items()}
        for sym in list(current):
            if sym not in targets:
                self._flatten(sym, legs)
        for sym, signed_notional in targets.items():
            px = self._price(sym, legs.get(sym) or {})
            if px <= 0:
                continue
            have = current.get(sym, 0.0)
            if have * signed_notional < 0:
                self._flatten(sym, legs)
                have = 0.0
            delta = signed_notional - have
            if abs(delta) < MIN_NOTIONAL:
                continue
            side = 1 if delta > 0 else -1
            reducing = have != 0 and (have > 0) == (signed_notional > 0) and abs(signed_notional) < abs(have)
            qty = abs(delta) / px
            if self._order(sym, side, qty, reduce_only=reducing) and self.trader.live:
                filled = self._fit_qty(sym, qty)
                prev = abs(float((legs.get(sym) or {}).get("qty") or 0.0))
                new_qty = max(0.0, prev - filled) if reducing else prev + filled
                if new_qty * px < MIN_NOTIONAL:
                    legs.pop(sym, None)
                else:
                    legs[sym] = {"side": 1 if signed_notional > 0 else -1, "qty": new_qty, "px": px}
        if self.trader.live:
            book["rebalance_ts"] = now_ms
            book["stopped_ts"] = -1

    def _flatten(self, sym, legs) -> None:
        leg = legs.get(sym)
        if not leg:
            return
        qty = abs(float(leg.get("qty") or 0.0))
        if qty > 0:
            self._order(sym, -int(leg.get("side") or 1), qty, reduce_only=True)
        if self.trader.live:
            legs.pop(sym, None)

    def _round_price(self, sym, price: float) -> float:
        tick = float((self.trader.instrument(sym).get("priceFilter") or {}).get("tickSize") or 0)
        if tick <= 0:
            return round(price, 6)
        steps = round(price / tick)
        raw = steps * tick
        text = f"{tick:.10f}".rstrip("0")
        decimals = len(text.split(".")[1]) if "." in text else 0
        return round(raw, decimals)

    def _price(self, sym, leg) -> float:
        try:
            px = float(self.trader.price(sym) or 0.0)
        except Exception:
            px = 0.0
        if px <= 0:
            px = float(leg.get("px") or 0.0)
        return px

    def _isolated(self, sym) -> bool:
        if not self.trader.live or not self.trader.has_keys():
            return True
        lev_str = str(int(SLEEVE_LEV))
        try:
            self.trader.set_leverage(sym, SLEEVE_LEV)
            self.trader.client.private_post(
                "/v5/position/switch-isolated",
                {
                    "category": "linear",
                    "symbol": sym,
                    "tradeMode": 1,
                    "buyLeverage": lev_str,
                    "sellLeverage": lev_str,
                },
            )
            return True
        except Exception as exc:
            text = str(exc).lower()
            if any(token in text for token in ("110026", "110043", "100028", "not modified", "not change", "same")):
                return True
            print(f"  sleeve isolated {sym} failed: {exc}", flush=True)
            return False

    def _fit_qty(self, sym, qty: float) -> float:
        text = self.trader.fit_qty(sym, qty)
        return float(text) if text else 0.0

    def _order(self, sym, side: int, qty: float, reduce_only: bool) -> bool:
        if sym in BANNED or qty <= 0:
            return False
        if not reduce_only and not self._isolated(sym):
            return False
        px = self._price(sym, {})
        if px > 0 and qty * px < MIN_NOTIONAL and not reduce_only:
            return False
        try:
            resp = self.trader.market_order(sym, side, qty, reduce_only=reduce_only)
        except Exception as exc:
            print(f"  sleeve order {sym} failed: {exc}", flush=True)
            return False
        if isinstance(resp, dict) and resp.get("skipped"):
            return False
        print(
            "  SLEEVE {} {} qty={:.6f} reduce={}".format(
                sym, "BUY" if side > 0 else "SELL", qty, reduce_only
            ),
            flush=True,
        )
        return True


def export_compare_book(cands, windows, end_ms: int) -> None:
    """Write the drought-mode 3x sleeve legs, Schwartz trades, and unified equity curve."""
    import json
    from datetime import datetime, timezone

    import _sleeve_recheck as rec
    import schwartz_trader as trader

    series = trader._load_sleeve_universe()
    syms = list(series.keys())
    payload = {}
    for tag, start in windows:
        res = rec.simulate(cands, series, syms, int(start), int(end_ms), "nox")
        curve = []
        for ts, eq in res.get("curve") or []:
            if not ts:
                continue
            stamp = datetime.fromtimestamp(int(ts) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
            curve.append([stamp, round((float(eq) / 2000.0 - 1.0) * 100.0, 2)])
        trades = []
        for leg in res.get("sleeve_trades") or []:
            row = dict(leg)
            row.pop("pnl_exact", None)
            trades.append(row)
        schwartz_trades = []
        for tr in res.get("schwartz_trades") or []:
            row = dict(tr)
            row.pop("pnl_exact", None)
            schwartz_trades.append(row)
        payload[tag] = {
            "return_pct": res["ret"],
            "max_dd_pct": res["dd"],
            "final": res["final"],
            "equity_pct": curve,
            "trades": trades,
            "schwartz_trades": schwartz_trades,
        }
        print(
            f"sleeve {tag}: legs={len(trades)} schwartz={len(schwartz_trades)} combined={res['ret']:+.2f}% dd={res['dd']:.1f}%",
            flush=True,
        )
    for folder in {sp.LAB, sp.OUT}:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "sleeve_compare.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("wrote sleeve_compare.json", flush=True)
