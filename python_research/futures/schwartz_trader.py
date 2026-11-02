# -*- coding: utf-8 -*-
"""Dense Schwartz trader: a small net learns which entry to take.

Schwartz traffic light stays (only with the 10d EMA). The net is not a coin ban.
It sees every 8h momentum bar plus classic 48h reclaim, including BTC and other
liquid majors, and is trained only on trades that have already closed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from sklearn.neural_network import MLPRegressor

from causal import DAY, HOUR, LAB
from schwartz_pitbull import build_hourly_series
from schwartz_production import (
    SHORT_NAME,
    TAKER_FEE,
    build_tranche_candidates,
    fit_lot_qty,
    simulate_window,
)
import schwartz_production as sp
from test_schwartz_hybrid import Q_CUT, Y2024, Y2025, Y2026

MAJORS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
    "LTCUSDT", "BCHUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT",
    "UNIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT",
    "TRXUSDT", "XLMUSDT", "ETCUSDT", "AAVEUSDT", "TIAUSDT",
]
EXTRA_LOT = {
    "LTCUSDT": 0.1, "BCHUSDT": 0.01, "DOTUSDT": 0.1, "NEARUSDT": 0.1,
    "ATOMUSDT": 0.1, "UNIUSDT": 0.1, "APTUSDT": 0.01, "ARBUSDT": 0.1,
    "OPUSDT": 0.1, "INJUSDT": 0.1, "FILUSDT": 0.1, "TRXUSDT": 1.0,
    "XLMUSDT": 1.0, "ETCUSDT": 0.01, "AAVEUSDT": 0.01, "TIAUSDT": 0.1,
}
N_NUM = 16
HIDDEN = (16,)
WEIGHTS = LAB / "schwartz_trader.npz"
REPORT = LAB / "schwartz_trader_report.json"


def _ema(px: np.ndarray, span: int) -> np.ndarray:
    out = np.empty(len(px), dtype=np.float64)
    a = 2.0 / (span + 1.0)
    out[0] = px[0]
    for i in range(1, len(px)):
        out[i] = a * px[i] + (1.0 - a) * out[i - 1]
    return out


def _load_universe():
    series = {}
    for sym in MAJORS:
        try:
            s = build_hourly_series(sym)
        except Exception:
            continue
        if len(s["px"]) < 5000 or len(s["f_ts"]) < 120:
            continue
        if int(s["close_ts"][0]) > Y2024:
            continue
        series[sym] = s
    sp.LOT_STEP.update(EXTRA_LOT)
    for sym in series:
        SHORT_NAME.setdefault(sym, sym.replace("USDT", ""))
    return series


def _load_sleeve_universe():
    """Coins the basket can rank. A coin may start after 2024."""
    series = {}
    for sym in MAJORS:
        try:
            s = build_hourly_series(sym)
        except Exception:
            continue
        if len(s["px"]) < 400 or len(s["f_ts"]) < 30:
            continue
        series[sym] = s
    sp.LOT_STEP.update(EXTRA_LOT)
    for sym in series:
        SHORT_NAME.setdefault(sym, sym.replace("USDT", ""))
    return series


def _exit_trade(s, i: int, side: int) -> dict | None:
    px = s["px"]
    n = len(px)
    if i < 24 or i >= n - 2:
        return None
    entry_px = float(px[i])
    atr = max(float(s["atr1h"][i]), 0.002)
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    peak_move = 0.0
    min_move = 0.0
    exit_idx = i + 1
    reason = "end"
    pyr_triggered = False
    pyr_entry_move = 0.0
    tp1_done = False
    tp1_move = 0.0
    tp2_done = False
    tp2_move = 0.0
    closed = False
    last_j = min(n - 1, i + 96)
    for j in range(i + 1, last_j + 1):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + HOUR:
            exit_idx = j - 1
            reason = "gap"
            closed = True
            break
        cpx = float(px[j])
        move = side * (cpx / entry_px - 1.0)
        min_move = min(min_move, move)
        peak_move = max(peak_move, move)
        h = j - i
        if h <= 4 and move <= -0.8 * atr:
            exit_idx, reason, closed = j, "fast_bail", True
            break
        if move <= -2.2 * atr:
            exit_idx, reason, closed = j, "hard_stop", True
            break
        be_floor = 1.15 * atr if pyr_triggered else 0.4 * atr
        if tp1_done and move <= be_floor:
            exit_idx, reason, closed = j, "tp_scale+be", True
            break
        ema = float(s["ema240"][j])
        if (side == 1 and cpx < ema) or (side == -1 and cpx > ema):
            exit_idx, reason, closed = j, "ema10_exit", True
            break
        if not pyr_triggered and move >= 2.0 * atr:
            pyr_triggered = True
            pyr_entry_move = move
        if not tp1_done and move >= 3.0 * atr:
            tp1_done = True
            tp1_move = move
        if not tp2_done and move >= 5.5 * atr:
            tp2_done = True
            tp2_move = move
        exit_idx = j
        if h >= 96:
            closed = True
            break
    if not closed and (exit_idx - i) < 90:
        return None
    exit_px = float(px[exit_idx])
    t_ms = int(s["close_ts"][i])
    exit_ts = int(s["close_ts"][exit_idx])
    lo = int(np.searchsorted(f_ts, t_ms, side="right"))
    hi = int(np.searchsorted(f_ts, exit_ts, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
    f_adj = -paid if side == 1 else paid
    raw_end = side * (exit_px / entry_px - 1.0)
    rem_w = 1.0
    base_gross = 0.0
    pyr_gross = 0.0
    if tp1_done:
        w1 = 0.20 * rem_w
        base_gross += w1 * tp1_move
        if pyr_triggered:
            pyr_gross += w1 * (tp1_move - pyr_entry_move)
        rem_w -= w1
    if tp2_done:
        w2 = 0.15 * rem_w
        base_gross += w2 * tp2_move
        if pyr_triggered:
            pyr_gross += w2 * (tp2_move - pyr_entry_move)
        rem_w -= w2
    base_gross += rem_w * raw_end
    if pyr_triggered:
        pyr_gross += rem_w * (raw_end - pyr_entry_move)
    net = base_gross + f_adj - TAKER_FEE
    pyr_net = 0.50 * (pyr_gross + 0.5 * f_adj - TAKER_FEE) if pyr_triggered else 0.0
    return {
        "t": t_ms,
        "exit_ts": exit_ts,
        "hold_h": int(exit_idx - i),
        "side": side,
        "entry_px": round(entry_px, 6),
        "exit_px": round(exit_px, 6),
        "net": float(net),
        "pyr_net": float(pyr_net),
        "min_move": float(min_move),
        "atr": float(atr),
        "reason": reason,
        "pyr": bool(pyr_triggered),
        "path": [],
    }


def build_events(series: dict) -> list[dict]:
    syms = list(series)
    onehot = {sym: i for i, sym in enumerate(syms)}
    n_coin = len(syms)
    btc = series["BTCUSDT"]
    btc_map = {int(t): i for i, t in enumerate(btc["close_ts"])}
    ema48 = {sym: _ema(s["px"], 48) for sym, s in series.items()}
    events = []
    for sym in syms:
        s = series[sym]
        px, vol, ema = s["px"], s["vol"], s["ema240"]
        n = len(px)
        fast = ema48[sym]
        taken = set()
        for i in range(260, n - 8):
            es = float(ema[i])
            prev_es = float(ema[i - 24])
            if prev_es <= 0 or es <= 0:
                continue
            slope = es / prev_es - 1.0
            cur = float(px[i])
            prev = float(px[i - 1])
            side = 0
            reclaim = False
            if cur > es and slope > 0:
                side = 1
                reclaim = fast[i] > es and prev <= fast[i - 1] and cur > fast[i]
            elif cur < es and slope < 0:
                side = -1
                reclaim = fast[i] < es and prev >= fast[i - 1] and cur < fast[i]
            if side == 0:
                continue
            on_grid = (i % 8) == 0
            if not on_grid and not reclaim:
                continue
            atr = max(float(s["atr1h"][i]), 0.002)
            r4 = side * (cur / float(px[i - 4]) - 1.0) / atr
            if not reclaim and r4 < 0.75:
                continue
            t = int(s["close_ts"][i])
            if t in taken:
                continue
            trade = _exit_trade(s, i, side)
            if trade is None:
                continue
            taken.add(t)
            bi = btc_map.get(t)
            if bi is None or bi < 24:
                continue
            btc_px = float(btc["px"][bi])
            btc_e = float(btc["ema240"][bi])
            btc_atr = max(float(btc["atr1h"][bi]), 0.002)
            btc_dist = side * (btc_px / btc_e - 1.0) / btc_atr
            fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
            if fi >= 90:
                hist = s["f_rate"][fi - 89: fi + 1]
                f_z = (float(s["f_rate"][fi]) - float(hist.mean())) / (float(hist.std()) + 1e-8)
                carry_z = -side * f_z
            else:
                carry_z = 0.0
            r1 = side * (cur / float(px[i - 1]) - 1.0) / atr
            r24 = side * (cur / float(px[i - 24]) - 1.0) / atr
            dist_es = side * (cur / es - 1.0) / atr
            dist_48 = side * (cur / float(fast[i]) - 1.0) / atr
            v4 = float(vol[i - 3: i + 1].mean())
            v24 = float(vol[i - 23: i + 1].mean()) + 1e-9
            hour = (t // HOUR) % 24
            ang = 2.0 * np.pi * hour / 24.0
            num = np.array([
                float(side), carry_z, atr * 100.0, slope / atr, dist_es, dist_48,
                r1, r4, r24, v4 / v24, btc_dist, 0.0, 0.0,
                np.sin(ang), np.cos(ang), 1.0 if reclaim else 0.0,
            ], dtype=np.float64)
            oh = np.zeros(n_coin, dtype=np.float64)
            oh[onehot[sym]] = 1.0
            trade.update({
                "sym": sym,
                "coin": SHORT_NAME.get(sym, sym.replace("USDT", "")),
                "ci": onehot[sym],
                "feat": np.concatenate([num, oh]),
                "reclaim": reclaim,
            })
            events.append(trade)
        print("  %s events_so_far=%d" % (sym, len(events)), flush=True)
    events.sort(key=lambda e: (e["t"], e["sym"]))
    from collections import deque
    hist = {s: deque(maxlen=6) for s in syms}
    streak = {s: 0 for s in syms}
    pend = []
    for e in events:
        t = e["t"]
        still = []
        for ex_t, sym_p, tot in pend:
            if ex_t <= t:
                hist[sym_p].append(tot)
                streak[sym_p] = streak[sym_p] + 1 if tot < 0 else 0
            else:
                still.append((ex_t, sym_p, tot))
        pend = still
        ch = hist[e["sym"]]
        hot_n = len(ch)
        hot_avg = float(np.mean(ch)) if hot_n else 0.0
        e["hot_n"] = hot_n
        e["hot_avg"] = hot_avg
        e["c_lstreak"] = streak[e["sym"]]
        e["feat"][11] = hot_avg
        e["feat"][12] = float(e["c_lstreak"])
        e["target"] = float(e["net"] + 1.5 * e["pyr_net"])
        pend.append((e["exit_ts"], e["sym"], e["target"]))
    return events


def _y(e):
    return float(np.clip(e["target"], -0.05, 0.12))


def _w(e):
    y = _y(e)
    if y > 0.02:
        return 2.4
    if y < 0:
        return 1.25
    return 1.0


@dataclass
class FittedNet:
    model: MLPRegressor
    mu: np.ndarray
    sd: np.ndarray

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict((X - self.mu) / self.sd)


def _fit(rows: list[dict]) -> FittedNet | None:
    if len(rows) < 250:
        return None
    use = rows[-5000:]
    X = np.vstack([e["feat"] for e in use])
    y = np.array([_y(e) for e in use], dtype=np.float64)
    w = np.array([_w(e) for e in use], dtype=np.float64)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    net = MLPRegressor(
        hidden_layer_sizes=HIDDEN,
        activation="tanh",
        solver="lbfgs",
        alpha=0.6,
        max_iter=320,
        random_state=0,
    )
    try:
        net.fit((X - mu) / sd, y, sample_weight=w)
    except TypeError:
        net.fit((X - mu) / sd, y)
    return FittedNet(net, mu, sd)


def _apply_scores(rows: list[dict], net: FittedNet) -> None:
    if not rows:
        return
    X = np.vstack([e["feat"] for e in rows])
    pred = net.predict(X)
    for e, p in zip(rows, pred):
        e["nn_score"] = float(p)


def _one_a_day(rows, thr):
    best = {}
    for e in rows:
        if e.get("nn_score", -1e9) < thr:
            continue
        day = (e["t"] // DAY) * DAY
        cur = best.get(day)
        if cur is None or e["nn_score"] > cur["nn_score"]:
            best[day] = e
    return list(best.values())


def _best_threshold(val: list[dict]) -> float:
    if len(val) < 12:
        return 1e9
    scores = np.array([e["nn_score"] for e in val], dtype=np.float64)
    best_thr = float(np.quantile(scores, 0.97))
    best_key = -1e9
    for q in (0.50, 0.70, 0.85, 0.93, 0.97):
        thr = float(np.quantile(scores, q))
        part = _one_a_day(val, thr)
        if not (3 <= len(part) <= 18):
            continue
        res = simulate_window(part, core_gate=False, rank="nn")
        if not (3 <= res["n"] <= 18):
            continue
        key = res["ret"] - max(0.0, res["dd"] - 25.0) * 2.0
        if key > best_key:
            best_key = key
            best_thr = thr
    return best_thr


def walk_forward(events: list[dict], refit_days: int = 21, train_days: int = 420) -> list[dict]:
    ordered = events
    t_min = min(e["t"] for e in ordered)
    t_max = max(e["t"] for e in ordered)
    day0 = ((t_min // DAY) + 180) * DAY
    by_day = {}
    for e in ordered:
        by_day.setdefault((e["t"] // DAY) * DAY, []).append(e)
    picked = []
    net = None
    thr = -1e9
    days = list(range(day0, t_max + DAY, DAY))
    for idx, d in enumerate(days):
        if net is None or idx % refit_days == 0:
            lo = d - train_days * DAY
            train = [e for e in ordered if lo <= e["t"] and e["exit_ts"] <= d]
            val_cut = d - 50 * DAY
            fit_rows = [e for e in train if e["t"] < val_cut]
            val_rows = [e for e in train if e["t"] >= val_cut]
            fitted = _fit(fit_rows)
            if fitted is not None:
                net = fitted
                _apply_scores(val_rows, net)
                thr = _best_threshold(val_rows)
                print("  refit %s train=%d thr=%.4f" % (
                    __import__("datetime").datetime.fromtimestamp(d / 1000, __import__("datetime").timezone.utc).date(),
                    len(fit_rows), thr,
                ), flush=True)
        if net is None:
            continue
        day_rows = by_day.get(d, [])
        if not day_rows:
            continue
        _apply_scores(day_rows, net)
        for e in _one_a_day(day_rows, thr):
            row = dict(e)
            row.pop("nn_mult", None)
            picked.append(row)
    return picked


def pack(res):
    return {
        "ret": res["ret"], "dd": res["dd"], "n": res["n"],
        "wins": res["wins"],
        "wr": round(res["wins"] / max(1, res["n"]) * 100.0, 1),
        "final": res["final"],
    }


def windows(cands, **kw):
    if not cands:
        return {}
    t_max = max(e["t"] for e in cands)
    slices = {
        "30": [e for e in cands if e["t"] >= t_max - 30 * DAY],
        "90": [e for e in cands if e["t"] >= Q_CUT],
        "365": [e for e in cands if e["t"] >= t_max - 365 * DAY],
        "full": list(cands),
        "2025": [e for e in cands if Y2025 <= e["t"] < Y2026],
        "2026": [e for e in cands if e["t"] >= Y2026],
    }
    return {k: pack(simulate_window([dict(e) for e in v], **kw)) for k, v in slices.items()}


def coin_stats(trades):
    by = {}
    for t in trades:
        b = by.setdefault(t["coin"], {"n": 0, "wins": 0, "usd": 0.0})
        b["n"] += 1
        b["wins"] += 1 if t["pnl_usd"] > 0 else 0
        b["usd"] += t["pnl_usd"]
    return {k: {**v, "usd": round(v["usd"], 1)} for k, v in sorted(by.items(), key=lambda kv: -kv[1]["usd"])}


def save_net(events, now_ms):
    train = [e for e in events if e["exit_ts"] <= now_ms]
    net = _fit(train)
    if net is None:
        return None
    np.savez(
        WEIGHTS,
        coefs_0=net.model.coefs_[0],
        coefs_1=net.model.coefs_[1],
        intercepts_0=net.model.intercepts_[0],
        intercepts_1=net.model.intercepts_[1],
        mu=net.mu,
        sd=net.sd,
        n_train=len(train[-5000:]),
        hidden=np.array(HIDDEN),
    )
    return net


def load_net():
    if not WEIGHTS.exists():
        return None
    z = np.load(WEIGHTS, allow_pickle=False)
    net = MLPRegressor(hidden_layer_sizes=HIDDEN, activation="tanh")
    net.coefs_ = [z["coefs_0"], z["coefs_1"]]
    net.intercepts_ = [z["intercepts_0"], z["intercepts_1"]]
    net.n_layers_ = 3
    net.n_outputs_ = 1
    net.out_activation_ = "identity"
    return FittedNet(net, z["mu"], z["sd"])


def main():
    print("loading majors...", flush=True)
    series = _load_universe()
    print("universe: %s" % ", ".join(series), flush=True)
    print("building dense labels...", flush=True)
    events = build_events(series)
    print("events=%d" % len(events), flush=True)
    print("walk-forward...", flush=True)
    picked = walk_forward(events)
    print("picked=%d" % len(picked), flush=True)

    model_w = windows(picked, core_gate=False, rank="nn")
    print("BASE production book...", flush=True)
    base_cands, _ = build_tranche_candidates()
    base_w = windows(base_cands, core_gate=True, rank="rules")

    def line(name, res):
        bits = ["%-18s" % name]
        for w in ("30", "90", "365", "2025", "2026", "full"):
            if w not in res:
                continue
            bits.append("%s=%+6.1f/%0.fn%d" % (w, res[w]["ret"], res[w]["dd"], res[w]["n"]))
        print("  ".join(bits), flush=True)

    print("\n=== books ===", flush=True)
    line("MODEL", model_w)
    line("BASE rules", base_w)

    y26 = [e for e in picked if e["t"] >= Y2026]
    qtr = [e for e in picked if e["t"] >= Q_CUT]
    sim26 = simulate_window(y26, core_gate=False, rank="nn")
    simq = simulate_window(qtr, core_gate=False, rank="nn")
    save_net(events, int(max(e["exit_ts"] for e in events)))
    report = {
        "universe": list(series),
        "n_events": len(events),
        "n_picked": len(picked),
        "model": model_w,
        "base": base_w,
        "model_2026_coins": coin_stats(sim26["trades"]),
        "model_qtr_coins": coin_stats(simq["trades"]),
        "reclaim_share_picked": round(sum(1 for e in picked if e.get("reclaim")) / max(1, len(picked)), 3),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("2026 coins", report["model_2026_coins"], flush=True)
    print("qtr coins", report["model_qtr_coins"], flush=True)
    print("wrote", REPORT, flush=True)


if __name__ == "__main__":
    main()
