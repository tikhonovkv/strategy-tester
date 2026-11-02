"""Walk-forward sequence trader in the LFM shape.

Research only. It does not load the 1.7B language weights. Each hour is a
48-bar window of causal returns, volume, funding and strength versus BTC.
The barrier exit frees the slot at the hour the target or the stop is hit.
"""
from __future__ import annotations

import heapq
import json
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

import schwartz_production as sp
import schwartz_trader as st

COST = 0.0011 + 0.0006
HORIZON = 48
UP_ATR = 2.5
DN_ATR = 1.5
OUT = sp.LAB / "_lfm_seq_trader.json"


def stamp(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
FOLDS = [
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, END),
]


def _funding_on_bars(s):
    out = np.zeros(len(s["px"]), dtype=np.float64)
    fi = 0
    rates = s["f_rate"]
    fts = s["f_ts"]
    for i, t in enumerate(s["close_ts"]):
        while fi + 1 < len(fts) and int(fts[fi + 1]) <= int(t):
            fi += 1
        if fi < len(rates) and int(fts[fi]) <= int(t):
            out[i] = float(rates[fi])
    return out


def _side_barrier(rel, atr_value):
    up = np.flatnonzero(rel >= UP_ATR * atr_value)
    dn = np.flatnonzero(rel <= -DN_ATR * atr_value)
    up_i = int(up[0]) if len(up) else 10**9
    dn_i = int(dn[0]) if len(dn) else 10**9
    if up_i < dn_i and up_i < 10**9:
        return 1, float(rel[up_i]) - COST, up_i + 1
    if dn_i < up_i and dn_i < 10**9:
        return 0, float(rel[dn_i]) - COST, dn_i + 1
    return 0, float(rel[-1]) - COST, len(rel)


def _barriers(px, atr):
    n = len(px)
    long_ret = np.zeros(n)
    short_ret = np.zeros(n)
    long_y = np.zeros(n, dtype=np.int8)
    short_y = np.zeros(n, dtype=np.int8)
    long_off = np.full(n, HORIZON, dtype=np.int16)
    short_off = np.full(n, HORIZON, dtype=np.int16)
    for i in range(n - 2):
        a = max(float(atr[i]), 0.002)
        window = px[i + 1:i + 1 + HORIZON]
        if len(window) < 2 or px[i] <= 0:
            continue
        rel = window / px[i] - 1.0
        long_y[i], long_ret[i], long_off[i] = _side_barrier(rel, a)
        short_y[i], short_ret[i], short_off[i] = _side_barrier(-rel, a)
    return long_y, short_y, long_ret, short_ret, long_off, short_off


def _causal_conv(x, kernel):
    full = np.convolve(x, kernel[::-1], mode="full")
    return full[:len(x)]


def _features(px, vol, fund, atr, btc_ret24):
    ret = np.zeros(len(px))
    ret[1:] = px[1:] / np.maximum(px[:-1], 1e-12) - 1.0
    c3 = _causal_conv(ret, np.ones(3) / 3.0)
    c7 = _causal_conv(ret, np.ones(7) / 7.0)
    kernel = np.exp(-np.arange(48)[::-1] / 12.0)
    kernel /= kernel.sum()
    ewm = _causal_conv(ret, kernel)
    med = np.array([
        np.median(vol[max(0, i - 24):i]) if i >= 8 else vol[i]
        for i in range(len(vol))
    ])
    surge = vol / np.maximum(med, 1e-12)
    ret24 = np.zeros(len(px))
    ret4 = np.zeros(len(px))
    ret24[24:] = px[24:] / np.maximum(px[:-24], 1e-12) - 1.0
    ret4[4:] = px[4:] / np.maximum(px[:-4], 1e-12) - 1.0
    fund_speed = np.zeros(len(fund))
    fund_speed[24:] = fund[24:] - fund[:-24]
    coil = np.abs(ret) / np.maximum(atr, 0.002)
    rs = ret24 - btc_ret24[:len(px)]
    return np.column_stack([
        c3, c7, ewm, surge, rs, fund, ret24, ret4,
        coil, c3 - c7, c7 - ewm, fund_speed,
    ])


def build_rows(series):
    btc = series["BTCUSDT"]
    btc_ret = np.zeros(len(btc["px"]))
    btc_ret[24:] = btc["px"][24:] / np.maximum(btc["px"][:-24], 1e-12) - 1.0
    btc_map = {int(t): btc_ret[i] for i, t in enumerate(btc["close_ts"])}
    rows = []
    for sym, s in series.items():
        if sym == "BTCUSDT" or sym in sp.ALTS_7:
            continue
        px = np.asarray(s["px"], dtype=np.float64)
        atr = np.asarray(s["atr1h"], dtype=np.float64)
        vol = np.asarray(s["vol"], dtype=np.float64)
        ts = [int(t) for t in s["close_ts"]]
        btc_aligned = np.array([btc_map.get(t, 0.0) for t in ts])
        fund = _funding_on_bars(s)
        feats = _features(px, vol, fund, atr, btc_aligned)
        ly, sy, lr, sr, loff, soff = _barriers(px, atr)
        for i in range(48, len(px) - HORIZON - 1, 4):
            rows.append({
                "sym": sym,
                "t": ts[i],
                "x": feats[i].astype(np.float32),
                "ly": int(ly[i]),
                "sy": int(sy[i]),
                "lr": float(lr[i]),
                "sr": float(sr[i]),
                "el": int(loff[i]),
                "es": int(soff[i]),
            })
    rows.sort(key=lambda r: r["t"])
    return rows


def _fit_pair(rows, kind):
    x = np.stack([r["x"] for r in rows])
    y_long = np.array([r["ly"] for r in rows])
    y_short = np.array([r["sy"] for r in rows])
    if kind == "hgb":
        long_m = HistGradientBoostingClassifier(max_iter=80, max_depth=3, learning_rate=0.08)
        short_m = HistGradientBoostingClassifier(max_iter=80, max_depth=3, learning_rate=0.08)
    else:
        long_m = LogisticRegression(max_iter=200, class_weight="balanced")
        short_m = LogisticRegression(max_iter=200, class_weight="balanced")
    long_m.fit(x, y_long)
    short_m.fit(x, y_short)
    return long_m, short_m


def _threshold(model, rows, key_r):
    if not rows:
        return 0.7, -1e9
    p = model.predict_proba(np.stack([r["x"] for r in rows]))[:, 1]
    best_t, best_score = 0.65, -1e9
    for thr in (0.55, 0.60, 0.65, 0.70, 0.75):
        mask = p >= thr
        if int(mask.sum()) < 20:
            continue
        score = float(np.mean([rows[i][key_r] for i in np.flatnonzero(mask)]))
        if score > best_score:
            best_t, best_score = thr, score
    return best_t, best_score


def _book(rows, long_m, short_m, long_thr, short_thr, start, end, busy=None, cash=None):
    equity = 2000.0
    peak = equity
    max_dd = 0.0
    open_pos = []
    trades = []
    chosen = [r for r in rows if start <= r["t"] < end]
    if not chosen:
        return {"ret": 0.0, "dd": 0.0, "final": 2000.0, "n": 0}
    x = np.stack([r["x"] for r in chosen])
    pl = long_m.predict_proba(x)[:, 1]
    ps = short_m.predict_proba(x)[:, 1]
    by_t = {}
    for i, r in enumerate(chosen):
        by_t.setdefault(r["t"], []).append((i, r))
    heap = [(t, 1) for t in by_t]
    heapq.heapify(heap)
    while heap:
        t, kind = heapq.heappop(heap)
        still = []
        for pos in open_pos:
            if pos["exit"] <= t:
                equity += pos["pnl"]
                if cash is not None:
                    cash[0] += pos["pnl"]
                trades.append(pos["pnl"])
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)
            else:
                still.append(pos)
        open_pos = still
        if kind == 0 or equity <= 0:
            continue
        if busy is not None and busy(t) >= 2:
            continue
        cands = []
        for i, r in by_t[t]:
            if pl[i] >= long_thr:
                cands.append((pl[i], 1, r))
            if ps[i] >= short_thr:
                cands.append((ps[i], -1, r))
        cands.sort(key=lambda row: row[0], reverse=True)
        held = {pos["sym"] for pos in open_pos}
        for _score, side, r in cands:
            if len(open_pos) >= 2 or r["sym"] in held:
                continue
            free = equity if cash is None else cash[0]
            notion = min(equity * 0.15, free)
            if notion < 15.0:
                continue
            if cash is not None:
                cash[0] -= notion
            ret = r["lr"] if side == 1 else r["sr"]
            off = r["el"] if side == 1 else r["es"]
            pnl = notion * ret
            open_pos.append({
                "sym": r["sym"],
                "exit": r["t"] + int(off) * sp.HOUR,
                "pnl": pnl,
                "margin": notion,
            })
            heapq.heappush(heap, (open_pos[-1]["exit"], 0))
            held.add(r["sym"])
    for pos in open_pos:
        equity += pos["pnl"]
        if cash is not None:
            cash[0] += pos["pnl"]
        trades.append(pos["pnl"])
    peak = max(peak, equity)
    if peak > 0:
        max_dd = max(max_dd, (peak - equity) / peak)
    return {
        "ret": round((equity / 2000.0 - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "final": round(equity, 2),
        "n": len(trades),
    }


def _shared(rows, long_m, short_m, long_thr, short_thr, start, end, schwartz):
    """One account: Schwartz margin stays reserved, LFM uses only the free cash."""
    events = []
    for tr in schwartz["trades"]:
        margin = float(tr["margin"])
        buffer = 0.50 * margin
        events.append((int(tr["t"]), 2, "lock", margin + buffer))
        events.append((int(tr["exit_ts"]), 0, "release", (margin + buffer, float(tr["pnl_usd"]))))
    chosen = [r for r in rows if start <= r["t"] < end]
    if chosen:
        x = np.stack([r["x"] for r in chosen])
        pl = long_m.predict_proba(x)[:, 1]
        ps = short_m.predict_proba(x)[:, 1]
    else:
        pl = ps = np.zeros(0)
    by_t = {}
    for i, r in enumerate(chosen):
        by_t.setdefault(r["t"], []).append((i, r))
    for t in by_t:
        events.append((t, 1, "signal", None))
    free = 2000.0
    reserved = 0.0
    open_pos = []
    schwartz_open = 0
    trades = []
    peak = 2000.0
    max_dd = 0.0

    def equity():
        return free + reserved

    def mark(eq):
        nonlocal peak, max_dd
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    heap = [(item[0], item[1], n, item[2], item[3]) for n, item in enumerate(events)]
    heapq.heapify(heap)
    seq = len(heap)
    while heap:
        _t, _order, _seq, kind, payload = heapq.heappop(heap)
        if kind == "lock":
            free -= float(payload)
            reserved += float(payload)
            schwartz_open += 1
        elif kind == "release":
            locked, pnl = payload
            reserved -= locked
            free += locked + pnl
            schwartz_open = max(0, schwartz_open - 1)
            mark(equity())
        elif kind == "lfm_exit":
            pos = payload
            if pos in open_pos:
                open_pos.remove(pos)
                free += pos["margin"] + pos["pnl"]
                reserved -= pos["margin"]
                trades.append(pos["pnl"])
                mark(equity())
        else:
            if schwartz_open >= 2 or equity() <= 0:
                continue
            cands = []
            for i, r in by_t[_t]:
                if pl[i] >= long_thr:
                    cands.append((pl[i], 1, r))
                if ps[i] >= short_thr:
                    cands.append((ps[i], -1, r))
            cands.sort(key=lambda row: row[0], reverse=True)
            held = {pos["sym"] for pos in open_pos}
            for _score, side, r in cands:
                if len(open_pos) >= 2 or r["sym"] in held:
                    continue
                notion = min(equity() * 0.15, free)
                if notion < 15.0:
                    continue
                free -= notion
                reserved += notion
                ret = r["lr"] if side == 1 else r["sr"]
                off = r["el"] if side == 1 else r["es"]
                exit_ts = r["t"] + int(off) * sp.HOUR
                pos = {
                    "sym": r["sym"],
                    "exit": exit_ts,
                    "pnl": notion * ret,
                    "margin": notion,
                }
                open_pos.append(pos)
                seq += 1
                heapq.heappush(heap, (exit_ts, 0, seq, "lfm_exit", pos))
                held.add(r["sym"])
    for pos in open_pos:
        free += pos["margin"] + pos["pnl"]
        reserved -= pos["margin"]
        trades.append(pos["pnl"])
    final = free + reserved
    mark(final)
    return {
        "ret": round((final / 2000.0 - 1.0) * 100.0, 2),
        "dd": round(max_dd * 100.0, 1),
        "final": round(final, 2),
        "n": len(trades),
    }


def main():
    series = st._load_universe()
    rows = build_rows(series)
    print("rows", len(rows), flush=True)
    cands, _ = sp.build_tranche_candidates()
    report = {"logistic": {}, "hgb": {}, "shared": {}}
    for label, a, b in FOLDS:
        train = [r for r in rows if r["t"] < a and r["t"] >= a - 370 * 24 * sp.HOUR]
        test = [r for r in rows if a <= r["t"] < b]
        if len(train) < 500 or not test:
            print(label, "skip", len(train), len(test), flush=True)
            continue
        part = [e for e in cands if a <= int(e["t"]) < b]
        schwartz = sp.simulate_window(part)
        best_kind = None
        best_train = -1e9
        fitted = {}
        for kind in ("logistic", "hgb"):
            long_m, short_m = _fit_pair(train, kind)
            long_thr, long_score = _threshold(long_m, train[-4000:], "lr")
            short_thr, short_score = _threshold(short_m, train[-4000:], "sr")
            solo = _book(test, long_m, short_m, long_thr, short_thr, a, b)
            fitted[kind] = (long_m, short_m, long_thr, short_thr)
            report[kind][label] = {"solo": solo, "long_thr": long_thr, "short_thr": short_thr}
            print(label, kind, solo, "thr", long_thr, short_thr, flush=True)
            if long_score + short_score > best_train:
                best_train = long_score + short_score
                best_kind = kind
        long_m, short_m, long_thr, short_thr = fitted[best_kind]
        shared = _shared(test, long_m, short_m, long_thr, short_thr, a, b, schwartz)
        report["shared"][label] = {
            "book": shared,
            "head": best_kind,
            "schwartz_ret": schwartz["ret"],
            "schwartz_final": schwartz["final"],
            "schwartz_dd": schwartz["dd"],
        }
        print(label, "shared", best_kind, shared, "schwartz", schwartz["ret"], flush=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
