# -*- coding: utf-8 -*-
"""Entry precursors for the Schwartz book. Causal features on the signal bar.
Flat $100 risk and the 7x book share one fee and one slippage. Local only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

import schwartz_production as sp

SLIP = 0.0003
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
CACHE = sp.LAB / "_ohlc_cache"
STATE = sp.LAB / "_entry_loop_state.json"
OUT = sp.LAB / "_entry_precursor.json"


def stamp(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


WINDOWS = [
    ("2022", stamp(2022, 1, 1), stamp(2023, 1, 1)),
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, END),
    ("full", stamp(2022, 1, 1), END),
]
FEATS = ["vol_surge", "body", "close_loc", "thrust", "rs4", "rs12", "rs24", "rank", "coil", "carry_d"]


def load_ohlc():
    out = {}
    for sym in sp.ALTS_7:
        path = CACHE / f"{sym}.json"
        if not path.exists():
            out[sym] = {}
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[sym] = {int(k): v for k, v in raw.items()}
    return out


def bar_at(ohlc, sym, close_ts):
    return ohlc.get(sym, {}).get(int(close_ts) - sp.HOUR)


def ret_n(s, i, n):
    if i < n:
        return 0.0
    prev = float(s["px"][i - n])
    if prev <= 0:
        return 0.0
    return float(s["px"][i]) / prev - 1.0


def carry(s, i, side):
    t = int(s["close_ts"][i])
    fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
    if fi < 90:
        return 0.0
    hist = s["f_rate"][fi - 89:fi + 1]
    z = (float(s["f_rate"][fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
    return float(-side * z)


def index_of(series, sym, ts):
    s = series[sym]
    j = int(np.searchsorted(s["close_ts"], ts))
    if j >= len(s["close_ts"]) or int(s["close_ts"][j]) != int(ts):
        return None
    return j


def features(e, series, ohlc, btc_i):
    s = series[e["sym"]]
    i = index_of(series, e["sym"], e["t"])
    side = int(e["side"])
    if i is None or i < 30 or btc_i is None:
        return None
    atr = max(float(s["atr1h"][i]), 0.002)
    vol = s["vol"]
    base = float(np.median(vol[i - 24:i])) if i >= 24 else float(vol[i])
    surge = float(vol[i]) / max(base, 1e-8)
    bar = bar_at(ohlc, e["sym"], e["t"])
    body = close_loc = thrust = coil = None
    if bar is not None:
        o, h, l, c = [float(x) for x in bar[:4]]
        span = max(h - l, 1e-12)
        body = abs(c - o) / span
        close_loc = (c - l) / span if side == 1 else (h - c) / span
        prev = float(s["px"][i - 1])
        thrust = abs(c - prev) / max(c, 1e-12) / atr
        highs, lows = [], []
        ok = True
        for k in range(i - 12, i):
            bk = bar_at(ohlc, e["sym"], int(s["close_ts"][k]))
            if bk is None:
                ok = False
                break
            highs.append(float(bk[1]))
            lows.append(float(bk[2]))
        if ok and highs:
            coil = (max(highs) - min(lows)) / max(12.0 * atr * float(s["px"][i]), 1e-8)
    btc = series["BTCUSDT"]
    rs4 = side * (ret_n(s, i, 4) - ret_n(btc, btc_i, 4))
    rs12 = side * (ret_n(s, i, 12) - ret_n(btc, btc_i, 12))
    rs24 = side * (ret_n(s, i, 24) - ret_n(btc, btc_i, 24))
    ranks = []
    for sym in sp.ALTS_7:
        j = index_of(series, sym, e["t"])
        if j is None:
            continue
        ranks.append((sym, side * ret_n(series[sym], j, 6)))
    ranks.sort(key=lambda x: x[1], reverse=True)
    rank = next((n + 1 for n, (sym, _r) in enumerate(ranks) if sym == e["sym"]), 7)
    cd = carry(s, i, side) - carry(s, i - 24, side)
    return {
        "i": i,
        "vol_surge": round(surge, 4),
        "body": None if body is None else round(body, 4),
        "close_loc": None if close_loc is None else round(close_loc, 4),
        "thrust": None if thrust is None else round(thrust, 4),
        "rs4": round(rs4, 5),
        "rs12": round(rs12, 5),
        "rs24": round(rs24, 5),
        "rank": int(rank),
        "coil": None if coil is None else round(coil, 4),
        "carry_d": round(cd, 4),
        "score": round(surge + (body or 0) + max(rs12, 0) * 20 + (0.5 if (close_loc or 0) >= 0.7 else 0), 4),
    }


def with_cost(e):
    out = dict(e)
    out["net"] = float(e["net"]) - 2.0 * SLIP
    out["pyr_net"] = float(e.get("pyr_net") or 0.0) - SLIP
    return out


def year_key(ts):
    d = datetime.fromtimestamp(ts / 1000, timezone.utc)
    if d.year == 2026 and ts >= sp.Q_CUT:
        return "2026Q"
    if d.year == 2026:
        return "2026H1"
    return str(d.year)


def flat_table(rows):
    by = {}
    for r in rows:
        by.setdefault(year_key(r["t"]), []).append(r)
    out = {}
    for key, part in by.items():
        pnls = []
        for r in part:
            stop = max(2.2 * float(r["atr"]), 0.008)
            pnls.append(100.0 * float(r["net"]) / stop)
        ordered = sorted(pnls, reverse=True)
        rest = ordered[5:] if len(ordered) > 5 else ordered
        out[key] = {
            "n": len(pnls),
            "mean": round(float(np.mean(pnls)), 2) if pnls else None,
            "ex5": round(float(np.mean(rest)), 2) if rest else None,
        }
    return out


def portfolio(rows):
    out = {}
    curves = {}
    for label, a, b in WINDOWS:
        part = [r for r in rows if a <= r["t"] < b]
        res = sp.simulate_window(part)
        wins = sorted((t["pnl_usd"] for t in res["trades"] if t["pnl_usd"] > 0), reverse=True)
        win_sum = sum(wins) or 1.0
        dd = float(res["dd"])
        ret = float(res["ret"])
        out[label] = {
            "n": res["n"],
            "ret": ret,
            "dd": dd,
            "final": res["final"],
            "calmar": round(ret / dd, 2) if dd else None,
            "top5": round(sum(wins[:5]) / win_sum, 3),
        }
        curves[label] = res["curve"]
    return out, curves


def passes(feat, name):
    if feat is None:
        return False
    v = feat["vol_surge"]
    body, loc, thrust = feat["body"], feat["close_loc"], feat["thrust"]
    if name == "all":
        return True
    if name == "vol15":
        return v >= 1.5
    if name == "vol20":
        return v >= 2.0
    if name == "body65":
        return body is not None and body >= 0.65
    if name == "close85":
        return loc is not None and loc >= 0.85
    if name == "thrust1":
        return thrust is not None and thrust >= 1.0
    if name == "rs12":
        return feat["rs12"] > 0
    if name == "leader2":
        return feat["rank"] <= 2
    if name == "coil08":
        return feat["coil"] is not None and feat["coil"] <= 0.8
    if name == "ignition":
        return v >= 1.5 and body is not None and body >= 0.5 and feat["rs12"] > 0
    if name == "edge_bar":
        return v >= 1.5 and loc is not None and loc >= 0.7
    return False


def vector(feat):
    return [0.0 if feat[k] is None else float(feat[k]) for k in FEATS]


def model_keep(rows):
    """Walk-forward keep-mask. Threshold is chosen on the previous year and may keep everyone."""
    rows = sorted(rows, key=lambda r: r["t"])
    keep = {id(r): True for r in rows}
    folds = [
        (stamp(2023, 1, 1), stamp(2022, 1, 1), stamp(2023, 1, 1)),
        (stamp(2024, 1, 1), stamp(2022, 1, 1), stamp(2024, 1, 1)),
        (stamp(2025, 1, 1), stamp(2022, 1, 1), stamp(2025, 1, 1)),
        (stamp(2026, 1, 1), stamp(2022, 1, 1), stamp(2026, 1, 1)),
        (sp.Q_CUT, stamp(2022, 1, 1), sp.Q_CUT),
    ]
    # The third value is unused; the test slice is [fold_start, next).
    cuts = [f[0] for f in folds] + [END]
    chosen = {}
    for n, (test_from, _train_from, _val) in enumerate(folds):
        test_to = cuts[n + 1]
        train = [r for r in rows if r["t"] + 96 * sp.HOUR <= test_from]
        test = [r for r in rows if test_from <= r["t"] < test_to]
        if len(train) < 80 or len({1 if r["net"] > 0 else 0 for r in train}) < 2:
            chosen[year_key(test_from)] = {"thr": 0.0, "reason": "too_few"}
            continue
        y = np.array([1 if r["net"] > 0 else 0 for r in train])
        w = np.array([
            min(20.0, 1.0 + (4.0 if r["net"] > 0 else 2.0) * abs(r["net"]) / max(r["atr"], 0.002))
            for r in train
        ])
        clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.06, max_iter=120, random_state=0)
        clf.fit(np.vstack([vector(r["feat"]) for r in train]), y, sample_weight=w)
        # Choose the threshold on the last 120 training rows, never on the test year.
        val = train[-120:]
        best_thr, best_score = 0.0, -1e18
        p_val = clf.predict_proba(np.vstack([vector(r["feat"]) for r in val]))[:, 1]
        for thr in (0.0, 0.45, 0.55, 0.65):
            kept = [r for r, p in zip(val, p_val) if p >= thr]
            table = flat_table(kept if kept else val[:1])
            score = np.mean([v["ex5"] for v in table.values() if v["ex5"] is not None])
            if score > best_score:
                best_thr, best_score = thr, score
        chosen[str(test_from)] = {"thr": best_thr, "val_ex5": round(float(best_score), 3)}
        if not test:
            continue
        p_test = clf.predict_proba(np.vstack([vector(r["feat"]) for r in test]))[:, 1]
        for r, p in zip(test, p_test):
            keep[id(r)] = bool(p >= best_thr)
    return keep, chosen


def evict_book(rows, series):
    """Same exits as the rules, except a strong new signal may replace a dead slot."""
    idx = {sym: {int(t): i for i, t in enumerate(s["close_ts"])} for sym, s in series.items()}
    by_t = {}
    times = set()
    for e in rows:
        by_t.setdefault(e["t"], []).append(e)
        times.add(e["t"])
        times.add(e["exit_ts"])
    free, reserved = 2000.0, 0.0
    open_pos = []
    trades = []
    peak, max_dd = free, 0.0
    loss_streak = win_streak = 0
    curve = []
    for t in sorted(times):
        still = []
        for p in open_pos:
            if p["exit_ts"] <= t:
                e = p["e"]
                tot = e["net"] + p["pyr_scale"] * e["pyr_net"]
                pnl = max(-p["margin"], p["margin"] * 7.0 * tot)
                free += p["margin"] + pnl
                reserved -= p["margin"]
                eq = free + reserved
                loss_streak, win_streak = (loss_streak + 1, 0) if pnl < 0 else (0, win_streak + 1)
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak if peak else 0)
                trades.append(pnl)
                curve.append((int(t), round(eq, 2)))
            else:
                still.append(p)
        open_pos = still
        eq = free + reserved
        if eq <= 0:
            break
        batch = sorted(by_t.get(t, []), key=lambda e: e["feat"]["score"], reverse=True)
        if not batch:
            continue
        # A champion may replace a slot that has not moved after two hours.
        if len(open_pos) >= 2 and batch[0]["feat"]["score"] >= 3.0:
            weakest = None
            for p in open_pos:
                held_h = (t - p["e"]["t"]) / sp.HOUR
                j = idx[p["e"]["sym"]].get(int(t))
                if held_h < 2 or j is None:
                    continue
                entry = float(p["e"]["entry_px"])
                move = p["e"]["side"] * (float(series[p["e"]["sym"]]["px"][j]) / entry - 1.0)
                atr = max(float(p["e"]["atr"]), 0.002)
                if move < 0.3 * atr and (weakest is None or move < weakest[0]):
                    weakest = (move, p)
            if weakest is not None:
                p = weakest[1]
                j = idx[p["e"]["sym"]][int(t)]
                raw = p["e"]["side"] * (float(series[p["e"]["sym"]]["px"][j]) / float(p["e"]["entry_px"]) - 1.0)
                early = raw - sp.TAKER_FEE - 2.0 * SLIP
                pnl = max(-p["margin"], p["margin"] * 7.0 * early)
                free += p["margin"] + pnl
                reserved -= p["margin"]
                loss_streak, win_streak = (loss_streak + 1, 0) if pnl < 0 else (0, win_streak + 1)
                trades.append(pnl)
                curve.append((int(t), round(free + reserved, 2)))
                open_pos = [x for x in open_pos if x is not p]
                eq = free + reserved
        if len(open_pos) >= 2:
            continue
        busy = {p["e"]["sym"] for p in open_pos}
        for e in batch:
            if len(open_pos) >= 2 or e["sym"] in busy:
                continue
            if e["sym"] not in sp.CORE_4 and (e["hot_n"] < 4 or e["hot_avg"] < 0.015):
                continue
            mult = sp.size_multiplier(loss_streak, win_streak, e["c_lstreak"], e["hot_avg"], 1.15)
            stop_dist = max(1.6 * e["atr"], 0.008)
            raw_margin = min((eq * 0.075 * mult) / stop_dist / 7.0, eq * 0.60, free * 0.90)
            if raw_margin < 15:
                continue
            qty = sp.fit_lot_qty(e["sym"], (raw_margin * 7.0) / max(e["entry_px"], 1e-8))
            if qty <= 0:
                continue
            margin = (qty * e["entry_px"]) / 7.0
            if margin < 15 or margin > free:
                continue
            free -= margin
            reserved += margin
            rem = max(0.0, free)
            need = 0.50 * margin
            pyr_scale = min(1.0, (rem * 0.90) / max(need, 1e-8)) if e["pyr_net"] and rem >= 10 else 0.0
            open_pos.append({"e": e, "exit_ts": e["exit_ts"], "margin": margin, "pyr_scale": pyr_scale})
            busy.add(e["sym"])
    final = free + reserved
    wins = sorted((x for x in trades if x > 0), reverse=True)
    dd = max_dd * 100.0
    ret = (final / 2000.0 - 1.0) * 100.0
    return {
        "n": len(trades),
        "ret": round(ret, 2),
        "dd": round(dd, 1),
        "final": round(final, 2),
        "calmar": round(ret / dd, 2) if dd else None,
        "top5": round(sum(wins[:5]) / (sum(wins) or 1.0), 3),
        "curve": curve,
    }


def separation(rows):
    win = [r for r in rows if r["net"] > 0]
    lose = [r for r in rows if r["net"] <= 0]
    out = {}
    for key in FEATS:
        def med(part, key=key):
            vals = [r["feat"][key] for r in part if r["feat"][key] is not None]
            return None if not vals else round(float(np.median(vals)), 4)
        out[key] = {"win": med(win), "lose": med(lose)}
    return out


def summarize(name, rows, port=None, curves=None, extra=None):
    if port is None:
        port, curves = portfolio(rows)
    item = {"flat": flat_table(rows), "portfolio": port, "n": len(rows)}
    if extra:
        item["extra"] = extra
    full = port["full"]
    print(name, "n", len(rows), "full", full["ret"], full["dd"], full["final"], "top5", full["top5"], flush=True)
    return item, curves


def main():
    cands, series = sp.build_tranche_candidates()
    ohlc = load_ohlc()
    btc_map = {int(t): i for i, t in enumerate(series["BTCUSDT"]["close_ts"])}
    rows = []
    for e in cands:
        feat = features(e, series, ohlc, btc_map.get(int(e["t"])))
        if feat is None:
            continue
        row = with_cost(e)
        row["feat"] = feat
        row["i"] = feat["i"]
        rows.append(row)
    names = ["all", "vol15", "vol20", "body65", "close85", "thrust1", "rs12", "leader2", "coil08", "ignition", "edge_bar"]
    report = {"separation": separation(rows)}
    curves = {}
    for name in names:
        kept = [r for r in rows if passes(r["feat"], name)]
        item, cur = summarize(name, kept)
        report[name] = item
        if name in ("all", "vol15", "ignition", "edge_bar", "leader2"):
            curves[name] = cur["full"]
    mask, chosen = model_keep(rows)
    model_rows = [r for r in rows if mask[id(r)]]
    item, cur = summarize("hgb", model_rows, extra={"thresholds": chosen})
    report["hgb"] = item
    curves["hgb"] = cur["full"]
    ev = {}
    ev_curves = {}
    for label, a, b in WINDOWS:
        part = [r for r in rows if a <= r["t"] < b]
        res = evict_book(part, series)
        ev_curves[label] = res.pop("curve")
        ev[label] = res
        if label == "full":
            print("evict", res, flush=True)
    report["evict"] = {"flat": flat_table(rows), "portfolio": ev, "n": len(rows)}
    curves["evict"] = ev_curves["full"]
    OUT.write_text(json.dumps({"report": report, "curves": curves}, ensure_ascii=False), encoding="utf-8")
    state = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "baseline_full": report["all"]["portfolio"]["full"],
        "tried": sorted(report.keys()),
        "next": "Try a higher ignition bar only when the flat ex5 mean is positive in 2024, and do not repeat vol15 or edge_bar if they lose 2025 or the quarter.",
        "rule": "Same 0.11% fee and 3 bps slippage on every row. Live bot stays unchanged.",
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
