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
from accounts_config import (
    get_account,
    parse_account_arg,
    write_account_json,
)

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
                "book": "schwartz",
            })
        return trades, j
    return [], j


def load_sleeve(tag: str) -> dict:
    best_row = {}
    best_count = -1
    for base in (OUT, LAB):
        path = base / "sleeve_compare.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        row = data.get(tag) if isinstance(data, dict) else None
        if isinstance(row, dict):
            cnt = len(row.get("trades") or []) + len(row.get("schwartz_trades") or [])
            if cnt > best_count:
                best_count = cnt
                best_row = row
    return best_row


def fetch_live_executions_and_closed_pnl(account_id: str = "main"):
    from rest import BybitClient
    client = BybitClient(account_id=account_id)
    try:
        client._require_keys()
    except BaseException:
        return [], [], [], 2000.0

    now = int(time.time() * 1000)
    day = 86400000

    # 1. Current wallet balance
    wallet_usdt = 2000.0
    try:
        d = client.private_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        for acct in (d.get("result") or {}).get("list") or []:
            equity = float(acct.get("totalEquity") or 0)
            if equity > 0:
                wallet_usdt = equity
                break
            for coin in acct.get("coin") or []:
                if str(coin.get("coin")) == "USDT":
                    wallet_usdt = float(coin.get("walletBalance") or 2000.0)
    except Exception:
        pass

    # 2. Recent executions (last 14 days with cursor pagination so partial fills never overflow limit=100)
    fills = []
    seen_exec_ids = set()
    for i in range(2):
        end = now - i * 7 * day
        start = end - 7 * day
        cursor = ""
        for _page in range(5):
            try:
                q_params = {"category": "linear", "limit": "100", "startTime": str(start), "endTime": str(end)}
                if cursor:
                    q_params["cursor"] = cursor
                d = client.private_get("/v5/execution/list", q_params)
                res_obj = d.get("result") or {}
                rows = res_obj.get("list") or []
                for r in rows:
                    if str(r.get("execType") or "Trade") != "Trade":
                        continue
                    exec_id = str(r.get("execId") or "")
                    if exec_id and exec_id in seen_exec_ids:
                        continue
                    if exec_id:
                        seen_exec_ids.add(exec_id)
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
                cursor = str(res_obj.get("nextPageCursor") or "")
                if not cursor or len(rows) < 100:
                    break
            except Exception:
                break
    fills.sort(key=lambda x: x["ts"])

    # 3. Closed PnL history over last 90 days (first call without time filter for latest records, then weekly windows)
    closed_pnl = []
    seen_ids = set()
    for i in range(-1, 13):
        params = {"category": "linear", "limit": "100"}
        if i >= 0:
            end = now - i * 7 * day
            start = end - 7 * day
            params["startTime"] = str(start)
            params["endTime"] = str(end)
        for attempt in range(2):
            try:
                d = client.private_get("/v5/position/closed-pnl", params)
                for r in (d.get("result") or {}).get("list") or []:
                    oid = str(r.get("orderId") or "")
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                    ut = int(r.get("updatedTime") or r.get("createdTime") or 0)
                    sym = str(r.get("symbol") or "")
                    close_side = str(r.get("side") or "")
                    pos_side = 1 if close_side.lower() == "sell" else -1
                    closed_size = float(r.get("closedSize") or r.get("qty") or 0)
                    # Recover true entry timestamp from trade executions (Bybit createdTime on closed-pnl is the closing order timestamp)
                    entry_ts = int(r.get("createdTime") or ut)
                    acc_q = 0.0
                    for fl in reversed(fills):
                        if fl["symbol"] == sym and fl["side"] == pos_side and fl["ts"] <= ut:
                            if acc_q >= closed_size * 0.98 and (entry_ts - fl["ts"]) > 60000:
                                break
                            acc_q += fl["qty"]
                            entry_ts = fl["ts"]
                            if acc_q >= closed_size * 1.005:
                                break
                    # Align hourly entry timestamps (executed within 10m of the hour close) to the exact HH:00 candle close
                    display_entry_ts = (entry_ts // 3600000) * 3600000 if (entry_ts % 3600000) <= 10 * 60 * 1000 else entry_ts
                    display_exit_ts = (ut // 3600000) * 3600000 if (ut % 3600000) <= 10 * 60 * 1000 and (ut - entry_ts) >= 1800000 else ut
                    closed_pnl.append({
                        "event_type": "trade",
                        "order_id": oid,
                        "symbol": sym,
                        "coin": sym.replace("USDT", ""),
                        "side": pos_side,
                        "created_ts": display_entry_ts,
                        "exec_entry_ts": entry_ts,
                        "closed_ts": ut,
                        "time": iso(display_entry_ts),
                        "exit_time": iso(display_exit_ts),
                        "qty": closed_size,
                        "entry_price": float(r.get("avgEntryPrice") or 0),
                        "exit_price": float(r.get("avgExitPrice") or 0),
                        "leverage": float(r.get("leverage") or 1),
                        "pnl_usd": round(float(r.get("closedPnl") or 0), 2),
                    })
                break
            except Exception:
                time.sleep(0.3)
    closed_pnl.sort(key=lambda x: x["closed_ts"])

    # 4. Account balance transfers (deposits / withdrawals / internal transfers) over last 90 days
    transfers = []
    seen_tx = set()
    transfer_types = {"TRANSFER_IN", "TRANSFER_OUT", "DEPOSIT", "WITHDRAW", "SUB_MEMBER_TRANSFER"}
    for i in range(-1, 13):
        t_params = {"accountType": "UNIFIED", "currency": "USDT", "limit": "50"}
        if i >= 0:
            end = now - i * 7 * day
            start = end - 7 * day
            t_params["startTime"] = str(start)
            t_params["endTime"] = str(end)
        try:
            d = client.private_get("/v5/account/transaction-log", t_params)
            for r in (d.get("result") or {}).get("list") or []:
                tx_type = str(r.get("type") or "").upper()
                if tx_type not in transfer_types and "TRANSFER" not in tx_type and "DEPOSIT" not in tx_type and "WITHDRAW" not in tx_type:
                    continue
                tx_id = str(r.get("id") or r.get("transId") or "")
                tx_ts = int(r.get("transactionTime") or 0)
                change = float(r.get("change") or 0.0)
                if abs(change) < 0.01 or tx_ts <= 0:
                    continue
                dedup_key = tx_id or f"{tx_ts}_{round(change, 2)}"
                if dedup_key in seen_tx:
                    continue
                seen_tx.add(dedup_key)
                is_in = change > 0
                transfers.append({
                    "event_type": "transfer",
                    "is_transfer": True,
                    "transfer_dir": "in" if is_in else "out",
                    "order_id": dedup_key,
                    "symbol": "USDT",
                    "coin": "USDT",
                    "side": 1 if is_in else -1,
                    "created_ts": tx_ts,
                    "closed_ts": tx_ts,
                    "ts": tx_ts,
                    "time": iso(tx_ts),
                    "exit_time": iso(tx_ts),
                    "qty": abs(round(change, 2)),
                    "entry_price": 1.0,
                    "exit_price": 1.0,
                    "leverage": 1.0,
                    "amount_usd": round(change, 2),
                    "pnl_usd": 0.0,
                    "pnl_pct": 0.0,
                    "cash_balance": round(float(r.get("cashBalance") or 0.0), 2),
                })
        except Exception:
            pass
    transfers.sort(key=lambda x: x["closed_ts"])
    return fills, closed_pnl, transfers, round(wallet_usdt, 2)


def build_real_equity_curve(
    closed_pnl: list[dict],
    transfers: list[dict],
    wallet_now: float,
    cut_ts: int,
    bt_trades: list[dict] | None = None,
    account_leverage: float = 7.0,
    sleeve_leverage: float = 3.0,
):
    """Reconstruct actual account balance, deposits/withdrawals & time-weighted PnL % curve inside [cut_ts, now]."""
    now_ms = int(time.time() * 1000)
    in_period = [dict(c) for c in closed_pnl if c["closed_ts"] >= cut_ts]
    in_transfers = [dict(t) for t in transfers if t["closed_ts"] >= cut_ts]
    total_pnl_period = sum(c["pnl_usd"] for c in in_period)
    net_transfers_period = sum(t["amount_usd"] for t in in_transfers)
    raw_start = wallet_now - total_pnl_period - net_transfers_period
    start_bal = max(10.0, raw_start) if wallet_now > 0 else 2000.0

    pts = [[iso(max(cut_ts, now_ms - 90 * 86400000 if cut_ts == 0 else cut_ts)), 0.0, round(start_bal, 2)]]
    running_bal = start_bal
    twr_mult = 1.0

    events = sorted(
        in_period + in_transfers,
        key=lambda e: (int(e["closed_ts"]), 0 if e.get("is_transfer") else 1),
    )

    for ev in events:
        if ev.get("is_transfer"):
            amt = float(ev.get("amount_usd") or 0.0)
            running_bal = max(0.0, running_bal + amt)
            ev["eq_after"] = round(running_bal, 2)
            ev["pnl_pct"] = 0.0
            pct_now = round((twr_mult - 1.0) * 100.0, 2)
            pts.append([ev["exit_time"], pct_now, round(running_bal, 2)])
            continue

        c = ev
        base_before = max(running_bal, 50.0)
        trade_ret = float(c["pnl_usd"]) / base_before
        twr_mult = max(0.01, twr_mult * (1.0 + trade_ret))
        running_bal += c["pnl_usd"]
        pct = round((twr_mult - 1.0) * 100.0, 2)
        c["pnl_pct"] = round(trade_ret * 100.0, 2)
        c["eq_after"] = round(running_bal, 2)
        matched_bt = False
        if bt_trades:
            for bt in bt_trades:
                if bt["coin"] == c["coin"] and bt["side"] == c["side"] and abs(bt["ts"] - c["created_ts"]) <= 36 * 3600000:
                    matched_bt = True
                    break
        schwartz_names = {"ETH", "SOL", "XRP", "ADA", "AVAX", "LINK", "SUI"}
        c["is_sleeve"] = c["coin"] not in schwartz_names and float(c.get("leverage") or 0) <= max(3.5, sleeve_leverage + 0.5)
        c["is_schwartz"] = (not c["is_sleeve"]) and bool(
            matched_bt or (c["created_ts"] >= 1792713600000 and abs(c.get("leverage", 0) - account_leverage) < 1.5)
        )
        pts.append([c["exit_time"], pct, round(running_bal, 2)])

    final_ret_pct = round((twr_mult - 1.0) * 100.0, 2)
    pts.append([iso(now_ms), final_ret_pct, round(running_bal, 2)])
    return {
        "start_balance": round(start_bal, 2),
        "end_balance": round(running_bal, 2),
        "pnl_usd": round(total_pnl_period, 2),
        "net_transfers_usd": round(net_transfers_period, 2),
        "deposits_count": sum(1 for t in in_transfers if t["amount_usd"] > 0),
        "withdrawals_count": sum(1 for t in in_transfers if t["amount_usd"] < 0),
        "return_pct": final_ret_pct,
        "trades_count": len(in_period),
        "wins_count": sum(1 for c in in_period if c["pnl_usd"] > 0),
        "schwartz_count": sum(1 for c in in_period if c.get("is_schwartz")),
        "curve": pts,
        "closed_trades": list(reversed(events)),
    }


def match(
    backtest: list[dict],
    live: list[dict],
    window_h: int = 12,
    wallet_now: float = 2000.0,
    acct_lev: float = 7.0,
    sleeve_lev: float = 3.0,
):
    win = window_h * 3600000
    used = [False] * len(live)
    results = []
    for bt in backtest:
        is_slv = (bt.get("book") == "sleeve")
        base_lev = 3.0 if is_slv else 7.0
        target_lev = sleeve_lev if is_slv else acct_lev
        bt_eq_after = float(bt.get("eq") or 2000.0)
        bt_pnl = float(bt.get("pnl_usd") or 0.0)
        bt_eq_before = max(200.0, bt_eq_after - bt_pnl)
        eq_ratio = (wallet_now / bt_eq_before) if wallet_now > 0 else 1.0
        lev_ratio = target_lev / max(base_lev, 1.0)
        expected_qty = float(bt.get("qty") or 0.0) * eq_ratio * lev_ratio

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
            results.append({
                "status": "BACKTEST",
                "quality": "NOT_ON_ACCOUNT",
                **bt,
                "scaled_qty": round(expected_qty, 4),
            })
        else:
            used[best] = True
            lf = live[best]
            price_diff = (
                abs(lf["px"] - bt["entry_price"]) / bt["entry_price"] * 100
                if bt.get("entry_price")
                else None
            )
            ref_qty = expected_qty if expected_qty > 0 else float(bt.get("qty") or 0.0)
            qty_diff = (
                abs(lf["qty"] - ref_qty) / ref_qty * 100
                if ref_qty > 0
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
                "scaled_qty": round(expected_qty, 4),
                "live_qty": round(lf["qty"], 4),
                "live_px": lf["px"],
                "price_diff_pct": round(price_diff, 2) if price_diff is not None else None,
                "qty_diff_pct": round(qty_diff, 2) if qty_diff is not None else None,
                "quality": quality,
            })
    extras = [lf for li, lf in enumerate(live) if not used[li]]
    return results, extras


def render_html(periods, account_id: str | None = None):
    acc_id = account_id or parse_account_arg()
    acc = get_account(acc_id)
    acct_lev = float(acc.get("leverage", 7.0))
    sleeve_lev = float(acc.get("sleeve_leverage", 3.0))
    enable_sleeve = bool(acc.get("enable_sleeve", True)) and (str(acc.get("strategy")) != "schwartz_only")
    now_ms = int(time.time() * 1000)
    day = 86400000
    live_fills, closed_pnl, transfers, wallet_now = fetch_live_executions_and_closed_pnl(acc_id)

    jperiods = []
    for label, tag, days in periods:
        sleeve = load_sleeve(tag) if enable_sleeve else {}
        if enable_sleeve and (sleeve.get("schwartz_trades") or sleeve.get("trades")):
            bt = []
            for tr in sleeve.get("schwartz_trades") or []:
                row = dict(tr)
                row["ts"] = int(row.get("t") or row.get("ts") or 0)
                row["exit_ts"] = int(row.get("exit_ts") or row["ts"])
                row["book"] = "schwartz"
                bt.append(row)
            for leg in sleeve.get("trades") or []:
                row = dict(leg)
                row["ts"] = int(row.get("t") or row.get("ts") or 0)
                row["exit_ts"] = int(row.get("exit_ts") or row["ts"])
                row["book"] = "sleeve"
                row["pnl_pct"] = float(row.get("pnl_pct") or row.get("raw_net_pct") or 0.0)
                bt.append(row)
            bt.sort(key=lambda x: (int(x.get("exit_ts") or 0), int(x.get("ts") or 0)))
            raw_j = sleeve
        else:
            bt, raw_j = load_backtest(tag)

        cut_ts = 0 if days >= 1000 else now_ms - days * day
        real_acct = build_real_equity_curve(
            closed_pnl,
            transfers,
            wallet_now,
            cut_ts,
            bt,
            account_leverage=acct_lev,
            sleeve_leverage=sleeve_lev,
        )
        res, extras = match(bt, live_fills, wallet_now=wallet_now, acct_lev=acct_lev, sleeve_lev=sleeve_lev)
        matched = sum(1 for r in res if r["status"] == "MATCHED")
        missed = sum(1 for r in res if r["status"] != "MATCHED")
        n_off = sum(1 for r in res if r["status"] == "MATCHED" and r.get("quality") != "OK")
        n_ok = matched - n_off
        wins = sum(1 for r in res if r.get("pnl_pct", 0) > 0)
        wr = round(wins / max(1, len(res)) * 100.0, 1)
        if sleeve.get("equity_pct"):
            ret_pct = round(float(sleeve.get("return_pct", 0.0)), 2)
            dd_pct = round(float(sleeve.get("max_dd_pct", 0.0)), 1)
            final_eq = round(float(sleeve.get("final", 2000.0)), 2)
            equity = sleeve.get("equity_pct") or []
        else:
            ret_pct = round(float(raw_j.get("return_pct", 0.0)), 2)
            dd_pct = round(float(raw_j.get("max_dd_pct", 0.0)), 1)
            final_eq = round(float(raw_j.get("final", 2000.0)), 2)
            equity = raw_j.get("equity_pct") or []

        matched = max(matched, int(real_acct.get("schwartz_count", 0)))

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
            "equity": equity,
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
                    "scaled_qty": r.get("scaled_qty"),
                    "margin": r.get("margin"),
                    "eff_lev": sleeve_lev if (r.get("book") == "sleeve") else acct_lev,
                    "raw_net_pct": r.get("raw_net_pct", 0.0),
                    "pnl_usd": r.get("pnl_usd", 0.0),
                    "eq": r.get("eq", 2000.0),
                    "reason": r.get("reason", "end"),
                    "pyr": bool(r.get("pyr", False)),
                    "book": r.get("book") or "schwartz",
                    "path": r.get("path"),
                    "quality": r.get("quality", "OK"),
                    "price_diff_pct": r.get("price_diff_pct"),
                    "qty_diff_pct": r.get("qty_diff_pct"),
                    "status": r["status"],
                    "pnl_pct": r["pnl_pct"],
                }
                for r in sorted(res, key=lambda x: (int(x.get("exit_ts") or 0), int(x.get("ts") or 0)), reverse=True)
            ],
        })

    payload = {
        "account_id": acc_id,
        "account_name": acc.get("name", acc_id),
        "exchange": acc.get("exchange", "bybit"),
        "leverage": acct_lev,
        "sleeve_leverage": sleeve_lev,
        "risk_pct": float(acc.get("risk_pct", 0.075)),
        "periods": jperiods,
        "wallet_usdt": wallet_now,
        "cron_schedule": "Каждые 5 минут — сверка счёта Bybit, переводов и пропорций позиций; каждый час (:02) — докачка свечей и вход по новым сигналам",
        "strategy": f"Шварц {acct_lev:.0f}x" + (f" + спящая корзина {sleeve_lev:.0f}x (вкл. при паузе ≥5д)" if enable_sleeve else ""),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    write_account_json("futures_compare", acc_id, payload, persist=True)
    print(f"saved futures_compare ({acc_id}) with real_account PnL, transfers and backtest curves", flush=True)


if __name__ == "__main__":
    periods = [
        ("Квартал", "recent90", 90),
        ("Год", "recent365", 365),
        ("Вся история", "causal_full", 1800),
        ("Месяц", "recent30", 30),
    ]
    render_html(periods)
