"""Futures funding: 5 coins, MULTI-position (one per coin, up to 5), leverage, $2000.
Also tracks how many positions are open at once and a per-month trade table.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

LAB = Path(__file__).resolve().parent
HOUR = 3_600_000
DAY = 24 * HOUR
FEE = 0.0015
COINS = ["ADAUSDT","SOLUSDT","ETHUSDT","SUIUSDT"]  # top-4, LINK dropped

def load_rows():
    data = json.loads((LAB/"futures_dataset.json").read_text(encoding="utf-8"))
    rows = []
    for ci, sym in enumerate(COINS):
        for t, feat, ret, paid, z in data[sym]:
            rows.append((ci, int(t), np.array(feat, dtype=np.float64), float(ret), float(paid), float(z)))
    rows.sort(key=lambda r: r[1])
    return rows

def fit_gb(X, y, w):
    m = HistGradientBoostingRegressor(max_iter=80, learning_rate=0.06, max_depth=3,
                                      min_samples_leaf=80, l2_regularization=1.0,
                                      early_stopping=False, random_state=0)
    m.fit(X, y, sample_weight=w)
    return m

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    window = int(args.get("--window", 180))
    pk = float(args.get("--pk", 0.5))
    leverage = float(args.get("--leverage", 3.0))
    stake = float(args.get("--stake", 0.2))
    thr = float(args.get("--thr", 0.006))
    start_bal = float(args.get("--start", 2000.0))
    tag = args.get("--tag", "f5m")

    rows = load_rows()
    ts = np.array([r[1] for r in rows], dtype=np.int64)
    first = int(ts[0]); last = int(ts[-1])
    day0 = (first // DAY + 1) * DAY
    starts = list(range(day0, last, DAY))
    print(f"5multi lev={leverage}x stake={stake} thr={thr} start=2000 days {len(starts)}", flush=True)

    balance = start_bal
    busy = {}  # coin -> free_ts
    month_bal = {}
    month_tbl = {}  # month -> [n, n_long, n_short, sum_pnl_pct]
    concurrency = []
    trades = []
    t0 = time.time()
    for k, d in enumerate(starts):
        d_end = d + DAY
        tr_idx = np.flatnonzero((ts >= d - window * DAY) & (ts < d))
        te_idx = np.flatnonzero((ts >= d) & (ts < d_end))
        if len(tr_idx) < 400 or len(te_idx) == 0:
            continue
        X, y, w = [], [], []
        by_coin = {}
        for i in tr_idx:
            by_coin.setdefault(rows[i][0], []).append(i)
        for ci, idxs in by_coin.items():
            idxs = sorted(idxs, key=lambda i: rows[i][1])
            streak = 0
            for i in idxs:
                r = rows[i]
                net_l = r[3] - r[4] - FEE
                net_s = -r[3] + r[4] - FEE
                best = max(net_l, net_s)
                streak = streak + 1 if best < 0 else 0
                wt = 1.0 + pk * min(streak, 4) if best < 0 else 1.0
                for side, net in ((1, net_l), (0, net_s)):
                    X.append(np.append(r[2], side)); y.append(net); w.append(wt)
        if len(X) < 800:
            continue
        model = fit_gb(np.vstack(X), np.array(y), np.array(w))
        # evaluate every test row; one position per coin (no global single slot)
        cands = []
        for i in te_idx:
            r = rows[i]
            f = r[2]
            net_l = float(model.predict(np.append(f, 1.0).reshape(1, -1))[0])
            net_s = float(model.predict(np.append(f, 0.0).reshape(1, -1))[0])
            side = 1 if net_l >= net_s else -1
            pred = max(net_l, net_s)
            if pred > thr:
                cands.append((pred, i, side))
        cands.sort(reverse=True)
        for pred, i, side in cands:
            r = rows[i]; ci = r[0]; t = int(r[1])
            # prune expired busy
            busy = {c: ex for c, ex in busy.items() if ex > t}
            if ci in busy:
                continue  # one position per coin
            actual = (r[3] - r[4] - FEE) if side == 1 else (-r[3] + r[4] - FEE)
            if actual * leverage <= -1.0:
                contrib = -stake
            else:
                contrib = stake * leverage * actual
            balance *= 1.0 + contrib
            busy[ci] = t + DAY
            concurrency.append(len(busy))  # open positions after this entry
            trades.append((t, ci, side, contrib, balance))
            m = time.strftime("%Y-%m", time.gmtime((t + DAY) / 1000))
            month_bal[m] = round(balance, 2)
            e = month_tbl.setdefault(m, [0, 0, 0, 0.0])
            e[0] += 1; e[1] += (1 if side == 1 else 0); e[2] += (1 if side == -1 else 0); e[3] += contrib
            if balance <= 0:
                print("RUINED at", time.strftime("%Y-%m-%d", time.gmtime(t/1000)), flush=True)
                break
        if balance <= 0:
            break
        if (k + 1) % 60 == 0:
            el = time.time() - t0
            print(f"{k+1}/{len(starts)} {time.strftime("%Y-%m-%d", time.gmtime(d/1000))} bal={balance:.0f} trades={len(trades)} open={len(busy)} {el/(k+1):.2f}s/day eta {el/(k+1)*(len(starts)-k-1)/60:.0f}m", flush=True)
    print(f"train done {time.time()-t0:.0f}s", flush=True)

    total = round((balance / start_bal - 1.0) * 100, 2)
    longs = sum(1 for x in trades if x[2] == 1)
    conc = np.array(concurrency) if concurrency else np.array([0])
    print(f"BALANCE {balance:.2f} from {start_bal:.0f} ({total:+.2f}%) trades={len(trades)} long={longs} short={len(trades)-longs}", flush=True)
    print(f"CONCURRENT max={int(conc.max())} mean={conc.mean():.2f} dist={np.bincount(conc.astype(int))}", flush=True)
    print("MONTH  trades  long short  pnl%", flush=True)
    for m in sorted(month_tbl):
        n, nl, ns, sp = month_tbl[m]
        print(f"{m}  {n:3d}  {nl:3d}  {ns:3d}  {sp*100:+.1f}%", flush=True)
    for m in sorted(month_bal):
        print(f"{m} ${month_bal[m]:,.2f}", flush=True)
    pnl_by_coin = np.zeros(len(COINS)); cnt_by_coin = np.zeros(len(COINS), dtype=int)
    for t, ci, side, contrib, bal in trades:
        cnt_by_coin[ci] += 1; pnl_by_coin[ci] += contrib
    payload = {
        "months": sorted(month_bal), "balance": [month_bal[m] for m in sorted(month_bal)],
        "final": round(balance, 2), "start": start_bal, "taken": len(trades),
        "leverage": leverage, "stake": stake, "longs": longs, "shorts": len(trades)-longs,
        "concurrency": {"max": int(conc.max()), "mean": round(float(conc.mean()), 2),
                        "hist": {str(i): int(v) for i, v in enumerate(np.bincount(conc.astype(int))) if v}},
        "coins": [c.replace("USDT","") for c in COINS],
        "pnl": {c.replace("USDT",""): round(float(pnl_by_coin[i]*100), 2) for i, c in enumerate(COINS)},
        "counts": {c.replace("USDT",""): int(cnt_by_coin[i]) for i, c in enumerate(COINS)},
        "month_tbl": {m: [int(x[0]), int(x[1]), int(x[2]), round(float(x[3])*100, 2)] for m, x in sorted(month_tbl.items())},
        "trades": [[int(t), int(ci), int(side), round(float(contrib), 4)] for t, ci, side, contrib, bal in trades],
    }
    out = LAB / f"stable_{tag}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    print("saved", out, flush=True)

if __name__ == "__main__":
    main()