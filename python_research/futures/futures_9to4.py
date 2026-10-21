"""Walk-forward backtest. No same-bar close and no open-trade labels in training.

Each calendar month the menu is scored on the previous 180 days, using only
trades that had already closed. The winner trades the next month. PnL hits
equity at the exit, not at the entry.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from causal import COINS, DAY, LAB, TRADE_IDX
from portfolio import simulate
from signals import MENU, apply_gb, decide, fit_gb, spec_name

WINDOW_MS = 180 * DAY


def load_rows():
    data = json.loads((LAB / "futures_dataset.json").read_text(encoding="utf-8"))
    rows = []
    for ci, sym in enumerate(COINS):
        for raw in data[sym]:
            t, feat, ret, paid, z, min_ret, max_ret, btc_r1d, btc_r7d = raw
            rows.append({
                "ci": ci,
                "t": int(t),
                "feat": np.array(feat, dtype=np.float64),
                "ret": float(ret),
                "paid": float(paid),
                "z": float(z),
                "min_ret": float(min_ret),
                "max_ret": float(max_ret),
                "btc_r1d": float(btc_r1d),
                "btc_r7d": float(btc_r7d),
            })
    rows.sort(key=lambda r: (r["t"], r["ci"]))
    return rows


def month_starts(t0: int, t1: int):
    d = datetime.fromtimestamp(t0 / 1000, timezone.utc)
    y, m = d.year, d.month
    out = []
    while True:
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        ms = int(start.timestamp() * 1000)
        if ms > t1:
            break
        if ms >= t0:
            out.append(ms)
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def _score_value(result, spec) -> float:
    if spec[0] == "flat":
        return 0.0
    if result["n"] < 8 or result["max_dd"] > 0.55:
        return -1e9
    return result["return"] - 0.5 * result["max_dd"]


def score_spec(spec, train_all, train_trade, leverage, stake):
    if spec[0] == "gb":
        ordered = sorted(train_all, key=lambda r: r["t"])
        if len(ordered) < 900:
            return -1e9
        cut_t = ordered[int(len(ordered) * 0.7)]["t"]
        fit_rows = [r for r in train_all if r["t"] + DAY <= cut_t]
        val = [dict(r) for r in train_trade if r["t"] >= cut_t]
        model = fit_gb(fit_rows)
        if model is None or len(val) < 20:
            return -1e9
        apply_gb(model, val, thr=float(spec[1]))
        result = simulate(val, lambda r, s=spec: decide(s, r), leverage, stake)
        return _score_value(result, spec)
    result = simulate(train_trade, lambda r, s=spec: decide(s, r), leverage, stake)
    return _score_value(result, spec)


def select(train_all, train_trade, menu, leverage, stake):
    best, best_score = ("flat",), 0.0
    for spec in menu:
        sc = score_spec(spec, train_all, train_trade, leverage, stake)
        if sc > best_score:
            best, best_score = spec, sc
    return best, best_score


def half_year_returns(curve, start_equity: float):
    """Equity change inside each UTC half-year, chained from the running equity."""
    buckets = {}
    prev_eq = start_equity
    prev_key = None
    for ts, eq in curve:
        if ts <= 0:
            continue
        d = datetime.fromtimestamp(ts / 1000, timezone.utc)
        key = f"{d.year}H{1 if d.month <= 6 else 2}"
        if prev_key is None:
            prev_key = key
        if key != prev_key:
            buckets.setdefault(prev_key, prev_eq)
            prev_key = key
        buckets[key] = eq
        prev_eq = eq
    # Rebuild start-of-bucket equity.
    keys = sorted(buckets)
    out = {}
    eq = start_equity
    seen = {}
    last = start_equity
    current = None
    start_eq = start_equity
    for ts, eqv in curve:
        if ts <= 0:
            continue
        d = datetime.fromtimestamp(ts / 1000, timezone.utc)
        key = f"{d.year}H{1 if d.month <= 6 else 2}"
        if key not in seen:
            seen[key] = last
        last = eqv
        current = key
    for key, start_e in seen.items():
        end_e = buckets[key]
        out[key] = round((end_e / start_e - 1.0) * 100, 2) if start_e else 0.0
    return out


def run(rows, leverage, stake, start, recent_days: int, with_gb: bool, tag: str):
    t_max = max(r["t"] for r in rows)
    t_min = min(r["t"] for r in rows)
    months = month_starts(t_min + WINDOW_MS, t_max)
    if recent_days:
        months = [m for m in months if m >= t_max - recent_days * DAY]
    if not months:
        raise SystemExit("no test months")
    menu = [s for s in MENU if with_gb or s[0] != "gb"]
    trade_rows = [r for r in rows if r["ci"] in TRADE_IDX]
    chosen = {}
    gb_models = {}
    t0 = time.time()
    for i, m in enumerate(months):
        train_all = [r for r in rows if m - WINDOW_MS <= r["t"] and r["t"] + DAY <= m]
        train_trade = [r for r in train_all if r["ci"] in TRADE_IDX]
        # Guard: a training label must be finished before the test month starts.
        if train_all and max(r["t"] for r in train_all) + DAY > m:
            raise SystemExit("look-ahead in training window")
        spec, sc = select(train_all, train_trade, menu, leverage, stake)
        chosen[m] = spec
        if spec[0] == "gb":
            gb_models[m] = fit_gb(train_all)
        if (i + 1) % 6 == 0 or i == 0:
            stamp = datetime.fromtimestamp(m / 1000, timezone.utc).strftime("%Y-%m")
            print(f"select {stamp} -> {spec_name(spec)} score={sc:.3f} ({time.time()-t0:.0f}s)", flush=True)

    for i, m in enumerate(months):
        m2 = months[i + 1] if i + 1 < len(months) else t_max + DAY
        if m not in gb_models:
            continue
        seg = [r for r in trade_rows if m <= r["t"] < m2]
        apply_gb(gb_models[m], seg, thr=0.006)

    def month_key(ts: int) -> int:
        prev = months[0]
        for m in months:
            if m <= ts:
                prev = m
            else:
                break
        return prev

    test = [r for r in trade_rows if r["t"] >= months[0]]

    def choose(row, _chosen=chosen, _month_key=month_key):
        spec = _chosen[_month_key(row["t"])]
        return decide(spec, row)

    result = simulate(test, choose, leverage, stake, start)
    halves = half_year_returns(result["curve"], start)
    last_cut = t_max - 90 * DAY
    recent = [tr for tr in result["trades"] if tr["exit"] >= last_cut]
    # Recent equity change: from equity just before the first recent exit.
    recent_ret = None
    if result["curve"]:
        base = start
        for ts, eq in result["curve"]:
            if ts < last_cut:
                base = eq
        recent_ret = result["final"] / base - 1.0 if base else 0.0
    payload = {
        "final": round(result["final"], 2),
        "start": start,
        "return_pct": round(result["return"] * 100, 2),
        "max_dd_pct": round(result["max_dd"] * 100, 2),
        "taken": result["n"],
        "leverage": leverage,
        "stake": stake,
        "half_years": halves,
        "recent90_pct": None if recent_ret is None else round(recent_ret * 100, 2),
        "selections": {str(m): spec_name(chosen[m]) for m in months},
        "live_spec": list(chosen[months[-1]]),
        "live_month": months[-1],
        "longs": sum(1 for tr in result["trades"] if tr["side"] == 1),
        "shorts": sum(1 for tr in result["trades"] if tr["side"] == -1),
        "equity_pct": [
            [datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M"),
             round((eq / start - 1.0) * 100, 2)]
            for ts, eq in result["curve"] if ts > 0
        ],
        "trades": [
            [int(tr["t"]), int(tr["ci"]), int(tr["side"]), round(float(tr["contrib"]), 6)]
            for tr in result["trades"]
        ],
        "causal": True,
    }
    out = LAB / f"stable_{tag}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    (LAB / "live_params.json").write_text(json.dumps({
        "spec": payload["live_spec"],
        "month": payload["live_month"],
        "leverage": leverage,
        "stake": stake,
        "tag": tag,
    }), encoding="utf-8")
    print(
        f"BALANCE {payload['final']:.2f} from {start:.0f} ({payload['return_pct']:+.2f}%) "
        f"dd={payload['max_dd_pct']:.1f}% trades={payload['taken']} recent90={payload['recent90_pct']}",
        flush=True,
    )
    print("HALF", " ".join(f"{k}:{v:+.1f}%" for k, v in halves.items()), flush=True)
    print("saved", out, flush=True)
    return payload


def parse_spec(text: str):
    kind, zthr, trend = text.split(":")
    return (kind, float(zthr), trend)


def run_fixed(rows, spec, leverage, stake, start, recent_days: int, tag: str):
    """One frozen rule. Nothing is chosen on the test window."""
    t_max = max(r["t"] for r in rows)
    trade_rows = [r for r in rows if r["ci"] in TRADE_IDX]
    if recent_days:
        trade_rows = [r for r in trade_rows if r["t"] >= t_max - recent_days * DAY]
    result = simulate(trade_rows, lambda r, s=spec: decide(s, r), leverage, stake, start)
    halves = half_year_returns(result["curve"], start)
    last_cut = t_max - 90 * DAY
    recent_ret = None
    if result["curve"]:
        base = start
        for ts, eq in result["curve"]:
            if ts < last_cut:
                base = eq
        recent_ret = result["final"] / base - 1.0 if base else 0.0
    payload = {
        "final": round(result["final"], 2),
        "start": start,
        "return_pct": round(result["return"] * 100, 2),
        "max_dd_pct": round(result["max_dd"] * 100, 2),
        "taken": result["n"],
        "leverage": leverage,
        "stake": stake,
        "half_years": halves,
        "recent90_pct": None if recent_ret is None else round(recent_ret * 100, 2),
        "live_spec": list(spec),
        "spec": spec_name(spec),
        "longs": sum(1 for tr in result["trades"] if tr["side"] == 1),
        "shorts": sum(1 for tr in result["trades"] if tr["side"] == -1),
        "equity_pct": [
            [datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M"),
             round((eq / start - 1.0) * 100, 2)]
            for ts, eq in result["curve"] if ts > 0
        ],
        "trades": [
            [int(tr["t"]), int(tr["ci"]), int(tr["side"]), round(float(tr["contrib"]), 6)]
            for tr in result["trades"]
        ],
        "causal": True,
        "mode": "fixed",
    }
    out = LAB / f"stable_{tag}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    (LAB / "live_params.json").write_text(json.dumps({
        "spec": payload["live_spec"],
        "leverage": leverage,
        "stake": stake,
        "tag": tag,
    }), encoding="utf-8")
    print(
        f"FIXED {spec_name(spec)} BALANCE {payload['final']:.2f} from {start:.0f} "
        f"({payload['return_pct']:+.2f}%) dd={payload['max_dd_pct']:.1f}% "
        f"trades={payload['taken']} recent90={payload['recent90_pct']}",
        flush=True,
    )
    print("HALF", " ".join(f"{k}:{v:+.1f}%" for k, v in halves.items()), flush=True)
    print("saved", out, flush=True)
    return payload


def run_daily(rows, leverage, stake, start, recent_days: int, tag: str, thr: float = 0.006, max_days: int = 0, exit_mode: str = "hold", write_live: bool = False):
    """One new model every UTC day.

    The fit uses only trades whose 24h exit is already known. Sample weight
    rises after losses that had closed before that sample's entry, so a bad
    streak counts on the next lesson and not on the trade that just opened.
    """
    t_min = min(r["t"] for r in rows)
    t_max = max(r["t"] for r in rows)
    ts = np.array([r["t"] for r in rows], dtype=np.int64)
    day0 = (t_min // DAY + 1) * DAY
    days = list(range(day0 + WINDOW_MS, t_max, DAY))
    if recent_days:
        days = [d for d in days if d >= t_max - recent_days * DAY]
    if max_days:
        days = days[:max_days]
    if not days:
        raise SystemExit("no training days")
    if exit_mode == "schwartz":
        from discipline import apply_schwartz
        st = apply_schwartz(rows)
        print(f"SCHWARTZ 10d MA early_exits={st['early_exit_pct']}% legs={st['legs']}", flush=True)
    print(
        f"DAILY fits={len(days)} window=180d thr={thr} lev={leverage} stake={stake} "
        f"from {datetime.fromtimestamp(days[0]/1000, timezone.utc):%Y-%m-%d}",
        flush=True,
    )
    scored = []
    fitted = 0
    skipped = 0
    t0 = time.time()
    spec = ("gb", thr)
    for i, d in enumerate(days):
        lo = int(np.searchsorted(ts, d - WINDOW_MS, side="left"))
        hi = int(np.searchsorted(ts, d - DAY, side="right"))
        train = rows[lo:hi]
        if train and int(train[-1]["t"]) + DAY > d:
            raise SystemExit("training row still open at day start")
        model = fit_gb(train)
        d2 = d + DAY
        a = int(np.searchsorted(ts, d, side="left"))
        b = int(np.searchsorted(ts, d2, side="left"))
        test = [r for r in rows[a:b] if r["ci"] in TRADE_IDX]
        if model is None:
            skipped += 1
        else:
            fitted += 1
            apply_gb(model, test, thr=thr)
            scored.extend(test)
        if i == 0 or (i + 1) % 10 == 0 or i + 1 == len(days):
            el = time.time() - t0
            left = el / (i + 1) * (len(days) - i - 1)
            stamp = datetime.fromtimestamp(d / 1000, timezone.utc).strftime("%Y-%m-%d")
            print(
                f"day {i+1}/{len(days)} {stamp} train={len(train)} test={len(test)} "
                f"fitted={fitted} skip={skipped} {el:.0f}s eta {left/60:.1f}m",
                flush=True,
            )
    result = simulate(scored, lambda r, s=spec: decide(s, r), leverage, stake, start)
    halves = half_year_returns(result["curve"], start)
    last_cut = t_max - 90 * DAY
    base = start
    for ts_i, eq in result["curve"]:
        if ts_i < last_cut:
            base = eq
    recent_ret = result["final"] / base - 1.0 if base else 0.0
    years = max((t_max - days[0]) / (365.25 * DAY), 1e-9)
    cagr = (result["final"] / start) ** (1 / years) - 1 if result["final"] > 0 else -1
    payload = {
        "final": round(result["final"], 2),
        "start": start,
        "return_pct": round(result["return"] * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_dd_pct": round(result["max_dd"] * 100, 2),
        "taken": result["n"],
        "fitted_days": fitted,
        "skipped_days": skipped,
        "leverage": leverage,
        "stake": stake,
        "half_years": halves,
        "recent90_pct": round(recent_ret * 100, 2),
        "live_spec": ["gb", thr],
        "longs": sum(1 for tr in result["trades"] if tr["side"] == 1),
        "shorts": sum(1 for tr in result["trades"] if tr["side"] == -1),
        "equity_pct": [
            [datetime.fromtimestamp(ts_i / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M"),
             round((eq / start - 1.0) * 100, 2)]
            for ts_i, eq in result["curve"] if ts_i > 0
        ],
        "trades": [
            [int(tr["t"]), int(tr["ci"]), int(tr["side"]), round(float(tr["contrib"]), 6)]
            for tr in result["trades"]
        ],
        "causal": True,
        "mode": "daily",
    }
    out = LAB / f"stable_{tag}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    if write_live:
        (LAB / "live_params.json").write_text(json.dumps({
            "spec": payload["live_spec"],
            "leverage": leverage,
            "stake": stake,
            "tag": tag,
        }), encoding="utf-8")
    if result["trades"]:
        early = sum(1 for tr in result["trades"] if tr["exit"] - tr["t"] < DAY)
        print(f"early exits {early}/{len(result['trades'])}", flush=True)
    print(
        f"DAILY BALANCE {payload['final']:.2f} from {start:.0f} ({payload['return_pct']:+.2f}%) "
        f"CAGR={payload['cagr_pct']:+.1f}% dd={payload['max_dd_pct']:.1f}% "
        f"trades={payload['taken']} fits={fitted} recent90={payload['recent90_pct']}",
        flush=True,
    )
    print("HALF", " ".join(f"{k}:{v:+.1f}%" for k, v in halves.items()), flush=True)
    print("saved", out, flush=True)
    return payload


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    mode = args.get("--mode", "daily")
    leverage = float(args.get("--leverage", 1.0))
    stake = float(args.get("--stake", 0.2))
    start = float(args.get("--start", 2000.0))
    tag = args.get("--tag", "causal")
    recent_days = int(args.get("--recent-days", 0))
    rows = load_rows()
    if mode == "fixed":
        spec = parse_spec(args.get("--spec", "carry:3:none"))
        print(f"rows={len(rows)} mode=fixed spec={spec_name(spec)} lev={leverage} stake={stake}", flush=True)
        run_fixed(rows, spec, leverage, stake, start, recent_days, tag)
        return
    if mode == "daily":
        thr = float(args.get("--thr", 0.006))
        max_days = int(args.get("--max-days", 0))
        exit_mode = args.get("--exit", "hold")
        print(f"rows={len(rows)} mode=daily lev={leverage} stake={stake} exit={exit_mode}", flush=True)
        run_daily(rows, leverage, stake, start, recent_days, tag, thr=thr, max_days=max_days, exit_mode=exit_mode, write_live=(tag == "causal_full"))
        return
    with_gb = args.get("--with-gb", "0") == "1"
    print(f"rows={len(rows)} mode=select gb={with_gb} recent={recent_days} lev={leverage}", flush=True)
    run(rows, leverage, stake, start, recent_days, with_gb, tag)


if __name__ == "__main__":
    main()
