# -*- coding: utf-8 -*-
"""Long-window regime study: flat market fades the signal. Local only."""
import json
from pathlib import Path
import numpy as np
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

def periods():
    return [
        ("2022", M.ms(2022, 1, 1), M.ms(2023, 1, 1)),
        ("2023", M.ms(2023, 1, 1), M.ms(2024, 1, 1)),
        ("2024", M.ms(2024, 1, 1), M.ms(2025, 1, 1)),
        ("2025", M.ms(2025, 1, 1), M.ms(2026, 1, 1)),
        ("2026H1", M.ms(2026, 1, 1), M.ms(2026, 7, 22)),
        ("2026Q", M.ms(2026, 7, 22), M.ms(2026, 11, 2)),
    ]

def summarize(rows, a, b):
    cands = L.with_hot([dict(r) for r in rows])
    res = sp.simulate_window([c for c in cands if a <= c["t"] < b])
    wins = [t for t in res["trades"] if t["pnl_usd"] > 0]
    wins.sort(key=lambda t: t["pnl_usd"], reverse=True)
    pos = sum(t["pnl_usd"] for t in wins) or 1.0
    top5 = sum(t["pnl_usd"] for t in wins[:5])
    return {"ret": res["ret"], "final": res["final"], "dd": res["dd"], "n": res["n"], "top5": round(top5 / pos, 3)}

def stamp(rows):
    out = {}
    start = M.ms(2022, 1, 1)
    end = M.ms(2026, 11, 2)
    out["full"] = summarize(rows, start, end)
    for name, a, b in periods():
        out[name] = summarize(rows, a, b)
    return out

def enrich(entries, series, ohlc, close_pol):
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    rows = []
    for e in entries:
        s = series[e["sym"]]
        i, side = e["i"], e["side"]
        if i < 96:
            continue
        ex = L.apply_exit(s, ohlc[e["sym"]], i, side, close_pol, L.SLIP, False)
        if ex is None:
            continue
        atr = max(float(s["atr1h"][i]), 0.002)
        d1 = side * (float(s["px"][i]) / float(s["ema24"][i]) - 1.0) / atr
        d3 = side * (float(s["ema72"][i]) / float(s["ema72"][i - 72]) - 1.0) / atr
        h4 = side * (float(s['px'][i]) / float(s['px'][i - 4]) - 1.0) / atr
        flat = d3 < 0.05 or h4 < 0
        rows.append({"t": e["t"], "sym": e["sym"], "side": side, "i": i, "flat": flat, "base": ex})
    return rows

def hybrid(rows, series, ohlc, close_pol, mode):
    out = []
    for r in rows:
        ex = dict(r["base"])
        side = r["side"]
        if r["flat"] and mode == "fade":
            flipped = L.apply_exit(series[r["sym"]], ohlc[r["sym"]], r["i"], -side, close_pol, L.SLIP, False)
            if flipped is None:
                continue
            ex = flipped
            side = -side
        elif r["flat"] and mode == "skip":
            continue
        out.append({"t": r["t"], "sym": r["sym"], "side": side, **ex})
    return out

def bnb_entries(series, btc_map):
    sym = "BNBUSDT"
    if sym not in series:
        series[sym] = sp.build_hourly_series(sym)
    s = series[sym]
    btc = series["BTCUSDT"]
    px, ema240, f_ts, f_rate, close_ts = s["px"], s["ema240"], s["f_ts"], s["f_rate"], s["close_ts"]
    n = len(px)
    ema48 = np.zeros(n)
    a48 = 2.0 / 49.0
    ema48[0] = px[0]
    for k in range(1, n):
        ema48[k] = a48 * px[k] + (1.0 - a48) * ema48[k - 1]
    found = []
    for i in range(240, n - 1):
        t_ms = int(close_ts[i])
        bi = btc_map.get(t_ms)
        if bi is None or bi < 240:
            continue
        cur_px, prev_px = float(px[i]), float(px[i - 1])
        es = float(ema240[i])
        slope_s = es / float(ema240[i - 24]) - 1.0
        side = 0
        if cur_px > es and slope_s > 0 and ema48[i] > es and prev_px <= ema48[i - 1] and cur_px > ema48[i]:
            side = 1
        elif cur_px < es and slope_s < 0 and ema48[i] < es and prev_px >= ema48[i - 1] and cur_px < ema48[i]:
            side = -1
        if side == 0:
            continue
        fi = int(np.searchsorted(f_ts, t_ms, side="right")) - 1
        if fi < 90:
            continue
        hist = f_rate[fi - 89:fi + 1]
        carry = -side * (float(f_rate[fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
        btc_ok = (side == 1 and float(btc["px"][bi]) > float(btc["ema240"][bi])) or (side == -1 and float(btc["px"][bi]) < float(btc["ema240"][bi]))
        if not btc_ok or carry < 0:
            continue
        found.append({"t": t_ms, "sym": sym, "side": side, "i": i})
    return found

def solo(rows):
    cands = []
    for r in rows:
        c = dict(r)
        c.update({"hot_n": 4, "hot_avg": 0.02, "c_lstreak": 0, "ci": 0, "coin": "BNB", "path": []})
        cands.append(c)
    start, end = M.ms(2022, 1, 1), M.ms(2026, 11, 2)
    res = sp.simulate_window([c for c in cands if start <= c["t"] < end])
    return {"n": res["n"], "ret": res["ret"], "final": res["final"], "dd": res["dd"]}

def main():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    if "BNBUSDT" not in ohlc:
        p = L.CACHE / "BNBUSDT.json"
        ohlc["BNBUSDT"] = {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()} if p.exists() else {}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    base_rows = enrich(entries, series, ohlc, close_pol)
    flat_n = sum(1 for r in base_rows if r["flat"])
    print("trades", len(base_rows), "flat", flat_n, flush=True)
    books = {
        "rules": [{"t": r["t"], "sym": r["sym"], "side": r["side"], **r["base"]} for r in base_rows],
        "skip_flat": hybrid(base_rows, series, ohlc, close_pol, "skip"),
        "fade_flat": hybrid(base_rows, series, ohlc, close_pol, "fade"),
    }
    report = {"flat_n": flat_n, "trades": len(base_rows)}
    for name, rows in books.items():
        report[name] = stamp(rows)
        print(name, "full", report[name]["full"], flush=True)
    btc_map = {int(ts): i for i, ts in enumerate(series["BTCUSDT"]["close_ts"])}
    try:
        bnb = bnb_entries(series, btc_map)
        print("bnb signals", len(bnb), flush=True)
        if ohlc.get("BNBUSDT"):
            bnb_rows = enrich(bnb, series, ohlc, close_pol)
            report["bnb_signals"] = len(bnb)
            report["bnb_rules"] = solo([{"t": r["t"], "sym": r["sym"], "side": r["side"], **r["base"]} for r in bnb_rows])
            print("bnb", report["bnb_rules"], flush=True)
        else:
            ohlc["BNBUSDT"] = {}
        bnb_rows = enrich(bnb, series, ohlc, close_pol)
        report["bnb_signals"] = len(bnb)
        report["bnb_rules"] = solo([{"t": r["t"], "sym": r["sym"], "side": r["side"], **r["base"]} for r in bnb_rows])
        report["bnb_fade"] = solo(hybrid(bnb_rows, series, ohlc, close_pol, "fade"))
        print("bnb", report["bnb_rules"], report["bnb_fade"], flush=True)
    except Exception as exc:
        report["bnb_error"] = str(exc)
        print("bnb error", exc, flush=True)
    Path(__file__).resolve().parent.joinpath("_chop_fade.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("wrote", flush=True)

if __name__ == "__main__":
    main()
