# -*- coding: utf-8 -*-
"""Verification tool: Real Bybit Account PnL + Strategy Matching + Backtest Reference.

Separates:
1. REAL ACCOUNT PNL & FILLS:
   - Pulls closed positions from `/v5/position/closed-pnl` (up to 90 days) to build the
     REAL account equity & cumulative PnL curve for each period (`Квартал`, `Год`, `Вся история`, `Месяц`).
   - Pulls recent executions from `/v5/execution/list` and matches them against strategy signals.
2. STRATEGY BACKTEST REFERENCE:
   - Exports each period's backtest curve and trade list separately, clearly marked as
     either `✓ ИСПОЛНЕНО НА СЧЁТЕ` (MATCHED) or `РАСЧЁТ БЭКТЕСТА` (BACKTEST).
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from causal import load_coin

LAB = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
COIN_NAMES = {
    0: "BTC", 1: "ETH", 2: "SOL", 3: "XRP", 4: "BNB",
    5: "ADA", 6: "AVAX", 7: "LINK", 8: "SUI",
}
SYMBOLS = {
    "ETH": "ETHUSDT", "SOL": "SOLUSDT", "ADA": "ADAUSDT", "SUI": "SUIUSDT",
    "XRP": "XRPUSDT", "AVAX": "AVAXUSDT", "LINK": "LINKUSDT",
}
HOUR = 3_600_000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_backtest(tag: str):
    path = OUT / f"stable_{tag}.json"
    if not path.exists():
        path = LAB / f"stable_{tag}.json"
    j = json.loads(path.read_text(encoding="utf-8"))

    if "rich_trades" in j and j["rich_trades"]:
        trades = []
        for tr in j["rich_trades"]:
            trades.append({
                "ts": int(tr["t"]),
                "exit_ts": int(tr["exit_ts"]),
                "hold_h": int(tr.get("hold_h", 24)),
                "coin": tr["coin"],
                "side": int(tr["side"]),
                "entry_price": tr["entry_price"],
                "exit_price": tr["exit_price"],
                "path": tr.get("path"),
                "qty": tr["qty"],
                "margin": tr.get("margin"),
                "eff_lev": tr.get("eff_lev", 7.0),
                "raw_net_pct": tr.get("raw_net_pct", 0.0),
                "pnl_usd": tr.get("pnl_usd", 0.0),
                "pnl_pct": round(float(tr["pnl_pct"]), 2),
                "eq": tr.get("eq", 2000.0),
                "reason": tr.get("reason", "end"),
                "pyr": bool(tr.get("pyr", False)),
            })
        return trades, j
    return [], j


def fetch_live_executions_and_closed_pnl():
    from rest import BybitClient
    client = BybitClient()
    try:
        client._require_keys()
    except BaseException:
        return [], [], 2000.0

    now = int(time.time() * 1000)
    day = 86400000

    # 1. Current wallet balance
    wallet_usdt = 2000.0
    try:
        d = client.private_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        for acct in (d.get("result") or {}).get("list") or []:
            for coin in acct.get("coin") or []:
                if str(coin.get("coin")) == "USDT":
                    wallet_usdt = float(coin.get("walletBalance") or 2000.0)
    except Exception:
        pass

    # 2. Recent executions (last 14 days, 2 fast calls)
    fills = []
    for i in range(2):
        end = now - i * 7 * day
        start = end - 7 * day
        try:
            d = client.private_get(
                "/v5/execution/list",
                {"category": "linear", "limit": "100", "startTime": str(start), "endTime": str(end)},
            )
            for r in (d.get("result") or {}).get("list") or []:
                sym = str(r.get("symbol") or "")
                coin = sym.replace("USDT", "")
                fills.append({
                    "coin": coin,
                    "symbol": sym,
                    "side": 1 if str(r.get("side")).lower() == "buy" else -1,
                    "ts": int(r.get("execTime") or 0),
                    "qty": float(r.get("execQty") or 0),
                    "px": float(r.get("execPrice") or 0),
                })
        except Exception:
            pass
    fills.sort(key=lambda x: x["ts"])

    # 3. Closed PnL history over last 90 days (13 weekly windows, fast)
    closed_pnl = []
    seen_ids = set()
    for i in range(13):
        end = now - i * 7 * day
        start = end - 7 * day
        try:
            d = client.private_get(
                "/v5/position/closed-pnl",
                {"category": "linear", "limit": "100", "startTime": str(start), "endTime": str(end)},
            )
            for r in (d.get("result") or {}).get("list") or []:
                oid = str(r.get("orderId") or "")
                if oid in seen_ids:
                    continue
                seen_ids.add(oid)
                ut = int(r.get("updatedTime") or r.get("createdTime") or 0)
                ct = int(r.get("createdTime") or ut)
                sym = str(r.get("symbol") or "")
                # Note: in Bybit closed-pnl, side='Sell' means the closing order was Sell (i.e. LONG position closed)
                close_side = str(r.get("side") or "")
                pos_side = 1 if close_side.lower() == "sell" else -1
                closed_pnl.append({
                    "order_id": oid,
                    "symbol": sym,
                    "coin": sym.replace("USDT", ""),
                    "side": pos_side,
                    "created_ts": ct,
                    "closed_ts": ut,
                    "time": iso(ct),
                    "exit_time": iso(ut),
                    "qty": float(r.get("closedSize") or r.get("qty") or 0),
                    "entry_price": float(r.get("avgEntryPrice") or 0),
                    "exit_price": float(r.get("avgExitPrice") or 0),
                    "leverage": float(r.get("leverage") or 1),
                    "pnl_usd": round(float(r.get("closedPnl") or 0), 2),
                })
        except Exception:
            pass
    closed_pnl.sort(key=lambda x: x["closed_ts"])
    return fills, closed_pnl, round(wallet_usdt, 2)


def build_real_equity_curve(closed_pnl: list[dict], wallet_now: float, cut_ts: int):
    """Reconstruct actual account balance & PnL % curve inside the period [cut_ts, now]."""
    now_ms = int(time.time() * 1000)
    in_period = [c for c in closed_pnl if c["closed_ts"] >= cut_ts]
    total_pnl_period = sum(c["pnl_usd"] for c in in_period)
    start_bal = max(100.0, wallet_now - total_pnl_period)

    pts = [[iso(max(cut_ts, now_ms - 90 * 86400000 if cut_ts == 0 else cut_ts)), 0.0, round(start_bal, 2)]]
    running_bal = start_bal
    for c in in_period:
        running_bal += c["pnl_usd"]
        pct = round((running_bal / start_bal - 1.0) * 100.0, 2)
        c["pnl_pct"] = round((c["pnl_usd"] / start_bal) * 100.0, 2)
        c["eq_after"] = round(running_bal, 2)
        pts.append([c["exit_time"], pct, round(running_bal, 2)])
    pts.append([iso(now_ms), round((running_bal / start_bal - 1.0) * 100.0, 2), round(running_bal, 2)])
    return {
        "start_balance": round(start_bal, 2),
        "end_balance": round(running_bal, 2),
        "pnl_usd": round(total_pnl_period, 2),
        "return_pct": round((running_bal / start_bal - 1.0) * 100.0, 2),
        "trades_count": len(in_period),
        "wins_count": sum(1 for c in in_period if c["pnl_usd"] > 0),
        "curve": pts,
        "closed_trades": list(reversed(in_period)),
    }


def match(backtest: list[dict], live: list[dict], window_h: int = 12):
    win = window_h * 3600000
    used = [False] * len(live)
    results = []
    for bt in backtest:
        best = None
        for li, lf in enumerate(live):
            if used[li]:
                continue
            if lf["coin"] != bt["coin"] or lf["side"] != bt["side"]:
                continue
            if abs(lf["ts"] - bt["ts"]) <= win:
                if best is None or abs(lf["ts"] - bt["ts"]) < abs(live[best]["ts"] - bt["ts"]):
                    best = li
        if best is None:
            results.append({"status": "BACKTEST", "quality": "NOT_ON_ACCOUNT", **bt})
        else:
            used[best] = True
            lf = live[best]
            price_diff = (
                abs(lf["px"] - bt["entry_price"]) / bt["entry_price"] * 100
                if bt.get("entry_price")
                else None
            )
            qty_diff = (
                abs(lf["qty"] - bt["qty"]) / bt["qty"] * 100
                if bt.get("qty")
                else None
            )
            issues = []
            if price_diff is not None and price_diff > 0.5:
                issues.append("PRICE")
            if qty_diff is not None and qty_diff > 15:
                issues.append("QTY")
            quality = "OK" if not issues else "_".join(issues)
            results.append({
                "status": "MATCHED",
                **bt,
                "live_qty": round(lf["qty"], 4),
                "live_px": lf["px"],
                "price_diff_pct": round(price_diff, 2) if price_diff is not None else None,
                "qty_diff_pct": round(qty_diff, 2) if qty_diff is not None else None,
                "quality": quality,
            })
    extras = [lf for li, lf in enumerate(live) if not used[li]]
    return results, extras


def render_html(periods):
    now_ms = int(time.time() * 1000)
    day = 86400000
    live_fills, closed_pnl, wallet_now = fetch_live_executions_and_closed_pnl()

    jperiods = []
    for label, tag, days in periods:
        bt, raw_j = load_backtest(tag)
        res, extras = match(bt, live_fills)
        matched = sum(1 for r in res if r["status"] == "MATCHED")
        missed = sum(1 for r in res if r["status"] != "MATCHED")
        n_off = sum(1 for r in res if r["status"] == "MATCHED" and r.get("quality") != "OK")
        n_ok = matched - n_off
        wins = sum(1 for r in res if r.get("pnl_pct", 0) > 0)
        wr = round(wins / max(1, len(res)) * 100.0, 1)
        ret_pct = round(float(raw_j.get("return_pct", 0.0)), 2)
        dd_pct = round(float(raw_j.get("max_dd_pct", 0.0)), 1)
        final_eq = round(float(raw_j.get("final", 2000.0)), 2)

        cut_ts = 0 if days >= 1000 else now_ms - days * day
        real_acct = build_real_equity_curve(closed_pnl, wallet_now, cut_ts)

        jperiods.append({
            "label": label,
            "total": len(res),
            "wins": wins,
            "win_rate": wr,
            "matched": matched,
            "missed": missed,
            "verified": n_ok,
            "extras": len(extras),
            "fidelity": round((matched / len(res) * 100) if res else 0.0, 1),
            "pnl_sum": round(sum(r["pnl_pct"] for r in res), 2),
            "return_pct": ret_pct,
            "max_dd_pct": dd_pct,
            "final_eq": final_eq,
            "equity": raw_j.get("equity_pct") or [],
            "real_account": real_acct,
            "trades": [
                {
                    "time": iso(r["ts"]),
                    "exit_time": iso(r["exit_ts"]),
                    "hold_h": r.get("hold_h", 24),
                    "coin": r["coin"],
                    "side": r["side"],
                    "entry_price": r["entry_price"],
                    "exit_price": r["exit_price"],
                    "qty": r["qty"],
                    "margin": r.get("margin"),
                    "eff_lev": r.get("eff_lev", 7.0),
                    "raw_net_pct": r.get("raw_net_pct", 0.0),
                    "pnl_usd": r.get("pnl_usd", 0.0),
                    "eq": r.get("eq", 2000.0),
                    "reason": r.get("reason", "end"),
                    "pyr": bool(r.get("pyr", False)),
                    "path": r.get("path"),
                    "quality": r.get("quality", "OK"),
                    "price_diff_pct": r.get("price_diff_pct"),
                    "qty_diff_pct": r.get("qty_diff_pct"),
                    "status": r["status"],
                    "pnl_pct": r["pnl_pct"],
                }
                for r in sorted(res, key=lambda x: x["ts"], reverse=True)
            ],
        })

    payload = {
        "periods": jperiods,
        "wallet_usdt": wallet_now,
        "cron_schedule": "Каждые 5 минут — сверка счёта Bybit и контроль открытых позиций; каждый час (:02) — докачка свечей и вход по новым сигналам",
        "strategy": "Schwartz Pitbull Championship 7x",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    for target_dir in {LAB, OUT}:
        (target_dir / "futures_compare.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    print("saved futures_compare.json with real_account PnL and backtest curves", flush=True)


if __name__ == "__main__":
    periods = [
        ("Квартал", "recent90", 90),
        ("Год", "recent365", 365),
        ("Вся история", "causal_full", 1800),
        ("Месяц", "recent30", 30),
    ]
    render_html(periods)
