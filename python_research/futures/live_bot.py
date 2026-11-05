# -*- coding: utf-8 -*-
"""Live futures bot running the Marty Schwartz ('Pitbull') Championship 7x strategy.

Default is dry-run (--live enables real Bybit orders).
Every hourly bar:
1. Checks open positions against Schwartz exit rules:
   - fast_bail: if held <= 4h and move <= -0.8 * ATR -> close immediately.
   - hard_stop: if move <= -2.2 * ATR -> close immediately.
   - After peak >= +3.0 ATR, the whole position closes if price falls back to the floor
     (+1.15 ATR with a pyramid, otherwise +0.4 ATR). No partial take-profit.
   - pyramid (2.0 * ATR): add 50% size in the winning direction once confirmed.
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
from carry_sleeve import CarrySleeve
from fetch_fresh import ensure_fresh
from schwartz_production import ALTS_7, scan_live_schwartz, size_multiplier, SHORT_NAME
from accounts_config import (
    get_account,
    parse_account_arg,
    read_account_json,
    write_account_json,
)

OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
PERSIST_STATE = True
CURRENT_ACCOUNT_ID = "main"


def load_params(account_id: str = "main") -> dict:
    base = {
        "mode": "schwartz_championship_7x",
        "leverage": 7.0,
        "sleeve_leverage": 3.0,
        "risk_pct": 0.075,
        "max_margin_frac": 0.60,
        "max_conc": 2,
        "enable_sleeve": True,
    }
    for d in (OUT, LAB):
        path = d / "live_params.json"
        if path.exists():
            try:
                base.update(json.loads(path.read_text(encoding="utf-8")))
                break
            except Exception:
                pass
    try:
        acc = get_account(account_id)
        base.update({
            "account_id": acc["id"],
            "account_name": acc["name"],
            "exchange": acc.get("exchange", "bybit"),
            "strategy": acc.get("strategy", "schwartz_7x_sleeve_3x"),
            "enabled": bool(acc.get("enabled", True)),
            "pause_new_trades": bool(acc.get("pause_new_trades", False)),
            "leverage": float(acc.get("leverage", base["leverage"])),
            "sleeve_leverage": float(acc.get("sleeve_leverage", base["sleeve_leverage"])),
            "risk_pct": float(acc.get("risk_pct", base["risk_pct"])),
            "max_margin_frac": float(acc.get("max_margin_frac", base["max_margin_frac"])),
            "max_conc": int(acc.get("max_conc", base["max_conc"])),
            "enable_sleeve": bool(acc.get("enable_sleeve", base["enable_sleeve"])),
        })
    except Exception as exc:
        print("  load_params account fallback:", exc, flush=True)
    return base


class FuturesTrader:
    def __init__(self, live: bool, account_id: str = "main"):
        self.live = live
        self.account_id = account_id
        self.client = BybitClient(account_id=account_id)
        self._wallet_cache: tuple[float, float, bool] | None = None
        self._wallet_bal_cache: float | None = None
        self._positions_cache: tuple[dict, bool] | None = None

    def invalidate_account(self) -> None:
        self._wallet_cache = None
        self._wallet_bal_cache = None
        self._positions_cache = None

    def has_keys(self) -> bool:
        try:
            self.client._require_keys()
            return True
        except BaseException:
            return False

    def wallet_snapshot(self) -> tuple[float, float, bool]:
        """One wallet read per cache lifetime: equity, free margin, success."""
        if self._wallet_cache is None:
            self._wallet_cache = self._fetch_wallet()
        return self._wallet_cache

    def _fetch_wallet(self) -> tuple[float, float, bool]:
        if not self.has_keys():
            self._wallet_bal_cache = 2000.0
            return 2000.0, 2000.0, True
        try:
            d = self.client.private_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            for acct in (d.get("result") or {}).get("list") or []:
                equity = float(acct.get("totalEquity") or 0)
                available = float(acct.get("totalAvailableBalance") or 0)
                wallet_bal = float(acct.get("totalWalletBalance") or 0)
                for coin in acct.get("coin") or []:
                    if str(coin.get("coin")) == "USDT":
                        c_wb = float(coin.get("walletBalance") or 0)
                        if c_wb > 0 and wallet_bal <= 0:
                            wallet_bal = c_wb
                        if equity <= 0:
                            upl = float(coin.get("unrealisedPnl") or 0)
                            equity = float(coin.get("equity") or 0) or (c_wb + upl)
                        break
                self._wallet_bal_cache = wallet_bal if wallet_bal > 0 else equity
                return equity, max(0.0, available), True
        except Exception as exc:
            print("  wallet-balance err:", exc)
        self._wallet_bal_cache = 0.0
        return 0.0, 0.0, False

    def wallet_balance(self) -> float:
        if self._wallet_cache is None:
            self.wallet_snapshot()
        return float(self._wallet_bal_cache or 0.0)

    def transfers_since(self, since_ms: int) -> tuple[float, int]:
        """Return (net_transfer_usd, latest_transfer_ts) from Bybit transaction log after since_ms."""
        if not self.has_keys() or since_ms <= 0:
            return 0.0, since_ms
        try:
            d = self.client.private_get(
                "/v5/account/transaction-log",
                {"accountType": "UNIFIED", "currency": "USDT", "limit": "50"},
            )
            net = 0.0
            latest = since_ms
            tx_types = {"TRANSFER_IN", "TRANSFER_OUT", "DEPOSIT", "WITHDRAW", "SUB_MEMBER_TRANSFER"}
            for r in (d.get("result") or {}).get("list") or []:
                t_type = str(r.get("type") or "").upper()
                if t_type not in tx_types and "TRANSFER" not in t_type and "DEPOSIT" not in t_type and "WITHDRAW" not in t_type:
                    continue
                ts = int(r.get("transactionTime") or 0)
                if ts <= since_ms:
                    continue
                chg = float(r.get("change") or 0.0)
                if abs(chg) >= 0.01:
                    net += chg
                    latest = max(latest, ts)
            return round(net, 2), latest
        except Exception:
            return 0.0, since_ms

    def account_equity(self) -> float:
        equity, _, ok = self.wallet_snapshot()
        return equity if ok else 0.0

    def available_margin(self) -> float:
        _, available, ok = self.wallet_snapshot()
        return available if ok else 0.0

    def usdt_free(self) -> float:
        return self.available_margin()

    def positions_snapshot(self) -> tuple[dict, bool]:
        if self._positions_cache is None:
            self._positions_cache = self._fetch_positions()
        return self._positions_cache

    def positions(self) -> dict:
        rows, _ok = self.positions_snapshot()
        return rows

    def _fetch_positions(self) -> tuple[dict, bool]:
        if not self.has_keys():
            return {}, True
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
                    "leverage": float(r.get("leverage") or 0),
                    "updated": int(r.get("updatedTime") or r.get("createdTime") or 0),
                    "created": int(r.get("createdTime") or r.get("updatedTime") or 0),
                }
            return out, True
        except Exception as exc:
            print("  positions err:", exc)
            return {}, False

    def recover_open_ts(self, symbol: str, side: int, target_size: float, now_ms: int) -> tuple[int, float, float]:
        """First fill of the open position: time, price and size before any pyramid add."""
        if not self.has_keys():
            return now_ms, 0.0, target_size
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
            fills = []
            for r in rows:
                if str(r.get("execType") or "Trade") != "Trade":
                    continue
                if str(r.get("side")) == want_side:
                    q = float(r.get("execQty") or 0)
                    ts = int(r.get("execTime") or 0)
                    px = float(r.get("execPrice") or 0)
                    if q > 0 and ts > 0:
                        acc += q
                        fills.append((ts, px, q))
                        if acc >= target_size * 0.95:
                            break
            if not fills:
                return now_ms, 0.0, target_size
            first_ts, first_px, first_qty = fills[-1]
            base_qty = first_qty if len(fills) > 1 else target_size
            return first_ts, first_px, base_qty
        except Exception as exc:
            print("  recover_open_ts err:", symbol, exc)
            return now_ms, 0.0, target_size

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
            qty = round(qty / step) * step
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


def load_state(account_id: str | None = None) -> dict:
    acc = account_id or CURRENT_ACCOUNT_ID
    return read_account_json("futures_position_state", acc)


def save_state(state: dict, account_id: str | None = None) -> None:
    if not PERSIST_STATE:
        return
    acc = account_id or CURRENT_ACCOUNT_ID
    write_account_json("futures_position_state", acc, state, persist=True)


def close_journal(state: dict) -> list[dict]:
    rows = [r for r in (state.get("_closes") or []) if isinstance(r, dict)]
    rows.sort(key=lambda r: int(r.get("ts") or 0))
    return rows


def remember_close(state: dict, sym: str, ts: int, win: bool, opened_ts: int = 0) -> None:
    rows = close_journal(state)
    rows.append({
        "sym": sym,
        "ts": int(ts),
        "opened_ts": int(opened_ts or ts),
        "win": bool(win),
    })
    state["_closes"] = rows[-24:]


def account_streaks(state: dict) -> tuple[int, int]:
    loss = win = 0
    for row in close_journal(state):
        if row.get("win"):
            loss = 0
            win += 1
        else:
            win = 0
            loss += 1
    return loss, win


def seed_close_journal(trader: "FuturesTrader") -> list[dict]:
    """Last Schwartz-universe closes, so a restart keeps the loss streak."""
    if not trader.has_keys():
        return []
    try:
        payload = trader.client.private_get("/v5/position/closed-pnl", {"category": "linear", "limit": "50"})
    except Exception as exc:
        print("  closed-pnl streak seed failed:", exc, flush=True)
        return []
    allowed = set(ALTS_7)
    oct_2026_ms = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    for row in (payload.get("result") or {}).get("list") or []:
        sym = str(row.get("symbol") or "")
        if sym not in allowed:
            continue
        ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
        opened_ts = int(row.get("createdTime") or ts)
        # 1 Oct 2026 UTC: earlier alt closes are not this bot's streak.
        if ts < oct_2026_ms:
            continue
        rows.append({
            "sym": sym,
            "ts": ts,
            "opened_ts": opened_ts,
            "win": float(row.get("closedPnl") or 0) > 0,
        })
    rows.sort(key=lambda r: r["ts"])
    # Deduplicate simultaneous batch/split fills within a 30-minute bar window
    merged = []
    for r in rows:
        if merged and abs(int(r["ts"]) - int(merged[-1]["ts"])) <= 1800_000 and bool(r["win"]) == bool(merged[-1]["win"]):
            merged[-1]["ts"] = int(r["ts"])
            continue
        merged.append(r)
    return merged[-24:]


def lookup_closed_win(trader: "FuturesTrader", sym: str, opened_ts: int):
    """Win/loss of a position that disappeared from the exchange. None if unknown."""
    if not trader.has_keys():
        return None
    try:
        payload = trader.client.private_get(
            "/v5/position/closed-pnl",
            {"category": "linear", "symbol": sym, "limit": "20"},
        )
    except Exception as exc:
        print("  closed-pnl lookup failed:", sym, exc, flush=True)
        return None
    best = None
    for row in (payload.get("result") or {}).get("list") or []:
        ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
        if opened_ts and ts + 1000 < int(opened_ts):
            continue
        pnl = float(row.get("closedPnl") or 0)
        if best is None or ts >= best[0]:
            best = (ts, pnl > 0)
    if best is None:
        return None
    return best[1]


def main():
    global PERSIST_STATE, CURRENT_ACCOUNT_ID
    sys.stdout.reconfigure(encoding="utf-8")
    account_id = parse_account_arg()
    CURRENT_ACCOUNT_ID = account_id
    live = "--live" in sys.argv[1:]
    PERSIST_STATE = live
    check = "--check" in sys.argv[1:]
    hourly_close = "--hourly-close" in sys.argv[1:]
    now = int(time.time() * 1000)
    try:
        ensure_fresh()
    except Exception as exc:
        print("  fresh bars skipped:", exc, flush=True)
    now_dt = datetime.now(timezone.utc)
    if now_dt.minute <= 7:
        hourly_close = True
    params = load_params(account_id)
    if not params.get("enabled", True):
        print(f"[{account_id}] account is disabled in S3 config, skipping.", flush=True)
        return
    leverage = float(params.get("leverage", 7.0))
    sleeve_leverage = float(params.get("sleeve_leverage", 3.0))
    risk_pct = float(params.get("risk_pct", 0.075))
    max_margin_frac = float(params.get("max_margin_frac", 0.60))
    max_conc = int(params.get("max_conc", 2))
    pause_new_trades = bool(params.get("pause_new_trades", False))
    strategy_code = str(params.get("strategy", "schwartz_7x_sleeve_3x"))
    enable_schwartz = strategy_code != "sleeve_only"
    enable_sleeve = bool(params.get("enable_sleeve", True)) and (strategy_code != "schwartz_only")

    trader = FuturesTrader(live, account_id=account_id)
    sigs, series = scan_live_schwartz(now)
    exchange_pos, pos_ok = trader.positions_snapshot()
    equity, free, wallet_ok = trader.wallet_snapshot()
    wallet_bal = trader.wallet_balance()
    sleeve = CarrySleeve(trader, sleeve_lev=sleeve_leverage) if enable_sleeve else None
    state = load_state(account_id)
    snapshot_ok = (not trader.has_keys()) or (pos_ok and wallet_ok)
    if sleeve and snapshot_ok:
        sleeve.reconcile(state, exchange_pos)
        book = sleeve.book(state)
        if not book["legs"]:
            adopted_ts = 0
            for sym, row in exchange_pos.items():
                if sym in ALTS_7 or sym == "BTCUSDT":
                    continue
                if float(row.get("leverage") or 0) > max(3.5, sleeve_leverage + 0.5):
                    continue
                book["legs"][sym] = {
                    "side": int(row["side"]),
                    "qty": float(row["size"]),
                    "px": float(row["entry"]),
                }
                adopted_ts = max(adopted_ts, int(row.get("updated") or row.get("created") or 0))
            if book["legs"] and not int(book.get("rebalance_ts") or 0):
                book["rebalance_ts"] = adopted_ts or now
    sleeve_syms = sleeve.symbols(state) if sleeve else set()
    pos = {sym: row for sym, row in exchange_pos.items() if sym not in sleeve_syms}
    equity = trader.account_equity()
    free = trader.available_margin()
    wallet_bal = trader.wallet_balance()

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
        "account_id": account_id,
        "account_name": params.get("account_name", account_id),
        "exchange": params.get("exchange", "bybit"),
        "leverage": leverage,
        "sleeve_leverage": sleeve_leverage,
        "risk_pct": risk_pct,
        "usdt_free": round(free, 2),
        "account_equity": round(equity, 2),
        "open_positions": len(pos),
        "sleeve_positions": len(sleeve_syms),
        "pause_new_trades": pause_new_trades,
        "mode": "LIVE" if (live and trader.has_keys()) else ("NO KEYS" if not trader.has_keys() else "DRY-RUN / MONITOR"),
        "strategy": f"Schwartz {leverage:.0f}x" + (f" + Корзина {sleeve_leverage:.0f}x" if enable_sleeve else ""),
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
    write_account_json("futures_signals", account_id, sig_payload, persist=True)

    print(
        "[{}:{}] USDT free={:.2f} equity={:.2f} open={} signals={} BTC={:.1f} ({}) sleeve={} new_trades={}".format(
            account_id, sig_payload["mode"], free, equity, len(pos), len(sigs), btc_px, btc_regime,
            "on" if enable_sleeve else "off",
            "PAUSED" if pause_new_trades else "on",
        ),
        flush=True,
    )

    if check or not snapshot_ok:
        if not snapshot_ok:
            print("  exchange snapshot failed, state left unchanged", flush=True)
        return

    # 0. Check -3.0% basket stop on every 5m tick
    if sleeve and sleeve.check_stop(state, now):
        trader.invalidate_account()
        save_state(state)
    elif sleeve and sleeve.arm_exchange_stops(state):
        save_state(state)

    # 1. Manage open positions according to Schwartz rules
    # Clean stale state entries for symbols that are no longer open on the exchange
    seeded = seed_close_journal(trader)
    if seeded:
        existing = close_journal(state)
        latest_existing_ts = max((int(r.get("ts") or 0) for r in existing), default=0)
        latest_seeded_ts = max((int(r.get("ts") or 0) for r in seeded), default=0)
        if not existing or latest_seeded_ts > latest_existing_ts or len(seeded) != len(existing):
            state["_closes"] = seeded
            save_state(state)

    for stale_sym in list(state.keys()):
        if str(stale_sym).startswith("_"):
            continue
        if stale_sym not in pos and stale_sym not in sleeve_syms:
            row = state.get(stale_sym) or {}
            opened_stale = int(row.get("opened") or 0)
            win = lookup_closed_win(trader, stale_sym, opened_stale)
            if win is not None:
                remember_close(state, stale_sym, now, win, opened_ts=opened_stale)
                print(f"  EXTERNAL CLOSE {stale_sym} win={win}", flush=True)
            state.pop(stale_sym, None)

    for sym, p in list(pos.items()):
        st = state.get(sym) or {}
        s_coin = series.get(sym)
        series_atr = float(s_coin["atr1h"][-1]) if s_coin and len(s_coin["atr1h"]) else 0.008
        if not st.get("opened") or int(st.get("side") or p["side"]) != p["side"]:
            rec_ts, rec_px, rec_base = trader.recover_open_ts(sym, p["side"], p["size"], now)
            inferred_pyr = bool(st.get("pyr_done", False)) or (rec_base > 0 and float(p["size"]) >= rec_base * 1.25)
            net_tx, latest_tx_ts = trader.transfers_since(rec_ts)
            init_eq = max(100.0, equity - net_tx) if abs(net_tx) >= 15.0 else equity
            init_wb = max(100.0, wallet_bal - net_tx) if abs(net_tx) >= 15.0 else wallet_bal
            st = {
                "opened": rec_ts,
                "entry_px": rec_px or p["entry"],
                "base_qty": rec_base or p["size"],
                "atr": float(st.get("atr") or series_atr),
                "side": p["side"],
                "pyr_done": inferred_pyr,
                "tp1_done": bool(st.get("tp1_done", False)),
                "tp2_done": bool(st.get("tp2_done", False)),
                "sized_equity": round(init_eq, 2),
                "sized_wallet": round(init_wb, 2),
                "scaled_ts": rec_ts,
            }
            state[sym] = st
            print(
                f"  RECOVERED STATE {sym} opened={rec_ts} ({round((now - rec_ts)/3600000.0, 1)}h ago) base={st['base_qty']} pyr={inferred_pyr}",
                flush=True,
            )
        else:
            if not st.get("pyr_done") and float(st.get("base_qty") or 0) > 0 and float(p["size"]) >= float(st["base_qty"]) * 1.25:
                st["pyr_done"] = True
            st.setdefault("sized_equity", round(equity, 2))
            st.setdefault("sized_wallet", round(wallet_bal, 2))
            st.setdefault("scaled_ts", int(st.get("opened") or now))
        opened = int(st["opened"])
        held_h = max(0.0, (now - opened) / 3600000.0)
        entry_px = float(st.get("entry_px") or p["entry"] or 0.0)
        atr = float(st.get("atr") or series_atr)
        cur_px = trader.price(sym)
        if cur_px <= 0 or entry_px <= 0:
            continue
        move = p["side"] * (cur_px / entry_px - 1.0)
        peak = max(float(st.get("peak_move") or 0.0), move)
        if peak != float(st.get("peak_move") or 0.0):
            st["peak_move"] = peak
            state[sym] = st
            save_state(state)
        ema240_now = float(s_coin["ema240"][-1]) if s_coin else entry_px

        # Full size until one exit. Hard stop and the 96h limit on every 5m tick.
        # Fast bail, breakeven and the 10d EMA only on the hourly close.
        be_floor = 1.15 * atr if st.get("pyr_done") else 0.4 * atr
        should_close = False
        close_reason = ""
        if move <= -2.2 * atr:
            should_close, close_reason = True, "hard_stop"
        elif held_h >= 96.0:
            should_close, close_reason = True, "end_96h"
        elif hourly_close and held_h >= 0.8 and held_h <= 4.5 and move <= -0.8 * atr:
            should_close, close_reason = True, "fast_bail"
        elif hourly_close and peak >= 3.0 * atr and move <= be_floor:
            should_close, close_reason = True, "breakeven"
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
            avg_px = float(p["entry"] or entry_px)
            move_avg = p["side"] * (cur_px / avg_px - 1.0) if avg_px > 0 else move
            remember_close(state, sym, now, move_avg > 0.0011, opened_ts=opened)
            state.pop(sym, None)
            pos.pop(sym, None)
            save_state(state)
            continue

        # Check Pyramid add (+50% of initial size at +2.0 ATR), respecting free margin (rem_free)
        if not pause_new_trades and not st.get("pyr_done") and move >= 2.0 * atr:
            base_qty = float(st.get("base_qty") or p["size"])
            q_pyr = round(base_qty * 0.50, 4)
            lot = float((trader.instrument(sym).get("lotSizeFilter") or {}).get("qtyStep") or 0)
            if lot > 0:
                q_pyr = int(q_pyr / lot) * lot
            trader.invalidate_account()
            avail_pyr = trader.available_margin()
            need_pyr_margin = (q_pyr * cur_px) / max(leverage, 1.0)
            if sleeve and need_pyr_margin > avail_pyr and cur_px > 0:
                sleeve.release(state, need_pyr_margin - avail_pyr)
                trader.invalidate_account()
                avail_pyr = trader.available_margin()
            if need_pyr_margin > avail_pyr * 0.90 and cur_px > 0:
                q_pyr = (avail_pyr * 0.90 * leverage) / cur_px
                if lot > 0:
                    q_pyr = int(q_pyr / lot) * lot
            if q_pyr > 0 and (q_pyr * cur_px) / max(leverage, 1.0) >= 10.0:
                print(f"  PYRAMID (+2.0 ATR) {sym} added 50% qty={q_pyr} at {cur_px}", flush=True)
                if not live:
                    continue
                try:
                    trader.market_order(sym, p["side"], q_pyr, reduce_only=False)
                except Exception as exc:
                    print(f"  PYRAMID {sym} order failed: {exc}", flush=True)
                    continue
                st["pyr_done"] = True
                st["pyr_px"] = cur_px
                state[sym] = st
                save_state(state)

    # 2. Open new positions ONLY on the hourly candle close check (unless pause_new_trades is active)
    trader.invalidate_account()
    equity = trader.account_equity()
    rem_free = trader.available_margin()
    wallet_bal = trader.wallet_balance()
    sizing_eq = max(wallet_bal, equity)
    if pause_new_trades and hourly_close and sigs:
        print(f"  [{account_id}] pause_new_trades=ON: skipping {len(sigs)} new entry signal(s)", flush=True)
    for s in (sigs if (hourly_close and enable_schwartz and not pause_new_trades) else []):
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
        loss_streak, win_streak = account_streaks(state)
        coin_ls = 0 if loss_streak > 0 else int(s.get("c_lstreak") or 0)
        mult = size_multiplier(
            loss_streak,
            win_streak,
            coin_ls,
            float(s.get("hot_avg") or 0.0),
        )
        target_notional = (sizing_eq * risk_pct * mult) / stop_dist
        sleeve_m = sleeve.margin(state) if sleeve else 0.0
        raw_margin = min(
            target_notional / leverage,
            sizing_eq * max_margin_frac,
            (rem_free + sleeve_m) * 0.90,
        )
        if sleeve and sleeve.book(state)["legs"]:
            sleeve.flatten_all(state, reason="schwartz_signal")
            trader.invalidate_account()
            rem_free = trader.available_margin()
            equity = trader.account_equity()
            wallet_bal = trader.wallet_balance()
            sizing_eq = max(wallet_bal, equity)
        margin = min(raw_margin, rem_free * 0.90)
        if margin < 15.0:
            continue
        qty = margin * leverage / px
        lot = float((trader.instrument(sym).get("lotSizeFilter") or {}).get("qtyStep") or 0)
        if lot > 0:
            qty = int(qty / lot) * lot
        if qty <= 0:
            continue
        print(
            "  OPEN {} {} qty={:.4f} margin=${:.1f} lev={:.0f}x mult={:.2f} tp1={} bail={}".format(
                s["coin"],
                "LONG" if s["side"] == 1 else "SHORT",
                qty,
                margin,
                leverage,
                mult,
                s["tp1_px"],
                s["fast_bail_px"],
            ),
            flush=True,
        )
        if live:
            trader.set_leverage(sym, leverage)
            try:
                resp = trader.market_order(sym, s["side"], qty)
                print("   order:", json.dumps(resp)[:200], flush=True)
            except Exception as exc:
                print(f"  OPEN {sym} order failed: {exc}", flush=True)
                continue
        rem_free = max(0.0, rem_free - margin)
        state[sym] = {
            "opened": now,
            "entry_px": px,
            "base_qty": qty,
            "atr": atr,
            "side": s["side"],
            "pyr_done": False,
            "tp1_done": False,
            "tp2_done": False,
            "sized_equity": round(equity, 2),
            "sized_wallet": round(wallet_bal, 2),
            "scaled_ts": now,
            "target_margin_frac": round(margin / max(equity, 1.0), 6),
        }
        pos[sym] = {"side": s["side"], "size": qty, "entry": px, "updated": now}
        save_state(state)

    if sleeve:
        trader.invalidate_account()
        rem_free = trader.available_margin()
        if hourly_close:
            sleeve.park(
                state,
                max(0.0, rem_free),
                now,
                schwartz_open_count=len(pos),
                pause_new_trades=pause_new_trades,
            )

    state["_last_wallet"] = round(trader.wallet_balance(), 2)
    save_state(state)


if __name__ == "__main__":
    main()
