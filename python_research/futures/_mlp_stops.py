# -*- coding: utf-8 -*-
"""Walk-forward MLP: predict SL and TP in ATR from entry features only.
Labels use the future of past trades. Each test trade is predicted by a model
fit solely on trades that have already closed. Local research, nothing uploaded.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
import _profit_lock as L
import schwartz_production as sp

SLIP = L.SLIP
CACHE = L.CACHE


def load_ohlc():
    out = {}
    for sym in sp.ALTS_7:
        p = CACHE / (sym + ".json")
        out[sym] = {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    return out


def ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def features(s, btc, btc_map, i, side):
    atr = max(float(s["atr1h"][i]), 0.002)
    px = float(s["px"][i])
    ema = float(s["ema240"][i])
    dist = side * (px / ema - 1.0) / atr
    slope = side * (ema / float(s["ema240"][i - 24]) - 1.0) / atr
    ret = s["ret1"]
    r72 = float(np.mean(np.abs(ret[max(0, i - 71):i + 1])))
    vol_ratio = atr / (r72 + 1e-8)
    t = int(s["close_ts"][i])
    bi = btc_map.get(t)
    if bi is None or bi < 24:
        btc_agree = 0.0
    else:
        ba = max(float(btc["atr1h"][bi]), 0.002)
        btc_agree = side * (float(btc["px"][bi]) / float(btc["ema240"][bi]) - 1.0) / ba
    hour = datetime.fromtimestamp(t / 1000, timezone.utc).hour
    ang = 2 * np.pi * hour / 24.0
    w = s["px"][max(0, i - 48):i + 1]
    rng = (px - float(np.min(w))) / (float(np.max(w)) - float(np.min(w)) + 1e-8)
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    fi = int(np.searchsorted(f_ts, t, side="right")) - 1
    carry = 0.0
    if fi >= 90:
        hist = f_rate[fi - 89:fi + 1]
        fz = (float(f_rate[fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
        carry = -side * fz
    return np.array([dist, slope, vol_ratio, btc_agree, np.sin(ang), np.cos(ang), rng, carry], dtype=np.float64), atr


def path_of(s, ohlc, i, side, entry):
    px, cts = s["px"], s["close_ts"]
    rows = []
    last = min(len(px), i + 96)
    for j in range(i + 1, last):
        if int(cts[j]) != int(cts[j - 1]) + sp.HOUR:
            break
        open_ts = int(cts[j]) - sp.HOUR
        bar = ohlc.get(open_ts)
        if bar is None:
            o = h = l = c = float(px[j])
        else:
            o, h, l, c = [float(x) for x in bar]
        fav_px, adv_px = (h, l) if side == 1 else (l, h)
        fav = side * (fav_px / entry - 1.0)
        adv = side * (adv_px / entry - 1.0)
        cls = side * (c / entry - 1.0)
        rows.append((fav, adv, cls, o, int(cts[j]), j))
    return rows


def sl_fill(side, entry, move, open_px):
    return L.fill_at(side, entry, move, open_px, SLIP)


def tp_fill(side, entry, move, open_px):
    tp_px = entry * (1.0 + side * move)
    if side == 1:
        px = max(open_px, tp_px)
    else:
        px = min(open_px, tp_px)
    return px * (1.0 - side * SLIP)


def finish(s, side, entry, entry_ts, exit_px, exit_ts, raw_end, locked, lock_move, lock_ts, pyr, pyr_move, min_move, atr, reason, peak):
    raw = raw_end
    f_adj = L.fund_adj(s, side, entry_ts, exit_ts)
    net = raw + f_adj - sp.TAKER_FEE
    pyr_net = 0.50 * ((raw_end - pyr_move) + 0.5 * f_adj - sp.TAKER_FEE) if pyr else 0.0
    return {
        "exit_ts": int(exit_ts),
        "hold_h": int(round((exit_ts - entry_ts) / sp.HOUR)),
        "entry_px": round(entry, 6),
        "exit_px": round(float(exit_px), 6),
        "net": float(net),
        "pyr_net": float(pyr_net),
        "min_move": float(min_move),
        "atr": float(atr),
        "reason": reason,
        "pyr": bool(pyr),
        "peak_move": float(peak),
        "exit_move": float(raw),
        "missed_bars": 0,
        "locked": 0.0,
    }


def play(s, rows, side, entry, entry_ts, atr, sl_atr, tp_atr):
    sl = -abs(sl_atr) * atr
    tp = abs(tp_atr) * atr
    peak = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    if not rows:
        return None
    for fav, adv, cls, o, ts, j in rows:
        min_move = min(min_move, adv)
        if adv <= sl:
            px = sl_fill(side, entry, sl, o)
            raw = side * (px / entry - 1.0)
            return finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "sl", peak)
        if fav >= tp:
            px = tp_fill(side, entry, tp, o)
            raw = side * (px / entry - 1.0)
            peak = max(peak, fav)
            if (not pyr) and fav >= 2.0 * atr:
                pyr = True
                pyr_move = 2.0 * atr
            return finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "tp", peak)
        if (not pyr) and fav >= 2.0 * atr:
            pyr = True
            pyr_move = 2.0 * atr
        peak = max(peak, fav)
    fav, adv, cls, o, ts, j = rows[-1]
    c = float(s["px"][j])
    px = c * (1.0 - side * SLIP)
    raw = side * (px / entry - 1.0)
    return finish(s, side, entry, entry_ts, px, ts, raw, 0, 0, ts, pyr, pyr_move, min_move, atr, "end_96h", peak)


def fit_model(X, y):
    scaler = StandardScaler()
    xs = scaler.fit_transform(X)
    mlp = MLPRegressor(
        hidden_layer_sizes=(12,),
        activation="relu",
        alpha=0.05,
        learning_rate_init=0.002,
        max_iter=250,
        early_stopping=True,
        validation_fraction=0.2,
        n_iter_no_change=12,
        random_state=0,
    )
    mlp.fit(xs, y)
    return scaler, mlp


def predict(scaler, mlp, x):
    pred = mlp.predict(scaler.transform(x.reshape(1, -1)))[0]
    sl = float(np.clip(pred[0], 0.7, 3.5))
    tp = float(np.clip(pred[1], 1.2, 10.0))
    return sl, tp


def choose_scales(scaler, mlp, val_rows):
    best = (1.0, 1.0)
    best_score = -1e18
    for ks in (0.85, 1.0, 1.25):
        for kt in (0.55, 0.8, 1.0):
            score = 0.0
            for item in val_rows:
                sl, tp = predict(scaler, mlp, item["x"])
                ex = play(item["s"], item["rows"], item["side"], item["entry"], item["t"], item["atr"], sl * ks, tp * kt)
                if ex:
                    score += ex["net"] + ex["pyr_net"]
            if score > best_score:
                best_score = score
                best = (ks, kt)
    return best


def period_table(cands):
    bounds = [
        ("2024H2", ms(2024, 7, 1), ms(2025, 1, 1)),
        ("2025H1", ms(2025, 1, 1), ms(2025, 7, 1)),
        ("2025H2", ms(2025, 7, 1), ms(2026, 1, 1)),
        ("2026H1", ms(2026, 1, 1), ms(2026, 7, 22)),
        ("2026Q", ms(2026, 7, 22), ms(2026, 11, 2)),
    ]
    out = []
    for name, a, b in bounds:
        part = [c for c in cands if a <= c["t"] < b]
        if len(part) < 5:
            continue
        res = L.run_window(part, 0)
        out.append({"period": name, "n": res["n"], "wr": res["wr"], "ret": res["ret"], "dd": res["dd"], "final": res["final"]})
    return out


def main():
    entries, series = L.collect_entries()
    ohlc = load_ohlc()
    btc = series["BTCUSDT"]
    btc_map = {int(ts): i for i, ts in enumerate(btc["close_ts"])}
    prepared = []
    for e in entries:
        s = series[e["sym"]]
        x, atr = features(s, btc, btc_map, e["i"], e["side"])
        entry = float(s["px"][e["i"]]) * (1.0 + e["side"] * SLIP)
        rows = path_of(s, ohlc[e["sym"]], e["i"], e["side"], entry)
        if len(rows) < 4:
            continue
        mae = min(r[1] for r in rows)
        mfe = max(r[0] for r in rows)
        y_sl = float(np.clip(-mae / atr, 0.5, 5.0))
        y_tp = float(np.clip(mfe / atr, 0.5, 12.0))
        prepared.append({
            "e": e, "s": s, "x": x, "atr": atr, "entry": entry, "rows": rows,
            "t": e["t"], "exit_ready": rows[-1][4], "y": np.array([y_sl, y_tp]),
            "side": e["side"], "sym": e["sym"],
        })
    prepared.sort(key=lambda z: z["t"])
    print("prepared", len(prepared), flush=True)

    model = None
    scales = (1.0, 1.0)
    next_refit = 0
    train_closed = []
    mlp_cands = []
    fixed_cands = []
    used_model = 0
    pred_sl, pred_tp = [], []

    for item in prepared:
        # Refit on trades whose path has fully closed before this entry.
        ready = [z for z in train_closed if z["exit_ready"] <= item["t"]]
        if model is None or item["t"] >= next_refit:
            if len(ready) >= 80:
                cut = ready[int(len(ready) * 0.75)]["t"]
                fit_rows = [z for z in ready if z["t"] < cut]
                val_rows = [z for z in ready if z["t"] >= cut]
                if len(fit_rows) >= 60 and len(val_rows) >= 15:
                    X = np.vstack([z["x"] for z in fit_rows])
                    Y = np.vstack([z["y"] for z in fit_rows])
                    model = fit_model(X, Y)
                    scales = choose_scales(model[0], model[1], val_rows)
                    next_refit = item["t"] + 45 * sp.DAY
                    print("refit", sp.iso(item["t"]), "n", len(fit_rows), "scales", scales, flush=True)
        if model is not None:
            sl, tp = predict(model[0], model[1], item["x"])
            sl *= scales[0]
            tp *= scales[1]
            used_model += 1
            pred_sl.append(sl)
            pred_tp.append(tp)
            how = "mlp"
        else:
            sl, tp = 2.2, 6.0
            how = "fixed_warmup"
        ex = play(item["s"], item["rows"], item["side"], item["entry"], item["t"], item["atr"], sl, tp)
        fx = play(item["s"], item["rows"], item["side"], item["entry"], item["t"], item["atr"], 2.2, 6.0)
        if ex:
            mlp_cands.append({"t": item["t"], "sym": item["sym"], "side": item["side"], "model": how, "sl": round(sl, 2), "tp": round(tp, 2), **ex})
        if fx:
            fixed_cands.append({"t": item["t"], "sym": item["sym"], "side": item["side"], **fx})
        train_closed.append(item)

    # Production-timing baseline on the same entries.
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    base_cands = []
    by_key = {(p["t"], p["sym"]): p for p in prepared}
    for e in entries:
        if (e["t"], e["sym"]) not in by_key:
            continue
        ex = L.apply_exit(series[e["sym"]], ohlc[e["sym"]], e["i"], e["side"], close_pol, SLIP, False)
        if ex:
            base_cands.append({"t": e["t"], "sym": e["sym"], "side": e["side"], **ex})

    mlp_cands = L.with_hot(mlp_cands)
    fixed_cands = L.with_hot(fixed_cands)
    base_cands = L.with_hot(base_cands)
    report = {
        "model": "MLP 8 features -> SL ATR and TP ATR. Fit only on trades closed before the entry. Scale chosen on the last 25% of that train. 3bps slip, taker 0.11%, wick SL/TP, pessimistic if both hit the same hour.",
        "used_model": used_model,
        "warmup_fixed": len(mlp_cands) - used_model,
        "pred_sl_med": round(float(np.median(pred_sl)), 2) if pred_sl else None,
        "pred_tp_med": round(float(np.median(pred_tp)), 2) if pred_tp else None,
        "mlp": period_table(mlp_cands),
        "fixed_sl2.2_tp6": period_table(fixed_cands),
        "hourly_rules": period_table(base_cands),
    }
    out = Path(__file__).resolve().parent / "_mlp_stops.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
