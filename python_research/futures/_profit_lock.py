# -*- coding: utf-8 -*-
"""Realistic profit-lock search. Local only.
Wick-conservative hourly stops: adverse extreme is checked against the stop
from prior bars before the favorable extreme updates the peak.
"""
from __future__ import annotations
import json, time, urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import schwartz_production as sp

SLIP = 0.0003  # 3 bps adverse per fill
CACHE = sp.LAB / "_ohlc_cache"
OUTP = sp.LAB / "_profit_lock_state.json"


def collect_entries():
    dump_path = sp.LAB / "championship_dump.json"
    by_key = {}
    if dump_path.exists():
        d = json.loads(dump_path.read_text(encoding="utf-8"))
        for rdata in d.get("runs", {}).values():
            for tr in rdata["periods"]["FULL"]["trades"]:
                by_key[(int(tr["t"]), tr["sym"])] = tr
    series = {sym: sp.build_hourly_series(sym) for sym in ["BTCUSDT"] + sp.ALTS_7}
    idx_map = {sym: {int(ts): i for i, ts in enumerate(s["close_ts"])} for sym, s in series.items()}
    dump_max_t = max((t for (t, _sym) in by_key), default=0)
    btc = series["BTCUSDT"]
    btc_map = idx_map["BTCUSDT"]
    for sym in sp.ALTS_7:
        s = series[sym]
        close_ts, px, ema240, f_ts, f_rate = s["close_ts"], s["px"], s["ema240"], s["f_ts"], s["f_rate"]
        n = len(px)
        if n < 265:
            continue
        ema48 = np.zeros(n, dtype=np.float64)
        a48 = 2.0 / 49.0
        ema48[0] = px[0]
        for k in range(1, n):
            ema48[k] = a48 * px[k] + (1.0 - a48) * ema48[k - 1]
        for i in range(240, n - 1):
            t_ms = int(close_ts[i])
            if t_ms <= dump_max_t or (t_ms, sym) in by_key:
                continue
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
            f_hist = f_rate[fi - 89:fi + 1]
            f_z = float(f_rate[fi] - f_hist.mean()) / (float(f_hist.std()) + 1e-8)
            carry_z = -side * f_z
            btc_px, btc_e240 = float(btc["px"][bi]), float(btc["ema240"][bi])
            btc_ok = (side == 1 and btc_px > btc_e240) or (side == -1 and btc_px < btc_e240)
            if not btc_ok or carry_z < 0.0:
                continue
            by_key[(t_ms, sym)] = {"t": t_ms, "sym": sym, "side": side}
    entries = []
    for (t, sym), tr in sorted(by_key.items()):
        if sym not in sp.ALTS_7 or t not in idx_map[sym]:
            continue
        entries.append({"t": int(t), "sym": sym, "side": int(tr["side"]), "i": idx_map[sym][t]})
    return entries, series


def fetch_ohlc(sym, start_open, end_open):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{sym}.json"
    data = {}
    if path.exists():
        data = {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    need_lo = int(start_open)
    need_hi = int(end_open)
    have = [k for k in data if need_lo <= k <= need_hi]
    span = max(1, int((need_hi - need_lo) / sp.HOUR))
    if len(have) > span * 0.95 and data:
        return data
    cur_end = need_hi + sp.HOUR
    pages = 0
    while cur_end > need_lo and pages < 80:
        payload = None
        last_err = None
        for host in ("https://api.bybit.com", "https://api.bytick.com"):
            url = (
                host + "/v5/market/kline?category=linear&symbol="
                + sym + "&interval=60&limit=1000&end=" + str(cur_end)
            )
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url, timeout=20) as r:
                        payload = json.loads(r.read().decode())
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    time.sleep(0.6 * (attempt + 1))
            if payload is not None and int(payload.get("retCode") or 0) == 0:
                break
            payload = None
        if payload is None:
            raise RuntimeError(str(last_err))
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            break
        oldest = None
        for row in rows:
            ts = int(row[0])
            oldest = ts if oldest is None else min(oldest, ts)
            data[ts] = [float(row[1]), float(row[2]), float(row[3]), float(row[4])]
        pages += 1
        nxt = oldest - 1
        if nxt >= cur_end:
            break
        cur_end = nxt
        time.sleep(0.08)
    path.write_text(json.dumps({str(k): v for k, v in data.items()}), encoding="utf-8")
    print(sym, "ohlc", len(data), "pages", pages, flush=True)
    return data


def fund_adj(s, side, t0, t1):
    f_ts, f_rate = s["f_ts"], s["f_rate"]
    lo = int(np.searchsorted(f_ts, t0, side="right"))
    hi = int(np.searchsorted(f_ts, t1, side="right"))
    paid = float(f_rate[lo:hi].sum()) if hi > lo else 0.0
    return (-paid if side == 1 else paid)


def fill_at(side, entry, move, open_px, slip):
    stop_px = entry * (1.0 + side * move)
    if side == 1:
        px = min(open_px, stop_px)
    else:
        px = max(open_px, stop_px)
    return px * (1.0 - side * slip)



def apply_exit(s, ohlc, i, side, policy, slip, hard_wick=True):
    px, ema = s["px"], s["ema240"]
    signal = float(px[i])
    entry = signal * (1.0 + side * slip)
    atr = max(float(s["atr1h"][i]), 0.002)
    peak_close = 0.0
    peak_wick = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    locked = 0.0
    lock_move = 0.0
    lock_ts = int(s["close_ts"][i])
    reason = "end_96h"
    exit_idx = min(len(px) - 1, i + 1)
    exit_px = entry
    last = min(len(px), i + 96)
    missed = 0
    for j in range(i + 1, last):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
            exit_idx = j - 1
            exit_px = float(px[exit_idx]) * (1.0 - side * slip)
            reason = "gap"
            break
        open_ts = int(s["close_ts"][j]) - sp.HOUR
        bar = ohlc.get(open_ts)
        if bar is None:
            missed += 1
            o, h, l, c = (float(px[j]),) * 4
        else:
            o, h, l, c = bar
        fav, adv = (h, l) if side == 1 else (l, h)
        move_adv = side * (adv / entry - 1.0)
        move_fav = side * (fav / entry - 1.0)
        move_cls = side * (c / entry - 1.0)
        min_move = min(min_move, move_adv)
        hld = j - i
        # Resting hard stop and resting trail see the wick. Bail, floor and EMA see the close.
        if hard_wick and move_adv <= -2.2 * atr:
            exit_idx = j
            exit_px = fill_at(side, entry, -2.2 * atr, o, slip)
            reason = "hard_stop"
            break
        extra = policy(hld, move_adv, peak_wick, atr, pyr) if hard_wick else None
        if extra is not None:
            exit_idx = j
            exit_px = fill_at(side, entry, extra[0], o, slip)
            reason = extra[1]
            break
        be = 1.15 * atr if pyr else 0.4 * atr
        if hld <= 4 and move_cls <= -0.8 * atr:
            exit_idx = j
            exit_px = c * (1.0 - side * slip)
            reason = "fast_bail"
            break
        if (not hard_wick) and move_cls <= -2.2 * atr:
            exit_idx = j
            exit_px = c * (1.0 - side * slip)
            reason = "hard_stop"
            break
        if peak_close >= 3.0 * atr and move_cls <= be:
            exit_idx = j
            exit_px = c * (1.0 - side * slip)
            reason = "breakeven"
            break
        if (not pyr) and move_cls >= 2.0 * atr:
            pyr = True
            pyr_move = 2.0 * atr
        want = getattr(policy, "partial_at", None)
        if locked == 0.0 and want and move_fav >= want[1] * atr:
            locked = want[0]
            lock_move = want[1] * atr
            lock_ts = int(s["close_ts"][j])
        peak_close = max(peak_close, move_cls)
        peak_wick = max(peak_wick, move_fav)
        if (side == 1 and c < float(ema[j])) or (side == -1 and c > float(ema[j])):
            exit_idx = j
            exit_px = c * (1.0 - side * slip)
            reason = "ema10_exit"
            break
        exit_idx = j
        exit_px = c * (1.0 - side * slip)
        reason = "end_96h" if hld >= 95 else "open"
    if reason == "open":
        return None
    exit_ts = int(s["close_ts"][exit_idx])
    t0 = int(s["close_ts"][i])
    raw_end = side * (exit_px / entry - 1.0)
    raw = (1.0 - locked) * raw_end + locked * lock_move
    f_adj = (1.0 - locked) * fund_adj(s, side, t0, exit_ts) + locked * fund_adj(s, side, t0, lock_ts)
    net = raw + f_adj - sp.TAKER_FEE
    if pyr:
        pyr_raw = (1.0 - locked) * (raw_end - pyr_move) + locked * (lock_move - pyr_move)
        pyr_net = 0.50 * (pyr_raw + 0.5 * f_adj - sp.TAKER_FEE)
    else:
        pyr_net = 0.0
    return {
        "exit_ts": exit_ts, "hold_h": int(exit_idx - i),
        "entry_px": round(entry, 6), "exit_px": round(exit_px, 6),
        "net": float(net), "pyr_net": float(pyr_net),
        "min_move": float(min_move), "atr": float(atr),
        "reason": reason, "pyr": bool(pyr),
        "peak_move": float(peak_wick), "exit_move": float(raw),
        "missed_bars": missed, "locked": locked,
    }


class Pol:
    def __init__(self, name, fn, partial=None, hard_wick=True):
        self.name = name
        self.fn = fn
        self.partial_at = partial
        self.hard_wick = hard_wick

    def __call__(self, h, move_adv, peak, atr, pyr):
        return self.fn(h, move_adv, peak, atr, pyr)


def no_trail(h, move, peak, atr, pyr):
    return None


def monster(arm, give):
    def fn(h, move, peak, atr, pyr):
        if peak >= arm * atr and move <= peak - give * atr:
            floor = 1.15 * atr if pyr else 0.4 * atr
            return (max(peak - give * atr, floor), "trail")
        return None
    return fn


POLICIES = [
    Pol("close_base", no_trail, hard_wick=False),
    Pol("wick_hard_base", no_trail, hard_wick=True),
    Pol("arm6_give2.5", monster(6, 2.5)),
    Pol("arm6_give3.5", monster(6, 3.5)),
    Pol("arm8_give3", monster(8, 3.0)),
    Pol("arm8_give4", monster(8, 4.0)),
    Pol("arm10_give3", monster(10, 3.0)),
    Pol("part30_at4", no_trail, (0.30, 4.0)),
    Pol("part40_at6", no_trail, (0.40, 6.0)),
    Pol("part25_at8", no_trail, (0.25, 8.0)),
    Pol("part50_at4", no_trail, (0.50, 4.0)),
]


def with_hot(cands):
    sig_hist = {s: deque(maxlen=6) for s in sp.ALTS_7}
    sig_lstreak = {s: 0 for s in sp.ALTS_7}
    pend = []
    out = []
    for e in sorted(cands, key=lambda x: x["t"]):
        t = e["t"]
        still = []
        for ex_t, sym_p, tot_p in pend:
            if ex_t <= t:
                sig_hist[sym_p].append(tot_p)
                sig_lstreak[sym_p] = sig_lstreak[sym_p] + 1 if tot_p < 0 else 0
            else:
                still.append((ex_t, sym_p, tot_p))
        pend = still
        ch = sig_hist[e["sym"]]
        e = dict(e)
        e["hot_n"] = len(ch)
        e["hot_avg"] = float(np.mean(ch)) if ch else 0.0
        e["c_lstreak"] = sig_lstreak[e["sym"]]
        e["ci"] = sp.COIN_IDX[e["sym"]]
        e["coin"] = sp.SHORT_NAME[e["sym"]]
        e["path"] = []
        pend.append((e["exit_ts"], e["sym"], e["net"] + 1.5 * e["pyr_net"]))
        out.append(e)
    return out


def run_window(cands, cut, before=None):
    part = [c for c in cands if c["t"] >= cut and (before is None or c["exit_ts"] < before)]
    res = sp.simulate_window(part)
    by = {(c["t"], c["sym"]): c for c in part}
    give = []
    for t in res["trades"]:
        src = by.get((t["t"], t["sym"]))
        if not src:
            continue
        pk = src["peak_move"]
        if pk >= 2.0 * src["atr"] and pk > 1e-8:
            give.append(src["exit_move"] / pk)
    return {
        "n": res["n"], "wins": res["wins"],
        "wr": round(100.0 * res["wins"] / max(1, res["n"]), 1),
        "ret": res["ret"], "dd": res["dd"], "final": res["final"],
        "capture_med": round(float(np.median(give)) * 100, 1) if give else None,
        "big_n": len(give),
    }


def main():
    entries, series = collect_entries()
    print("entries", len(entries), flush=True)
    ohlc = {}
    for sym in sp.ALTS_7:
        s = series[sym]
        ohlc[sym] = fetch_ohlc(sym, int(s["close_ts"][240]) - sp.HOUR, int(s["close_ts"][-1]) - sp.HOUR)
    rows = []
    is_cut = sp.Q_CUT
    for pol in POLICIES:
        cands = []
        missed = 0
        for e in entries:
            ex = apply_exit(series[e["sym"]], ohlc[e["sym"]], e["i"], e["side"], pol, SLIP, pol.hard_wick)
            if ex is None:
                continue
            missed += ex["missed_bars"]
            cands.append({"t": e["t"], "sym": e["sym"], "side": e["side"], **ex})
        cands = with_hot(cands)
        ins = run_window(cands, 0, is_cut)
        oos = run_window(cands, is_cut, None)
        print(pol.name, "IS", ins["ret"], ins["dd"], "cap", ins["capture_med"], "OOS", oos["ret"], oos["dd"], "cap", oos["capture_med"], "miss", missed, flush=True)
        rows.append({"name": pol.name, "is": ins, "oos": oos, "missed_bars": missed})
    base = next(r for r in rows if r['name']=='wick_hard_base')
    ranked = []
    for r in rows:
        if r["name"] in ("close_base", "wick_hard_base"):
            continue
        if r["is"]["n"] < 40 or r["is"]["dd"] > base["is"]["dd"] + 8:
            continue
        score = r["is"]["ret"] / max(r["is"]["dd"], 5)
        ranked.append((score, r))
    ranked.sort(key=lambda x: x[0], reverse=True)
    payload = {
        "slip_bps_per_fill": 3,
        "model": "3bps slippage each fill, taker 0.11% RT, same margin. close_base = production timing. wick_hard = resting -2.2 ATR on the hourly extreme. trails and partials are resting orders on the extreme; bail, floor and EMA stay on the hourly close.",
        "is_rule": "exit before 2026-07-22; score=IS ret/max(dd,5); drop if IS dd>base+8",
        "base": base,
        "ranked": [{"score": round(s, 3), **r} for s, r in ranked],
        "all": rows,
        "next": "If no OOS beat of wick_base by >=10% relative with capture up, test 5m hard-stop path on the quarter only for the least-bad monster trail.",
    }
    OUTP.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUTP, flush=True)


if __name__ == "__main__":
    main()
