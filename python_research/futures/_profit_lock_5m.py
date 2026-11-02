# -*- coding: utf-8 -*-
"""Quarter replay on 5-minute last prices, same timing as the live cron."""
import json, time, urllib.request
from pathlib import Path
import _profit_lock as L
import schwartz_production as sp

FIVE = 5 * 60 * 1000
SLIP = L.SLIP
CACHE = L.CACHE
PARTIAL = (0.25, 8.0)


def fetch_5m(sym, start_ms, end_ms):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (sym + "_5m.json")
    data = {}
    if path.exists():
        data = {int(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    have = [k for k in data if start_ms <= k <= end_ms]
    span = max(1, int((end_ms - start_ms) / FIVE))
    if len(have) > span * 0.9:
        print(sym, "5m cache", len(have), flush=True)
        return data
    cur_end = end_ms
    pages = 0
    while cur_end > start_ms and pages < 40:
        payload = None
        last_err = None
        for host in ("https://api.bybit.com", "https://api.bytick.com"):
            url = host + "/v5/market/kline?category=linear&symbol=" + sym + "&interval=5&limit=1000&end=" + str(cur_end)
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url, timeout=20) as r:
                        payload = json.loads(r.read().decode())
                    break
                except Exception as exc:
                    last_err = exc
                    time.sleep(0.5 * (attempt + 1))
            if payload is not None and int(payload.get("retCode") or 0) == 0:
                break
            payload = None
        if payload is None:
            raise RuntimeError(sym + " " + str(last_err))
        rows = (payload.get("result") or {}).get("list") or []
        if not rows:
            break
        oldest = None
        for row in rows:
            ts = int(row[0])
            oldest = ts if oldest is None else min(oldest, ts)
            data[ts] = float(row[4])
        pages += 1
        nxt = oldest - 1
        if nxt >= cur_end:
            break
        cur_end = nxt
        time.sleep(0.05)
    path.write_text(json.dumps({str(k): v for k, v in data.items()}), encoding="utf-8")
    print(sym, "5m", len(data), "pages", pages, flush=True)
    return data


def replay(s, bars, i, side, partial):
    px, ema, cts = s["px"], s["ema240"], s["close_ts"]
    entry_ts = int(cts[i])
    entry = float(px[i]) * (1.0 + side * SLIP)
    atr = max(float(s["atr1h"][i]), 0.002)
    end_ts = entry_ts + 96 * sp.HOUR
    keys = [t for t in bars if entry_ts <= t + FIVE <= end_ts + FIVE]
    keys.sort()
    if len(keys) < 100:
        return None
    peak = 0.0
    min_move = 0.0
    pyr = False
    pyr_move = 0.0
    locked = 0.0
    lock_move = 0.0
    lock_ts = entry_ts
    reason = "end_96h"
    exit_px = entry
    exit_ts = end_ts
    for t in keys:
        c = bars[t]
        bar_close = t + FIVE
        if bar_close <= entry_ts or bar_close > end_ts:
            continue
        move = side * (c / entry - 1.0)
        min_move = min(min_move, move)
        peak = max(peak, move)
        held_h = (bar_close - entry_ts) / sp.HOUR
        hourly = bar_close % sp.HOUR == 0
        hit = None
        if move <= -2.2 * atr:
            hit = "hard_stop"
        elif held_h >= 96.0 - 1e-9:
            hit = "end_96h"
        elif hourly and 0.8 <= held_h <= 4.5 and move <= -0.8 * atr:
            hit = "fast_bail"
        elif hourly and peak >= 3.0 * atr and move <= (1.15 * atr if pyr else 0.4 * atr):
            hit = "breakeven"
        elif hourly:
            k = int(s["close_ts"].searchsorted(bar_close)) if hasattr(s["close_ts"], "searchsorted") else None
            import numpy as np
            k = int(np.searchsorted(cts, bar_close))
            if k < len(cts) and int(cts[k]) == bar_close:
                emax = float(ema[k])
                if (side == 1 and c < emax) or (side == -1 and c > emax):
                    hit = "ema10_exit"
        if hit:
            exit_px = c * (1.0 - side * SLIP)
            exit_ts = bar_close
            reason = hit
            break
        if (not pyr) and move >= 2.0 * atr:
            pyr = True
            pyr_move = move
        if partial and locked == 0.0 and move >= partial[1] * atr:
            locked = partial[0]
            fill = c * (1.0 - side * SLIP)
            lock_move = side * (fill / entry - 1.0)
            lock_ts = bar_close
        exit_px = c * (1.0 - side * SLIP)
        exit_ts = bar_close
    raw_end = side * (exit_px / entry - 1.0)
    raw = (1.0 - locked) * raw_end + locked * lock_move
    f_adj = (1.0 - locked) * L.fund_adj(s, side, entry_ts, exit_ts) + locked * L.fund_adj(s, side, entry_ts, lock_ts)
    net = raw + f_adj - sp.TAKER_FEE
    pyr_net = 0.50 * ((1.0 - locked) * (raw_end - pyr_move) + locked * (lock_move - pyr_move) + 0.5 * f_adj - sp.TAKER_FEE) if pyr else 0.0
    return {
        "exit_ts": int(exit_ts), "hold_h": int(round((exit_ts - entry_ts) / sp.HOUR)),
        "entry_px": round(entry, 6), "exit_px": round(exit_px, 6),
        "net": float(net), "pyr_net": float(pyr_net), "min_move": float(min_move),
        "atr": float(atr), "reason": reason, "pyr": bool(pyr),
        "peak_move": float(peak), "exit_move": float(raw), "missed_bars": 0, "locked": locked,
        "ci": sp.COIN_IDX[s["sym"]] if "sym" in s else 0,
    }


def run(name, entries, series, bars, ohlc, partial):
    close_pol = next(p for p in L.POLICIES if p.name == "close_base")
    cands = []
    for e in entries:
        sym = e["sym"]
        if e["t"] < sp.Q_CUT:
            ex = L.apply_exit(series[sym], ohlc[sym], e["i"], e["side"], close_pol, SLIP, False)
        else:
            ex = replay(series[sym], bars[sym], e["i"], e["side"], partial)
        if ex is None:
            continue
        cands.append({"t": e["t"], "sym": sym, "side": e["side"], **ex})
    cands = L.with_hot(cands)
    oos = L.run_window(cands, sp.Q_CUT)
    reasons = {}
    for c in cands:
        if c["t"] >= sp.Q_CUT:
            reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
    print(name, "OOS", oos["ret"], "dd", oos["dd"], "n", oos["n"], "wr", oos["wr"], "cap", oos["capture_med"], reasons, flush=True)
    return {"name": name, "oos": oos, "reasons": reasons}


def main():
    entries, series = L.collect_entries()
    oos_entries = [e for e in entries if e["t"] >= sp.Q_CUT]
    start = sp.Q_CUT - sp.DAY
    end = max(int(series[e["sym"]]["close_ts"][min(len(series[e["sym"]]["close_ts"]) - 1, e["i"] + 96)]) for e in oos_entries)
    end = max(end, int(series["ETHUSDT"]["close_ts"][-1]))
    print("oos entries", len(oos_entries), "window", sp.iso(start), sp.iso(end), flush=True)
    bars = {sym: fetch_5m(sym, start, end) for sym in sp.ALTS_7}
    ohlc = {}
    for sym in sp.ALTS_7:
        p = CACHE / (sym + ".json")
        ohlc[sym] = {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()} if p.exists() else {}
    rows = [
        run("5m_cron", entries, series, bars, ohlc, None),
        run("5m_part25_at8", entries, series, bars, ohlc, PARTIAL),
    ]
    out = Path(__file__).resolve().parent / "_profit_lock_5m.json"
    out.write_text(json.dumps({"rows": rows, "hourly_close_base_oos": 167.94}, ensure_ascii=False), encoding="utf-8")
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
