# -*- coding: utf-8 -*-
"""Hourly remaining-upside network. Local only, no upload.
At each closed hour the MLP predicts how much favorable close-move is left
over the next 24h. If the forecast is small and the trade is already in profit,
it locks. Safety stops stay: fast bail, -2.2 ATR, breakeven floor, 10d EMA, 96h.
"""
import json
from pathlib import Path
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
import _mlp_stops as M
import _profit_lock as L
import schwartz_production as sp

SLIP = L.SLIP


def bar_x(s, i, j, side, atr, move, peak, base):
    held = (j - i) / 96.0
    dist = side * (float(s["px"][j]) / float(s["ema240"][j]) - 1.0) / atr
    return np.array([held, move / atr, peak / atr, (peak - move) / atr, dist, base[2], base[3], base[7]], dtype=np.float64)


def samples_of(item):
    rows = item["rows"]
    xs, ys = [], []
    peak = 0.0
    for k, (fav, adv, cls, o, ts, j) in enumerate(rows):
        peak = max(peak, cls)
        fut = [r[2] for r in rows[k:k + 24]]
        rem = max(0.0, max(fut) - cls) / item["atr"] if fut else 0.0
        xs.append(bar_x(item["s"], item["i"], j, item["side"], item["atr"], cls, peak, item["base"]))
        ys.append(float(np.clip(rem, 0.0, 8.0)))
    return xs, ys


def fit_model(X, y):
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    mlp = MLPRegressor(
        hidden_layer_sizes=(16,), activation="relu", alpha=0.08,
        learning_rate_init=0.002, max_iter=180, early_stopping=True,
        validation_fraction=0.15, n_iter_no_change=10, random_state=0,
    )
    mlp.fit(xs, y)
    return scaler, mlp


def rem_hat(model, x):
    scaler, mlp = model
    return float(np.clip(mlp.predict(scaler.transform(x.reshape(1, -1)))[0], 0.0, 8.0))


def play(item, model, rem_cut, min_profit):
    s, side, atr = item["s"], item["side"], item["atr"]
    entry, entry_ts = item["entry"], item["t"]
    rows = item["rows"]
    peak = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    reason = "end_96h"
    exit_px = entry
    exit_ts = entry_ts
    j_exit = item["i"]
    locked = False
    for fav, adv, cls, o, ts, j in rows:
        min_move = min(min_move, adv)
        h = j - item["i"]
        if adv <= -2.2 * atr:
            exit_px = L.fill_at(side, entry, -2.2 * atr, o, SLIP)
            raw = side * (exit_px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, exit_px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "hard_stop", peak), locked
        if h <= 4 and cls <= -0.8 * atr:
            exit_px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (exit_px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, exit_px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "fast_bail", peak), locked
        if model is not None and cls >= min_profit * atr:
            x = bar_x(s, item["i"], j, side, atr, cls, max(peak, cls), item["base"])
            if rem_hat(model, x) <= rem_cut:
                exit_px = float(s["px"][j]) * (1.0 - side * SLIP)
                raw = side * (exit_px / entry - 1.0)
                return M.finish(s, side, entry, entry_ts, exit_px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "lock", max(peak, cls)), True
        be = 1.15 * atr if pyr else 0.4 * atr
        if peak >= 3.0 * atr and cls <= be:
            exit_px = float(s["px"][j]) * (1.0 - side * SLIP)
            raw = side * (exit_px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, exit_px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "breakeven", peak), locked
        if (not pyr) and cls >= 2.0 * atr:
            pyr = True
            pyr_move = 2.0 * atr
        peak = max(peak, cls)
        c = float(s["px"][j])
        if (side == 1 and c < float(s["ema240"][j])) or (side == -1 and c > float(s["ema240"][j])):
            exit_px = c * (1.0 - side * SLIP)
            raw = side * (exit_px / entry - 1.0)
            return M.finish(s, side, entry, entry_ts, exit_px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "ema10_exit", peak), locked
        exit_px = c * (1.0 - side * SLIP)
        exit_ts = ts
        j_exit = j
        reason = "end_96h" if h >= 95 else "open"
    if reason == "open":
        reason = "end_96h"
    raw = side * (exit_px / entry - 1.0)
    return M.finish(s, side, entry, entry_ts, exit_px, exit_ts, raw, 0, 0, exit_ts, pyr, pyr_move, min_move, atr, reason, peak), locked


def choose(model, val_items):
    best, best_s = (0.45, 1.5), -1e18
    for cut in (0.3, 0.5, 0.8):
        for profit in (1.0, 1.5, 2.5):
            score = 0.0
            for item in val_items:
                ex, _ = play(item, model, cut, profit)
                if ex:
                    score += ex["net"] + ex["pyr_net"]
            if score > best_s:
                best_s, best = score, (cut, profit)
    return best


def main():
    entries, series = L.collect_entries()
    ohlc = M.load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    prepared = []
    for e in entries:
        s = series[e["sym"]]
        base, atr = M.features(s, btc, btc_map, e["i"], e["side"])
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * SLIP)
        rows = M.path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        hourly = L.apply_exit(s, ohlc[e["sym"]], e["i"], e["side"], close_pol, SLIP, False)
        if hourly is None:
            continue
        prepared.append({
            "s": s, "i": e["i"], "t": e["t"], "sym": e["sym"], "side": e["side"],
            "atr": atr, "entry": entry, "rows": rows, "base": base,
            "exit_ready": rows[-1][4], "hourly": hourly,
        })
    prepared.sort(key=lambda z: z["t"])
    print("prepared", len(prepared), flush=True)
    model = None
    params = (0.45, 1.5)
    next_refit = 0
    closed = []
    dyn, base = [], []
    locks = 0
    used = 0
    for item in prepared:
        ready = [z for z in closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 90:
                cut_i = int(len(ready) * 0.8)
                fit_rows, val_rows = ready[:cut_i], ready[cut_i:]
                xs, ys = [], []
                for z in fit_rows:
                    a, b = samples_of(z)
                    xs.extend(a)
                    ys.extend(b)
                if len(xs) >= 400:
                    model = fit_model(np.vstack(xs), np.array(ys))
                    params = choose(model, val_rows[-40:])
                    next_refit = item["t"] + 60 * sp.DAY
                    print("refit", sp.iso(item["t"]), "bars", len(xs), "params", params, flush=True)
        if model is not None:
            ex, did = play(item, model, params[0], params[1])
            used += 1
            locks += int(did)
        else:
            ex, did = dict(item["hourly"]), False
        if ex:
            dyn.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **ex})
        base.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **item["hourly"]})
        closed.append(item)
    dyn = L.with_hot(dyn)
    base = L.with_hot(base)
    d_tbl, b_tbl = M.period_table(dyn), M.period_table(base)
    by_b = {r["period"]: r for r in b_tbl}
    wins = sum(1 for r in d_tbl if r["period"] in by_b and r["ret"] > by_b[r["period"]]["ret"])
    q_dyn = next((r["ret"] for r in d_tbl if r["period"] == "2026Q"), None)
    q_base = (by_b.get("2026Q") or {}).get("ret")
    report = {
        "model": "MLP predicts remaining upside in ATR over the next 24 hourly closes. Lock if forecast is small and profit is already large. Walk-forward, past closed trades only.",
        "used_model": used,
        "locks": locks,
        "dynamic": d_tbl,
        "hourly_rules": b_tbl,
        "periods_won": wins,
        "q_not_worse": q_dyn is not None and q_base is not None and q_dyn >= q_base,
        "change_bot": False,
    }
    report["change_bot"] = wins >= 4 and report["q_not_worse"]
    out = Path(__file__).resolve().parent / "_dyn_lock.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
