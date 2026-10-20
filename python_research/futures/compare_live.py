"""Verification tool: backtest model trades vs LIVE account trades.

For each backtest signal (coin + side + time) it checks whether the account
actually executed a matching fill, and reports MATCHED / MISSED / EXTRA plus a
fidelity score. Run after the live bot has been trading to confirm it follows
the model exactly.
"""
import sys, time, json, os, bisect
from pathlib import Path
from datetime import datetime, timezone

LAB = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
COIN_NAMES = {1: "ETH", 2: "SOL", 5: "ADA", 8: "SUI"}   # 9-coin index -> name
SYMBOLS = {"ETH": "ETHUSDT", "SOL": "SOLUSDT", "ADA": "ADAUSDT", "SUI": "SUIUSDT"}

from futures_dataset import load_coin

def load_prices():
    out = {}
    for name, sym in SYMBOLS.items():
        bars, vols, fund = load_coin(sym)
        ts_list = sorted(bars)
        out[name] = (ts_list, [bars[t] for t in ts_list])
    return out

def close_at(prices, name, t):
    if name not in prices:
        return None
    ts_list, closes = prices[name]
    i = bisect.bisect_right(ts_list, t) - 1
    return round(closes[i], 4) if i >= 0 else None

def load_backtest(tag):
    j = json.loads((LAB / f"stable_{tag}.json").read_text(encoding="utf-8"))
    prices = load_prices()
    stake = float(j.get("stake", 0.2)); lev = float(j.get("leverage", 3.0))
    balance = float(j.get("start", 2000.0))
    trades = []
    for t, ci, side, contrib in j["trades"]:
        name = COIN_NAMES.get(int(ci), "?")
        entry_px = close_at(prices, name, int(t))
        qty = round(stake * lev * balance / entry_px, 4) if entry_px else None
        trades.append({
            "ts": int(t), "exit_ts": int(t) + 86400000, "coin": name, "side": int(side),
            "entry_price": entry_px,
            "exit_price": close_at(prices, name, int(t) + 86400000),
            "qty": qty,
            "pnl_pct": round(float(contrib) * 100, 2),
        })
        balance *= 1.0 + float(contrib)
    return trades

def fetch_live(days):
    from rest import BybitClient
    client = BybitClient()
    now = int(time.time() * 1000)
    day = 86400000
    fills = []
    for name, sym in SYMBOLS.items():
        for i in range((days + 6) // 7):
            end = now - i * 7 * day
            start = end - 7 * day
            try:
                d = client.private_get("/v5/execution/list",
                                       {"category": "linear", "symbol": sym, "limit": "100",
                                        "startTime": str(start), "endTime": str(end)})
                rows = (d.get("result") or {}).get("list") or []
                for r in rows:
                    fills.append({
                        "coin": name,
                        "side": 1 if str(r.get("side")).lower() == "buy" else -1,
                        "ts": int(r.get("execTime") or 0),
                        "qty": float(r.get("execQty") or 0),
                        "px": float(r.get("execPrice") or 0),
                    })
            except Exception:
                pass
    fills.sort(key=lambda x: x["ts"])
    return fills

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")

def match(backtest, live, window_h=12):
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
            results.append({"status": "MISSED", **bt})
        else:
            used[best] = True
            lf = live[best]
            price_diff = abs(lf["px"] - bt["entry_price"]) / bt["entry_price"] * 100 if bt.get("entry_price") else None
            qty_diff = abs(lf["qty"] - bt["qty"]) / bt["qty"] * 100 if bt.get("qty") else None
            issues = []
            if price_diff is not None and price_diff > 0.5:
                issues.append("PRICE")
            if qty_diff is not None and qty_diff > 15:
                issues.append("QTY")
            quality = "OK" if not issues else "_".join(issues)
            results.append({"status": "MATCHED", **bt,
                            "live_qty": round(lf["qty"], 4), "live_px": lf["px"],
                            "price_diff_pct": round(price_diff, 2) if price_diff is not None else None,
                            "qty_diff_pct": round(qty_diff, 2) if qty_diff is not None else None,
                            "quality": quality})
    extras = [lf for li, lf in enumerate(live) if not used[li]]
    return results, extras

def render_html(periods):
    rows = ""
    for label, tag, days in periods:
        bt = load_backtest(tag)
        live = fetch_live(days)
        res, extras = match(bt, live)
        matched = sum(1 for r in res if r["status"] == "MATCHED")
        missed = sum(1 for r in res if r["status"] == "MISSED")
        n_off = sum(1 for r in res if r["status"] == "MATCHED" and r.get("quality") != "OK")
        n_ok = matched - n_off
        fid = (matched / len(res) * 100) if res else 0.0
        pnl_sum = sum(r["pnl_pct"] for r in res)
        def px(v):
            return v if v is not None else "-"
        def rowcls(r):
            if r['status'] == 'MISSED':
                return 'missed'
            q = r.get('quality', 'OK')
            if q == 'OK':
                return 'matched'
            return 'bad' if q == 'PRICE_QTY' else 'warn'
        def qcell(r):
            q = px(r.get('qty'))
            if r['status'] == 'MATCHED' and r.get('live_qty') is not None:
                return f"{q} / live {r['live_qty']}"
            return q
        def dcell(r):
            if r['status'] != 'MATCHED':
                return "-"
            parts = []
            if r.get('price_diff_pct') is not None and r['price_diff_pct'] > 0.5:
                parts.append("цена " + str(r['price_diff_pct']) + "%")
            if r.get('qty_diff_pct') is not None and r['qty_diff_pct'] > 15:
                parts.append("объём " + str(r['qty_diff_pct']) + "%")
            return " / ".join(parts) if parts else "OK"
        btr = "".join(
            f"<tr class='{rowcls(r)}'><td>{iso(r['ts'])}</td><td>{iso(r['exit_ts'])}</td><td>{r['coin']}</td>"
            f"<td>{'LONG' if r['side']==1 else 'SHORT'}</td>"
            f"<td>{px(r['entry_price'])}</td><td>{px(r['exit_price'])}</td>"
            f"<td>{qcell(r)}</td>"
            f"<td>{r['status']}</td><td>{dcell(r)}</td><td>{r['pnl_pct']:+.2f}%</td></tr>"
            for r in sorted(res, key=lambda x: x["ts"], reverse=True))
        ltr = "".join(
            f"<tr><td>{iso(f['ts'])}</td><td>{f['coin']}</td>"
            f"<td>{'LONG' if f['side']==1 else 'SHORT'}</td><td>{f['qty']}</td><td>{f['px']}</td></tr>"
            for f in extras)
        rows += (f"<section><h2>{label}</h2>"
                 f"<p><b>{len(res)} сигналов модели</b>: ✅ {n_ok} совпало точно, ⚠️ {n_off} с расхождением, ❌ {missed} пропущено, "
                 f"⚠️ {len(extras)} лишних живых исполнений. "
                 f"<b style='color:{'green' if fid>=90 else 'orange' if fid>=50 else 'red'}'>Точность следования {fid:.0f}%</b>. "
                 f"PnL модели за период {pnl_sum:+.2f}%</p>"
                 f"<div class='cols'><div><h3>Сигналы модели (бэктест)</h3><table><tr><th>Вход</th><th>Выход</th><th>Монета</th><th>Сторона</th><th>Цена вх.</th><th>Цена вых.</th><th>Объём</th><th>Статус</th><th>PnL</th></tr>{btr}</table></div>"
                 f"<div><h3>Лишние живые сделки</h3><table><tr><th>Время</th><th>Монета</th><th>Сторона</th><th>Кол-во</th><th>Цена</th></tr>{ltr or '<tr><td colspan=5>нет</td></tr>'}</table></div></div></section>")
    html = ("<!doctype html><meta charset='utf-8'><title>Сверка бэктест vs live</title>"
            "<style>body{font-family:Segoe UI,Arial;margin:24px;color:#222;max-width:1200px}"
            "h1{font-size:20px}h2{font-size:17px;margin-top:28px}h3{font-size:14px}"
            "table{border-collapse:collapse;font-size:12px;margin:6px 0}td,th{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left}"
            ".cols{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}"
            ".matched td{background:#eafaf0}.missed td{background:#fdecea}.warn td{background:#fdf6e3}.bad td{background:#fde2e2}</style>"
            f"<h1>Сверка: бэктест модели vs живой счёт (4 монеты: ETH/SOL/ADA/SUI)</h1>{rows}")
    Path(OUT / "compare_report.html").write_text(html, encoding="utf-8")
    # structured JSON for the Laravel page
    jperiods = []
    for label, tag, days in periods:
        bt = load_backtest(tag)
        live = fetch_live(days)
        res, extras = match(bt, live)
        matched = sum(1 for r in res if r["status"] == "MATCHED")
        missed = sum(1 for r in res if r["status"] == "MISSED")
        fid = round((matched / len(res) * 100) if res else 0.0, 1)
        jperiods.append({
            "label": label, "total": len(res), "matched": matched, "missed": missed,
            "verified": matched - sum(1 for r in res if r["status"] == "MATCHED" and r.get("quality") != "OK"),
            "extras": len(extras), "fidelity": fid,
            "pnl_sum": round(sum(r["pnl_pct"] for r in res), 2),
            "trades": [{"time": iso(r["ts"]), "exit_time": iso(r["exit_ts"]), "coin": r["coin"], "side": r["side"],
                        "entry_price": r["entry_price"], "exit_price": r["exit_price"], "qty": r["qty"],
                        "quality": r.get("quality", "OK"), "price_diff_pct": r.get("price_diff_pct"), "qty_diff_pct": r.get("qty_diff_pct"),
                        "status": r["status"], "pnl_pct": r["pnl_pct"]}
                       for r in sorted(res, key=lambda x: x["ts"], reverse=True)],
        })
    (OUT / "futures_compare.json").write_text(json.dumps({"periods": jperiods, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    print("saved futures_compare.json")

if __name__ == "__main__":
    periods = [("Месяц", "recent30", 30), ("Квартал", "recent90", 90), ("Год", "recent365", 365)]
    render_html(periods)
