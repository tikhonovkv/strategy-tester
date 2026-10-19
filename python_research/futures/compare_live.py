"""Verification tool: backtest model trades vs LIVE account trades.

For each backtest signal (coin + side + time) it checks whether the account
actually executed a matching fill, and reports MATCHED / MISSED / EXTRA plus a
fidelity score. Run after the live bot has been trading to confirm it follows
the model exactly.
"""
import sys, time, json
from pathlib import Path
from datetime import datetime, timezone

LAB = Path(r"C:\Users\123\Documents\deepseek-harness\bybit\lab")
COIN_NAMES = {1: "ETH", 2: "SOL", 5: "ADA", 8: "SUI"}   # 9-coin index -> name
SYMBOLS = {"ETH": "ETHUSDT", "SOL": "SOLUSDT", "ADA": "ADAUSDT", "SUI": "SUIUSDT"}

def load_backtest(tag):
    j = json.loads((LAB / f"stable_{tag}.json").read_text(encoding="utf-8"))
    trades = []
    for t, ci, side, contrib in j["trades"]:
        trades.append({"ts": int(t), "coin": COIN_NAMES.get(int(ci), "?"), "side": int(side), "pnl_pct": round(float(contrib) * 100, 2)})
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
            results.append({"status": "MATCHED", **bt, "live_qty": round(live[best]["qty"], 4), "live_px": live[best]["px"]})
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
        fid = (matched / len(res) * 100) if res else 0.0
        pnl_sum = sum(r["pnl_pct"] for r in res)
        btr = "".join(
            f"<tr class='{r['status'].lower()}'><td>{iso(r['ts'])}</td><td>{r['coin']}</td>"
            f"<td>{'LONG' if r['side']==1 else 'SHORT'}</td><td>{r['status']}</td>"
            f"<td>{r['pnl_pct']:+.2f}%</td></tr>"
            for r in sorted(res, key=lambda x: x["ts"]))
        ltr = "".join(
            f"<tr><td>{iso(f['ts'])}</td><td>{f['coin']}</td>"
            f"<td>{'LONG' if f['side']==1 else 'SHORT'}</td><td>{f['qty']}</td><td>{f['px']}</td></tr>"
            for f in extras)
        rows += (f"<section><h2>{label}</h2>"
                 f"<p><b>{len(res)} сигналов модели</b>: ✅ {matched} совпало, ❌ {missed} пропущено, "
                 f"⚠️ {len(extras)} лишних живых исполнений. "
                 f"<b style='color:{'green' if fid>=90 else 'orange' if fid>=50 else 'red'}'>Точность следования {fid:.0f}%</b>. "
                 f"PnL модели за период {pnl_sum:+.2f}%</p>"
                 f"<div class='cols'><div><h3>Сигналы модели (бэктест)</h3><table><tr><th>Время</th><th>Монета</th><th>Сторона</th><th>Статус</th><th>PnL</th></tr>{btr}</table></div>"
                 f"<div><h3>Лишние живые сделки</h3><table><tr><th>Время</th><th>Монета</th><th>Сторона</th><th>Кол-во</th><th>Цена</th></tr>{ltr or '<tr><td colspan=5>нет</td></tr>'}</table></div></div></section>")
    html = ("<!doctype html><meta charset='utf-8'><title>Сверка бэктест vs live</title>"
            "<style>body{font-family:Segoe UI,Arial;margin:24px;color:#222;max-width:1200px}"
            "h1{font-size:20px}h2{font-size:17px;margin-top:28px}h3{font-size:14px}"
            "table{border-collapse:collapse;font-size:12px;margin:6px 0}td,th{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left}"
            ".cols{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}"
            ".matched td{background:#eafaf0}.missed td{background:#fdecea}</style>"
            f"<h1>Сверка: бэктест модели vs живой счёт (4 монеты: ETH/SOL/ADA/SUI)</h1>{rows}")
    Path(LAB / "compare_report.html").write_text(html, encoding="utf-8")
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
            "extras": len(extras), "fidelity": fid,
            "pnl_sum": round(sum(r["pnl_pct"] for r in res), 2),
            "trades": [{"time": iso(r["ts"]), "coin": r["coin"], "side": r["side"],
                        "status": r["status"], "pnl_pct": r["pnl_pct"]}
                       for r in sorted(res, key=lambda x: x["ts"])],
        })
    (LAB / "futures_compare.json").write_text(json.dumps({"periods": jperiods}, ensure_ascii=False), encoding="utf-8")
    print("saved futures_compare.json")

if __name__ == "__main__":
    periods = [("Последние 7 дней", "recent7", 7), ("Последние 30 дней", "recent30", 30)]
    render_html(periods)
