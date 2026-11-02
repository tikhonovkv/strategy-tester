# -*- coding: utf-8 -*-
"""1x carry/momentum sleeve the one-way Bybit account can actually hold.

Three longs and three shorts, refreshed every 48 hours, with a -3.0% basket
stop-loss checked every 5 minutes. The basket never includes BTC or the seven
Schwartz alts, so a sleeve leg cannot net into a Schwartz position.
"""
from __future__ import annotations

import numpy as np

import schwartz_production as sp
import schwartz_trader as st

HOLD_MS = 48 * 3600 * 1000
MIN_NOTIONAL = 15.0
BASKET_STOP = 0.030  # Close all 6 legs if combined basket PnL reaches -3.0%
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
    """Long the 3 leaders and short the 3 laggards. Gross exposure is 1.0."""
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
        return raw

    def symbols(self, state) -> set:
        legs = self.book(state)["legs"]
        return {sym for sym in legs if sym not in BANNED}

    def margin(self, state) -> float:
        total = 0.0
        for sym, leg in self.book(state)["legs"].items():
            px = self._price(sym, leg)
            total += abs(float(leg.get("qty") or 0.0)) * px
        return total

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
        """Close all sleeve legs if combined basket PnL reaches -3.0% of entry notional."""
        book = self.book(state)
        legs = book["legs"]
        if not legs:
            return False
        total_entry = 0.0
        unreal_pnl = 0.0
        for sym, leg in list(legs.items()):
            qty = abs(float(leg.get("qty") or 0.0))
            entry_px = float(leg.get("px") or 0.0)
            cur_px = self._price(sym, leg)
            side = int(leg.get("side") or 1)
            if qty <= 0 or entry_px <= 0 or cur_px <= 0:
                continue
            entry_notional = qty * entry_px
            total_entry += entry_notional
            unreal_pnl += side * qty * (cur_px - entry_px)
        if total_entry <= MIN_NOTIONAL:
            return False
        ret = unreal_pnl / total_entry
        if ret > -BASKET_STOP:
            return False
        print(
            f"  SLEEVE STOP ({ret*100:+.2f}% <= -{BASKET_STOP*100:.1f}%) closing {len(legs)} legs (pnl=${unreal_pnl:+.2f})",
            flush=True,
        )
        for sym in list(legs):
            self._flatten(sym, legs)
        if self.trader.live:
            book["stopped_ts"] = int(book.get("rebalance_ts") or now_ms)
        return True

    def arm_exchange_stops(self, state) -> bool:
        """Put a Bybit stop on each leg. One leg moving 18% costs 3% of the basket."""
        if not self.trader.live or not self.trader.has_keys():
            return False
        legs = self.book(state)["legs"]
        adverse = BASKET_STOP * 6.0
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
        """Close part of the basket until about `need` USDT of margin comes back."""
        if need <= 1.0:
            return 0.0
        book = self.book(state)
        legs = book["legs"]
        notionals = []
        for sym, leg in list(legs.items()):
            px = self._price(sym, leg)
            notion = abs(float(leg.get("qty") or 0.0)) * px
            if notion > 1.0:
                notionals.append((sym, notion, px))
        gross = sum(row[1] for row in notionals)
        if gross <= 1.0:
            return 0.0
        fraction = min(1.0, need / gross)
        freed = 0.0
        for sym, notion, px in notionals:
            leg = legs[sym]
            qty = abs(float(leg.get("qty") or 0.0))
            cut = qty * fraction
            if cut * px < 1.0:
                continue
            if self._order(sym, -int(leg["side"]), cut, reduce_only=True):
                if not self.trader.live:
                    freed += cut * px
                    continue
                left = qty - cut
                if left * px < MIN_NOTIONAL:
                    legs.pop(sym, None)
                    freed += notion
                else:
                    leg["qty"] = left
                    freed += cut * px
        return freed

    def park(self, state, deployable: float, now_ms: int) -> None:
        """Put free USDT to work. Full ranking only every 48 hours."""
        series = self.series()
        btc = series.get("BTCUSDT")
        asof = int(now_ms)
        if btc is not None and len(btc["close_ts"]):
            last = int(btc["close_ts"][-1])
            if last < asof:
                asof = last
        stale_h = (int(now_ms) - asof) / sp.HOUR
        weights_now = target_weights(series, list(series.keys()), asof)
        book_txt = ", ".join(f"{sym}:{weight:+.2f}" for sym, weight in weights_now.items()) or "empty"
        print(f"  SLEEVE book lag={stale_h:.1f}h {book_txt}", flush=True)
        if stale_h > 6.0:
            print("  SLEEVE skip orders, hourly cache is older than 6 hours", flush=True)
            return
        book = self.book(state)
        reb_ts = int(book.get("rebalance_ts") or 0)
        due = int(now_ms) - reb_ts >= HOLD_MS
        if not due and reb_ts > 0 and int(book.get("stopped_ts") or -1) == reb_ts:
            print("  SLEEVE waiting for next 48h slot after basket_stop", flush=True)
            return
        if deployable < MIN_NOTIONAL and not book["legs"]:
            return
        if book["legs"] and not due:
            self._scale_in(state, max(0.0, deployable))
            return
        weights = weights_now
        self._rebalance(state, weights, max(0.0, deployable), int(now_ms))
        self.arm_exchange_stops(state)

    def _scale_in(self, state, amount: float) -> None:
        if amount < MIN_NOTIONAL:
            return
        book = self.book(state)
        legs = book["legs"]
        marked = []
        for sym, leg in legs.items():
            px = self._price(sym, leg)
            notion = abs(float(leg.get("qty") or 0.0)) * px
            if notion > 1.0:
                marked.append((sym, notion, px))
        base = sum(row[1] for row in marked)
        if base <= 1.0:
            return
        for sym, notion, px in marked:
            extra = amount * (notion / base)
            if extra < MIN_NOTIONAL:
                continue
            leg = legs[sym]
            qty = extra / max(px, 1e-8)
            if self._order(sym, int(leg["side"]), qty, reduce_only=False) and self.trader.live:
                leg["qty"] = abs(float(leg.get("qty") or 0.0)) + self._fit_qty(sym, qty)

    def _rebalance(self, state, weights: dict, deployable: float, now_ms: int) -> None:
        book = self.book(state)
        legs = book["legs"]
        marked = 0.0
        current = {}
        for sym, leg in list(legs.items()):
            px = self._price(sym, leg)
            signed = int(leg.get("side") or 1) * abs(float(leg.get("qty") or 0.0)) * px
            current[sym] = signed
            marked += abs(signed)
        equity = marked + deployable
        if equity < MIN_NOTIONAL or not weights:
            for sym in list(legs):
                self._flatten(sym, legs)
            if self.trader.live:
                book["rebalance_ts"] = now_ms
                book["stopped_ts"] = -1
            return
        targets = {sym: w * equity for sym, w in weights.items()}
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
        try:
            self.trader.set_leverage(sym, 1.0)
            self.trader.client.private_post(
                "/v5/position/switch-isolated",
                {
                    "category": "linear",
                    "symbol": sym,
                    "tradeMode": 1,
                    "buyLeverage": "1",
                    "sellLeverage": "1",
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
    """Write the sleeve legs, combined Schwartz trades, and unified equity curve."""
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
