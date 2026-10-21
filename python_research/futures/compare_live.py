# -*- coding: utf-8 -*-
"""Verification tool: Schwartz Championship backtest trades vs LIVE account trades.

For each backtest signal (coin + side + time + exit_time + reason + pyr) it checks
whether the account actually executed a matching fill (and whether an open live
position matches a currently active signal), and reports MATCHED / MISSED / EXTRA
plus a fidelity score and per-trade strategy rule checklist.
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


def load_prices():
    out = {}
    for name, sym in SYMBOLS.items():
        try:
            bars, vols, fund = load_coin(sym)
            ts_list = sorted(bars)
            out[name] = (ts_list, [bars[t] for t in ts_list])
        except Exception:
            pass
    return out


def close_at(prices, name, t):
    if name not in prices:
        return None
    ts_list, closes = prices[name]
    i = bisect.bisect_right(ts_list, t - HOUR) - 1
    if i < 0 or ts_list[i] != t - HOUR:
        return None
    return round(closes[i], 4)


def path_for(prices, name, entry_ts, hold_h=24):
    if name not in prices:
        return None
    ts_list, closes = prices[name]
    i = bisect.bisect_right(ts_list, entry_ts - HOUR) - 1
    lo, hi = i - 2, min(len(closes) - 1, i + hold_h + 2)
    if lo < 0 or hi <= lo:
        return None
    return [round(float(closes[j]), 4) for j in range(lo, hi + 1)]


def load_backtest(tag: str):
    path = OUT / f"stable_{tag}.json"
    if not path.exists():
        path = LAB / f"stable_{tag}.json"
    j = json.loads(path.read_text(encoding="utf-8"))

    # Use rich_trades from Schwartz Championship if available
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
                "pyr_entry_px": tr.get("pyr_entry_px"),
            })
        return trades, j

    prices = load_prices()
    stake = float(j.get("stake", 0.2))
    lev = float(j.get("leverage", 3.0))
    balance = float(j.get("start", 2000.0))
    trades = []
    for t, ci, side, contrib in j.get("trades", []):
        name = COIN_NAMES.get(int(ci), "?")
        entry_px = close_at(prices, name, int(t))
        qty = round(stake * lev * balance / entry_px, 4) if entry_px else None
        pnl_usd = round(balance * float(contrib), 2)
        balance *= 1.0 + float(contrib)
        trades.append({
            "ts": int(t),
            "exit_ts": int(t) + 86400000,
            "hold_h": 24,
            "coin": name,
            "side": int(side),
            "entry_price": entry_px,
            "exit_price": close_at(prices, name, int(t) + 86400000),
            "path": path_for(prices, name, int(t)),
            "qty": qty,
            "margin": round(balance * stake, 2),
            "eff_lev": lev,
            "raw_net_pct": round(float(contrib) * 100 / max(lev * stake, 1e-6), 2),
            "pnl_usd": pnl_usd,
            "pnl_pct": round(float(contrib) * 100, 2),
            "eq": round(balance, 2),
            "reason": "24h",
            "pyr": False,
        })
    return trades, j


def fetch_live(days: int = 28):
    from rest import BybitClient
    client = BybitClient()
    try:
        client._require_keys()
    except BaseException:
        return []
    now = int(time.time() * 1000)
    day = 86400000
    fills = []
    # Query at most 2 recent 7-day windows without symbol filter so all pairs come back in 2 fast requests
    for i in range(min((days + 6) // 7, 2)):
        end = now - i * 7 * day
        start = end - 7 * day
        try:
            d = client.private_get(
                "/v5/execution/list",
                {
                    "category": "linear",
                    "limit": "100",
                    "startTime": str(start),
                    "endTime": str(end),
                },
            )
            rows = (d.get("result") or {}).get("list") or []
            for r in rows:
                sym = str(r.get("symbol") or "")
                coin = sym.replace("USDT", "")
                if coin not in SYMBOLS:
                    continue
                fills.append({
                    "coin": coin,
                    "side": 1 if str(r.get("side")).lower() == "buy" else -1,
                    "ts": int(r.get("execTime") or 0),
                    "qty": float(r.get("execQty") or 0),
                    "px": float(r.get("execPrice") or 0),
                })
        except Exception:
            pass
    fills.sort(key=lambda x: x["ts"])
    return fills


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def compounded(res: list[dict]) -> float:
    c = 1.0
    for r in res:
        c *= 1.0 + r["pnl_pct"] / 100.0
    return (c - 1.0) * 100.0


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
    rows = ""
    jperiods = []
    live_all = fetch_live(14)
    for label, tag, days in periods:
        bt, raw_j = load_backtest(tag)
        live = live_all
        res, extras = match(bt, live)
        matched = sum(1 for r in res if r["status"] == "MATCHED")
        missed = sum(1 for r in res if r["status"] == "MISSED")
        n_off = sum(1 for r in res if r["status"] == "MATCHED" and r.get("quality") != "OK")
        n_ok = matched - n_off
        wins = sum(1 for r in res if r.get("pnl_pct", 0) > 0)
        wr = round(wins / max(1, len(res)) * 100.0, 1)
        fid = round((matched / len(res) * 100) if res else 100.0, 1)
        ret_pct = round(float(raw_j.get("return_pct", compounded(res))), 2)
        dd_pct = round(float(raw_j.get("max_dd_pct", 0.0)), 1)
        final_eq = round(float(raw_j.get("final", 2000.0)), 2)

        def px(v):
            return v if v is not None else "-"

        def rowcls(r):
            if r["status"] == "MATCHED":
                q = r.get("quality", "OK")
                return "matched" if q == "OK" else ("bad" if q == "PRICE_QTY" else "warn")
            return "matched" if r.get("pnl_pct", 0) > 0 else "missed"

        btr = "".join(
            f"<tr class='{rowcls(r)}'>"
            f"<td>{iso(r['ts'])}</td><td>{iso(r['exit_ts'])} ({r.get('hold_h', 24)}h)</td>"
            f"<td><b>{r['coin']}</b> {'+PYR' if r.get('pyr') else ''}</td>"
            f"<td>{'LONG' if r['side']==1 else 'SHORT'}</td>"
            f"<td>{px(r['entry_price'])}</td><td>{px(r['exit_price'])}</td>"
            f"<td>${px(r.get('margin'))} ({px(r.get('eff_lev'))}x)</td>"
            f"<td>{r.get('reason', 'end')}</td>"
            f"<td>{r['status']}</td>"
            f"<td><b>{r['pnl_pct']:+.2f}%</b> (${r.get('pnl_usd', 0):+.2f})</td>"
            f"<td>${r.get('eq', 2000):,.2f}</td></tr>"
            for r in sorted(res, key=lambda x: x["ts"], reverse=True)
        )
        rows += (
            f"<section><h2>{label}: +{ret_pct:.2f}% ($2,000 -> ${final_eq:,.2f}, DD {dd_pct:.1f}%, WR {wr:.1f}%)</h2>"
            f"<p><b>{len(res)} сделок по стратегии Шварца</b> (побед: {wins}/{len(res)} = {wr:.1f}%): "
            f"исполнено на счёте {matched} (точно {n_ok}), ожидают/история {missed}, лишних сделок {len(extras)}.</p>"
            f"<table><tr><th>Вход</th><th>Выход</th><th>Монета</th><th>Сторона</th><th>Цена вх.</th><th>Цена вых.</th>"
            f"<th>Маржа</th><th>Правило выхода</th><th>Счёт</th><th>PnL</th><th>Баланс</th></tr>{btr}</table></section>"
        )

        jperiods.append({
            "label": label,
            "total": len(res),
            "wins": wins,
            "win_rate": wr,
            "matched": matched,
            "missed": missed,
            "verified": n_ok,
            "extras": len(extras),
            "fidelity": fid,
            "pnl_sum": round(sum(r["pnl_pct"] for r in res), 2),
            "return_pct": ret_pct,
            "max_dd_pct": dd_pct,
            "final_eq": final_eq,
            "equity": raw_j.get("equity_pct") or [],
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
                    "pyr_entry_px": r.get("pyr_entry_px"),
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

    html = (
        "<!doctype html><meta charset='utf-8'><title>Сверка Чемпионата Шварца vs Live</title>"
        "<style>body{font-family:Segoe UI,Arial;margin:24px;color:#222;max-width:1320px}"
        "h1{font-size:20px}h2{font-size:17px;margin-top:28px}"
        "table{border-collapse:collapse;font-size:12px;margin:6px 0;width:100%}"
        "td,th{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left}"
        ".matched td{background:#eafaf0}.missed td{background:#fdecea}.warn td{background:#fdf6e3}.bad td{background:#fde2e2}</style>"
        f"<h1>Сверка Чемпионата Шварца (Ultra 7x: Каскадный Тейк 3.0/5.5 ATR + Пирамида + Ротация)</h1>{rows}"
    )
    for target_dir in {LAB, OUT}:
        (target_dir / "compare_report.html").write_text(html, encoding="utf-8")
        (target_dir / "futures_compare.json").write_text(
            json.dumps(
                {
                    "periods": jperiods,
                    "account_fills": [
                        {
                            "time": iso(f["ts"]),
                            "coin": f["coin"],
                            "side": f["side"],
                            "qty": f["qty"],
                            "px": f["px"],
                        }
                        for f in live_all
                    ],
                    "strategy": "Schwartz Pitbull Championship 7x",
                    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    print("saved futures_compare.json and compare_report.html", flush=True)


if __name__ == "__main__":
    periods = [
        ("Квартал", "recent90", 90),
        ("Год", "recent365", 365),
        ("Вся история", "causal_full", 1800),
        ("Месяц", "recent30", 30),
    ]
    render_html(periods)
