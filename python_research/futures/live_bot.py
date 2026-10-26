# -*- coding: utf-8 -*-
"""Live futures bot running the Marty Schwartz ('Pitbull') Championship 7x strategy.

Default is dry-run (--live enables real Bybit orders).
Every hourly bar:
1. Checks open positions against Schwartz exit rules:
   - fast_bail: if held <= 4h and move <= -0.8 * ATR -> close immediately.
   - hard_stop: if move <= -2.2 * ATR -> close immediately.
   - tp1 (3.0 * ATR): take 20% profit and lock stop above entry (+0.5 * ATR).
   - tp2 (5.5 * ATR): take 15% profit.
   - pyramid (2.0 * ATR): add 50% size in winning direction once confirmed.
   - ema10_exit / 96h time stop: close runner when price crosses 10d EMA or reaches 96h.
2. Evaluates new hourly signals from `scan_live_schwartz` (10d EMA + 48h EMA reclaim +
   BTC 10d EMA regime + non-crowded funding carry + causal coin rotation).
3. Writes `futures_signals.json` with live checklist & active strategy parameters for the dashboard.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rest import BybitClient
from causal import HOUR, LAB
from schwartz_production import scan_live_schwartz, SHORT_NAME

OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))


def load_params() -> dict:
    for d in (OUT, LAB):
        path = d / "live_params.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {
        "mode": "schwartz_championship_7x",
        "leverage": 7.0,
        "risk_pct": 0.075,
        "max_margin_frac": 0.60,
        "max_conc": 2,
    }


class FuturesTrader:
    def __init__(self, live: bool):
        self.live = live
        self.client = BybitClient()

    def has_keys(self) -> bool:
        try:
            self.client._require_keys()
            return True
        except BaseException:
            return False

    def usdt_free(self) -> float:
        if not self.has_keys():
            return 2000.0
        try:
            d = self.client.private_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            for acct in (d.get("result") or {}).get("list") or []:
                for coin in acct.get("coin") or []:
                    if str(coin.get("coin")) == "USDT":
                        return float(coin.get("walletBalance") or 0) - float(coin.get("locked") or 0)
        except Exception as exc:
            print("  wallet-balance err:", exc)
        return 0.0

    def positions(self) -> dict:
        if not self.has_keys():
            return {}
        try:
            d = self.client.private_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
            out = {}
            for r in (d.get("result") or {}).get("list") or []:
                size = float(r.get("size") or 0)
                if size == 0:
                    continue
                out[r.get("symbol")] = {
                    "side": 1 if str(r.get("side")) == "Buy" else -1,
                    "size": size,
                    "entry": float(r.get("avgPrice") or 0),
                    "updated": int(r.get("updatedTime") or r.get("createdTime") or 0),
                    "created": int(r.get("createdTime") or r.get("updatedTime") or 0),
                }
            return out
        except Exception as exc:
            print("  positions err:", exc)
            return {}

    def recover_open_ts(self, symbol: str, side: int, target_size: float, now_ms: int) -> int:
        """Recover the actual entry timestamp of the currently open position from Bybit executions
        by walking backwards until cumulative entry fills reach target_size. Never uses slot createdTime!
        """
        if not self.has_keys():
            return now_ms
        want_side = "Buy" if side == 1 else "Sell"
        try:
            d = self.client.private_get(
                "/v5/execution/list",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "limit": "100",
                    "startTime": str(now_ms - 5 * 86400000),
                    "endTime": str(now_ms),
                },
            )
            rows = sorted(
                (d.get("result") or {}).get("list") or [],
                key=lambda x: int(x.get("execTime") or 0),
                reverse=True,
            )
            acc = 0.0
            first_ts = now_ms
            for r in rows:
                if str(r.get("side")) == want_side:
                    q = float(r.get("execQty") or 0)
                    ts = int(r.get("execTime") or 0)
                    if q > 0 and ts > 0:
                        acc += q
                        first_ts = ts
                        if acc >= target_size * 0.95:
                            break
            return first_ts
        except Exception as exc:
            print("  recover_open_ts err:", symbol, exc)
            return now_ms

    def set_leverage(self, symbol: str, leverage: float):
        try:
            self.client.private_post(
                "/v5/position/set-leverage",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "buyLeverage": str(int(leverage)),
                    "sellLeverage": str(int(leverage)),
                },
            )
        except Exception as exc:
            print("  leverage", symbol, exc)

    def instrument(self, symbol: str) -> dict:
        d = self.client.public_get("/v5/market/instruments-info", {"category": "linear", "symbol": symbol})
        rows = (d.get("result") or {}).get("list") or []
        return rows[0] if rows else {}

    def fit_qty(self, symbol: str, qty: float) -> str:
        step = float((self.instrument(symbol).get("lotSizeFilter") or {}).get("qtyStep") or 0)
        if step > 0:
            qty = int(qty / step) * step
            raw = "{:.8f}".format(step).rstrip("0")
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            qty = round(qty, decimals)
        else:
            decimals = 4
            qty = round(qty, 4)
        if qty <= 0:
            return ""
        return "{:.{}f}".format(qty, decimals)

    def market_order(self, symbol: str, side: int, qty: float, reduce_only: bool = False):
        qty_text = self.fit_qty(symbol, qty)
        if not qty_text:
            print("  skip order", symbol, "qty rounds to 0", flush=True)
            return {"skipped": True, "qty": "0"}
        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": "Buy" if side == 1 else "Sell",
            "orderType": "Market",
            "qty": qty_text,
        }
        if reduce_only:
            payload["reduceOnly"] = True
        if self.live and self.has_keys():
            return self.client.private_post("/v5/order/create", payload)
        return {"dry_run": True, "payload": payload}

    def close_position(self, symbol: str, pos: dict):
        return self.market_order(symbol, -pos["side"], pos["size"], reduce_only=True)

    def price(self, symbol: str) -> float:
        d = self.client.public_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        rows = (d.get("result") or {}).get("list") or []
        return float(rows[0]["lastPrice"]) if rows else 0.0


def state_path() -> Path:
    return OUT / "futures_position_state.json"


def load_state() -> dict:
    for d in (OUT, LAB):
        path = d / "futures_position_state.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data:
                    return data
            except Exception:
                pass
    return {}


def save_state(state: dict) -> None:
    for d in {LAB, OUT}:
        tmp = d / "futures_position_state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "futures_position_state.json")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv[1:]
    check = "--check" in sys.argv[1:]
    hourly_close = "--hourly-close" in sys.argv[1:]
    now = int(time.time() * 1000)
    now_dt = datetime.now(timezone.utc)
    if now_dt.minute <= 7:
        hourly_close = True
    params = load_params()
    leverage = float(params.get("leverage", 7.0))
    risk_pct = float(params.get("risk_pct", 0.075))
    max_margin_frac = float(params.get("max_margin_frac", 0.60))
    max_conc = int(params.get("max_conc", 2))

    trader = FuturesTrader(live)
    sigs, series = scan_live_schwartz(now)
    pos = trader.positions()
    free = trader.usdt_free()
    state = load_state()

    # Build live regime & checklist for the dashboard so the user sees strategy status in real time
    btc_s = series.get("BTCUSDT")
    btc_px = float(btc_s["px"][-1]) if btc_s else 0.0
    btc_ema10 = float(btc_s["ema240"][-1]) if btc_s else 0.0
    btc_regime = "LONG (выше 10d EMA)" if btc_px > btc_ema10 else "SHORT (ниже 10d EMA)"

    coin_status = []
    for sym in ["ETHUSDT", "SOLUSDT", "ADAUSDT", "SUIUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT"]:
        s = series.get(sym)
        if not s:
            continue
        cpx = float(s["px"][-1])
        e240 = float(s["ema240"][-1])
        atr = float(s["atr1h"][-1]) * 100.0
        trend = "BULL (>10d EMA)" if cpx > e240 else "BEAR (<10d EMA)"
        coin_status.append({
            "coin": SHORT_NAME[sym],
            "price": round(cpx, 4),
            "ema10d": round(e240, 4),
            "atr_pct": round(atr, 2),
            "trend": trend,
            "in_position": sym in pos,
        })

    sig_payload = {
        "usdt_free": round(free, 2),
        "open_positions": len(pos),
        "mode": "LIVE" if (live and trader.has_keys()) else "DRY-RUN / MONITOR",
        "strategy": "Schwartz Pitbull Championship 7x (TP1 3.0 ATR / TP2 5.5 ATR + 65% Runner + Pyramid)",
        "btc_price": round(btc_px, 2),
        "btc_ema10d": round(btc_ema10, 2),
        "btc_regime": btc_regime,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "coins": coin_status,
        "signals": [
            {
                "symbol": s["coin"],
                "side": s["side"],
                "pred_net": s["score"],
                "ts": s["ts"],
                "entry_px": s["entry_px"],
                "tp1_px": s["tp1_px"],
                "tp2_px": s["tp2_px"],
                "fast_bail_px": s["fast_bail_px"],
                "hard_stop_px": s["hard_stop_px"],
            }
            for s in sigs
        ],
    }
    for d in {LAB, OUT}:
        tmp = d / "futures_signals.json.tmp"
        tmp.write_text(json.dumps(sig_payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "futures_signals.json")

    print(
        "[{}] USDT free={:.2f} open={} signals={} BTC={:.1f} ({})".format(
            sig_payload["mode"], free, len(pos), len(sigs), btc_px, btc_regime
        ),
        flush=True,
    )

    if check:
        return

    # 1. Manage open positions according to Schwartz rules
    # Clean stale state entries for symbols that are no longer open on the exchange
    for stale_sym in list(state.keys()):
        if stale_sym not in pos:
            state.pop(stale_sym, None)

    for sym, p in list(pos.items()):
        st = state.get(sym) or {}
        s_coin = series.get(sym)
        series_atr = float(s_coin["atr1h"][-1]) if s_coin and len(s_coin["atr1h"]) else 0.008
        if not st.get("opened") or int(st.get("side") or p["side"]) != p["side"]:
            rec_ts = trader.recover_open_ts(sym, p["side"], p["size"], now)
            st = {
                "opened": rec_ts,
                "entry_px": p["entry"],
                "base_qty": st.get("base_qty") or p["size"],
                "atr": float(st.get("atr") or series_atr),
                "side": p["side"],
                "pyr_done": bool(st.get("pyr_done", False)),
                "tp1_done": bool(st.get("tp1_done", False)),
                "tp2_done": bool(st.get("tp2_done", False)),
            }
            state[sym] = st
            print(f"  RECOVERED STATE {sym} opened={rec_ts} ({round((now - rec_ts)/3600000.0, 1)}h ago)", flush=True)
        opened = int(st["opened"])
        held_h = max(0.0, (now - opened) / 3600000.0)
        entry_px = float(p["entry"] or st.get("entry_px") or 0.0)
        atr = float(st.get("atr") or series_atr)
        cur_px = trader.price(sym)
        if cur_px <= 0 or entry_px <= 0:
            continue
        move = p["side"] * (cur_px / entry_px - 1.0)
        ema240_now = float(s_coin["ema240"][-1]) if s_coin else entry_px

        # Check exits: hard_stop and end_96h on every 5m tick;
        # fast_bail, breakeven and ema10_exit ONLY on hourly close (prevents 5m wick shakeouts)
        be_floor = 1.15 * atr if st.get("pyr_done") else 0.4 * atr
        should_close = False
        close_reason = ""
        if move <= -2.2 * atr:
            should_close, close_reason = True, "hard_stop"
        elif held_h >= 96.0:
            should_close, close_reason = True, "end_96h"
        elif hourly_close and held_h >= 0.8 and held_h <= 4.5 and move <= -0.8 * atr:
            should_close, close_reason = True, "fast_bail"
        elif hourly_close and st.get("tp1_done") and move <= be_floor:
            should_close, close_reason = True, "tp_scale+be"
        elif hourly_close and ((p["side"] == 1 and cur_px < ema240_now) or (p["side"] == -1 and cur_px > ema240_now)):
            should_close, close_reason = True, "ema10_exit"

        if should_close:
            print(f"  CLOSE {sym} ({close_reason}) held={held_h:.1f}h move={move*100:+.2f}%", flush=True)
            if live:
                try:
                    trader.close_position(sym, p)
                except Exception as exc:
                    print(f"  CLOSE {sym} order failed: {exc}", flush=True)
                    continue
            state.pop(sym, None)
            pos.pop(sym, None)
            save_state(state)
            continue

        # Check Pyramid add (+50% of initial size at +2.0 ATR)
        if not st.get("pyr_done") and move >= 2.0 * atr:
            base_qty = float(st.get("base_qty") or p["size"])
            q_pyr = round(base_qty * 0.50, 4)
            lot = float((trader.instrument(sym).get("lotSizeFilter") or {}).get("qtyStep") or 0)
            if lot > 0:
                q_pyr = int(q_pyr / lot) * lot
            if q_pyr > 0 and live:
                try:
                    trader.market_order(sym, p["side"], q_pyr, reduce_only=False)
                except Exception as exc:
                    print(f"  PYRAMID {sym} order failed: {exc}", flush=True)
                    continue
            st["pyr_done"] = True
            st["pyr_px"] = cur_px
            state[sym] = st
            save_state(state)
            print(f"  PYRAMID (+2.0 ATR) {sym} added 50% qty={q_pyr} at {cur_px}", flush=True)

        # Check Tranche TP1 (20% at +3.0 ATR) and TP2 (15% at +5.5 ATR)
        if not st.get("tp1_done") and move >= 3.0 * atr:
            q_tp1 = round(p["size"] * 0.20, 4)
            if q_tp1 > 0 and live:
                try:
                    trader.market_order(sym, -p["side"], q_tp1, reduce_only=True)
                except Exception as exc:
                    print(f"  TP1 {sym} order failed: {exc}", flush=True)
                    continue
            st["tp1_done"] = True
            state[sym] = st
            save_state(state)
            print(f"  TP1 (3.0 ATR) {sym} closed 20% qty={q_tp1}", flush=True)

        if not st.get("tp2_done") and move >= 5.5 * atr:
            q_tp2 = round(p["size"] * 0.15, 4)
            if q_tp2 > 0 and live:
                try:
                    trader.market_order(sym, -p["side"], q_tp2, reduce_only=True)
                except Exception as exc:
                    print(f"  TP2 {sym} order failed: {exc}", flush=True)
                    continue
            st["tp2_done"] = True
            state[sym] = st
            save_state(state)
            print(f"  TP2 (5.5 ATR) {sym} closed 15% qty={q_tp2}", flush=True)

    # 2. Open new positions ONLY on the hourly candle close check
    equity = free
    for s in (sigs if hourly_close else []):
        if len(pos) >= max_conc:
            break
        sym = s["symbol"]
        if sym in pos:
            continue
        px = trader.price(sym) or s["entry_px"]
        if px <= 0:
            continue
        atr = max(float(s["atr"]), 0.005)
        stop_dist = max(1.6 * atr, 0.008)
        target_notional = (equity * risk_pct) / stop_dist
        margin = min(target_notional / leverage, equity * max_margin_frac, free * 0.90)
        if margin < 15.0:
            continue
        qty = margin * leverage / px
        lot = float((trader.instrument(sym).get("lotSizeFilter") or {}).get("qtyStep") or 0)
        if lot > 0:
            qty = int(qty / lot) * lot
        if qty <= 0:
            continue
        print(
            "  OPEN {} {} qty={:.4f} margin=${:.1f} lev={:.0f}x tp1={} bail={}".format(
                s["coin"],
                "LONG" if s["side"] == 1 else "SHORT",
                qty,
                margin,
                leverage,
                s["tp1_px"],
                s["fast_bail_px"],
            ),
            flush=True,
        )
        if live:
            trader.set_leverage(sym, leverage)
            print("   order:", json.dumps(trader.market_order(sym, s["side"], qty))[:200], flush=True)
        state[sym] = {
            "opened": now,
            "entry_px": px,
            "base_qty": qty,
            "atr": atr,
            "side": s["side"],
            "pyr_done": False,
            "tp1_done": False,
            "tp2_done": False,
        }
        pos[sym] = {"side": s["side"], "size": qty, "entry": px, "updated": now}
        save_state(state)

    save_state(state)


if __name__ == "__main__":
    main()
