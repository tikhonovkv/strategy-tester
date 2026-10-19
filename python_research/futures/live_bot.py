"""Live futures bot: funding+price model (9-train / 4-trade), 3x, 24h hold.

Runs the SAME signal logic as the backtest (futures_9to4.py), then reconciles
with the real account and places/closes orders. Default is DRY-RUN (logs only).

Usage:
  python live_bot.py --dry-run          # log what would happen
  python live_bot.py --live             # place real orders (careful!)
  python live_bot.py --check            # show current positions + signals, no action
"""
from __future__ import annotations
import sys, time, json, os
from pathlib import Path

LAB = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))

from rest import BybitClient
from futures_dataset import build_rows, load_coin, FEE, HOUR, DAY

TRAIN_COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]
TRADE_COINS = ["ETHUSDT","SOLUSDT","ADAUSDT","SUIUSDT"]
TRADE_SHORT = {"ETHUSDT":"ETH","SOLUSDT":"SOL","ADAUSDT":"ADA","SUIUSDT":"SUI"}

WINDOW = 180
THR = 0.006
PK = 0.5
LEVERAGE = 3
STAKE = 0.20
HOLD_MS = DAY


def build_train_rows():
    import numpy as np
    now = int(time.time() * 1000)
    lo = now - WINDOW * DAY
    X, y, w = [], [], []
    for sym in TRAIN_COINS:
        rows = build_rows(sym)
        for t, feat, ret, paid, z in rows:
            if t < lo:
                continue
            net_l = ret - paid - FEE
            net_s = -ret + paid - FEE
            X.append(np.append(feat[:16], 1.0)); y.append(net_l); w.append(1.0)
            X.append(np.append(feat[:16], 0.0)); y.append(net_s); w.append(1.0)
    return np.vstack(X), np.array(y), np.array(w)


def predict_now():
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    X, y, w = build_train_rows()
    if len(X) < 800:
        print("not enough training rows:", len(X))
        return []
    model = HistGradientBoostingRegressor(max_iter=80, learning_rate=0.06, max_depth=3,
                                          min_samples_leaf=80, l2_regularization=1.0,
                                          early_stopping=False, random_state=0)
    model.fit(X, y, sample_weight=w)
    sigs = []
    for sym in TRADE_COINS:
        rows = build_rows(sym)
        if not rows:
            continue
        t, feat, ret, paid, z = rows[-1]
        f = feat[:16]
        net_l = float(model.predict(np.append(f, 1.0).reshape(1, -1))[0])
        net_s = float(model.predict(np.append(f, 0.0).reshape(1, -1))[0])
        side = 1 if net_l >= net_s else -1
        pred = max(net_l, net_s)
        if pred > THR:
            sigs.append({"symbol": sym, "side": side, "pred_net": pred, "ts": int(t)})
    return sigs


class FuturesTrader:
    def __init__(self, live: bool):
        self.live = live
        self.client = BybitClient()

    def usdt_free(self) -> float:
        d = self.client.private_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        for acct in (d.get("result") or {}).get("list") or []:
            for coin in acct.get("coin") or []:
                if str(coin.get("coin")) == "USDT":
                    return float(coin.get("walletBalance") or 0) - float(coin.get("locked") or 0)
        return 0.0

    def positions(self):
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
                "leverage": r.get("leverage"),
            }
        return out

    def set_leverage(self, symbol: str):
        for s in ("Buy", "Sell"):
            self.client.private_post("/v5/position/set-leverage", {
                "category": "linear", "symbol": symbol,
                (s.lower() + "Leverage"): str(LEVERAGE),
            })

    def instrument(self, symbol: str):
        d = self.client.public_get("/v5/market/instruments-info", {"category": "linear", "symbol": symbol})
        rows = (d.get("result") or {}).get("list") or []
        return rows[0] if rows else {}

    def market_order(self, symbol: str, side: int, qty: float, reduce_only: bool = False):
        payload = {
            "category": "linear", "symbol": symbol,
            "side": "Buy" if side == 1 else "Sell",
            "orderType": "Market", "qty": "{:.4f}".format(qty),
        }
        if reduce_only:
            payload["reduceOnly"] = True
        if self.live:
            return self.client.private_post("/v5/order/create", payload)
        return {"dry_run": True, "payload": payload}

    def close_position(self, symbol: str, pos):
        return self.market_order(symbol, -pos["side"], pos["size"], reduce_only=True)

    def price(self, symbol: str) -> float:
        d = self.client.public_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        rows = (d.get("result") or {}).get("list") or []
        return float(rows[0]["lastPrice"]) if rows else 0.0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv[1:]
    check = "--check" in sys.argv[1:]
    trader = FuturesTrader(live)

    sigs = predict_now()
    pos = trader.positions()
    free = trader.usdt_free()
    # write JSON for the Laravel dashboard
    (OUT / "futures_signals.json").write_text(json.dumps({
        "usdt_free": round(free, 2),
        "open_positions": len(pos),
        "signals": [{"symbol": TRADE_SHORT[s["symbol"]], "side": s["side"], "pred_net": round(float(s["pred_net"]), 4), "ts": int(s["ts"])} for s in sigs],
    }, ensure_ascii=False), encoding="utf-8")
    print("[{}] USDT free={:.2f} open_positions={}".format("LIVE" if live else "DRY-RUN", free, len(pos)))
    print("SIGNALS (model, latest funding timestamp):")
    for s in sigs:
        print("  {} {} pred_net={:.2f}%".format(TRADE_SHORT[s["symbol"]], "LONG" if s["side"]==1 else "SHORT", s["pred_net"]*100))

    if check:
        return

    for sym, p in pos.items():
        print("  open: {} side={} size={} entry={}".format(sym, p["side"], p["size"], p["entry"]))

    for s in sigs:
        sym = s["symbol"]
        if sym in pos:
            if pos[sym]["side"] == s["side"]:
                print("  skip {} (already same side)".format(TRADE_SHORT[sym]))
                continue
            print("  flip {}: close {} -> open {}".format(TRADE_SHORT[sym], pos[sym]["side"], s["side"]))
            if live:
                trader.close_position(sym, pos[sym])
        px = trader.price(sym)
        if px <= 0:
            print("  skip {} (no price)".format(TRADE_SHORT[sym])); continue
        notional = free * STAKE * LEVERAGE
        qty = notional / px
        instr = trader.instrument(sym)
        lot = float(instr.get("lotSizeFilter", {}).get("qtyStep") or 0)
        if lot > 0:
            qty = int(qty / lot) * lot
        if live:
            trader.set_leverage(sym)
        print("  OPEN {} {} qty={:.4f} notional={:.0f}".format(TRADE_SHORT[sym], "LONG" if s["side"]==1 else "SHORT", qty, notional))
        if live:
            r = trader.market_order(sym, s["side"], qty)
            print("   order:", json.dumps(r)[:200])

    if not sigs:
        print("no signals above threshold this tick")


if __name__ == "__main__":
    main()
