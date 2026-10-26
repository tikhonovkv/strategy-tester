"""Causal futures rows.

A decision at funding time t may use only information already known at t.
Hourly bars are stored by open time; the close of that bar is known one hour
later. Funding rates at t, t+8h, t+16h and t+24h that have not settled yet
are labels, not features.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

STATE = Path.home() / ".cursor" / "bybit-top25-scan-state"
LAB = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUTPUT_DIR", str(LAB)))
HOUR = 3_600_000
DAY = 24 * HOUR
FEE = 0.0015

COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
]
TRADE_IDX = [1, 2, 5, 8]  # ETH, SOL, ADA, SUI
N_FEAT = 16


def load_coin(sym: str):
    bars, vols, fund = {}, {}, {}
    for base in (LAB / "data", STATE / "pairs_cache", OUT / "data"):
        bp = base / f"{sym}.json"
        if not bp.exists():
            continue
        try:
            blob = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in (blob.get("bars") or {}).items():
            bars[int(k)] = float(v[0])
            vols[int(k)] = float(v[1])
        fund.update({int(k): float(v) for k, v in (blob.get("funding") or {}).items()})
    fp = LAB / "fresh" / f"{sym}_fresh.json"
    if fp.exists():
        fr = json.loads(fp.read_text(encoding="utf-8"))
        for k, v in (fr.get("bars") or {}).items():
            bars[int(k)] = float(v[0])
            vols[int(k)] = float(v[1])
        fund.update({int(k): float(v) for k, v in (fr.get("funding") or {}).items()})
    return bars, vols, fund


def completed_index(bar_ts: np.ndarray, t: int) -> int:
    """Last bar whose close is known at t. Keys are open times."""
    return int(np.searchsorted(bar_ts, t - HOUR, side="right")) - 1


def _features(bar_ts, bar_px, f_ts, f_rate, i: int, t: int):
    bi = completed_index(bar_ts, t)
    if bi < 30 or int(bar_ts[bi]) != t - HOUR:
        return None
    px = float(bar_px[bi])
    if not np.isfinite(px) or px <= 0:
        return None
    hist = f_rate[i - 89:i + 1]
    past = []
    for k in (1, 3, 9, 21, 42, 90):
        j = completed_index(bar_ts, t - k * 8 * HOUR)
        if j < 0:
            return None
        p = float(bar_px[j])
        if not np.isfinite(p) or p <= 0:
            return None
        past.append(p)
    z = float(hist[-1] - hist.mean()) / (float(hist.std()) + 1e-8)
    closes = bar_px[bi - 14:bi + 1]
    diff = np.diff(closes)
    gain = float(np.maximum(diff, 0).mean())
    loss = float(np.maximum(-diff, 0).mean())
    rsi = 100.0 - 100.0 / (1.0 + gain / (loss + 1e-12))
    logr = np.diff(np.log(bar_px[bi - 24:bi + 1]))
    vol24 = float(logr.std()) if len(logr) >= 20 else 0.0
    feat = np.array([
        *f_rate[i - 2:i + 1],
        z,
        float(f_rate[i] - f_rate[i - 1]),
        *[px / p - 1.0 for p in past],
        rsi,
        vol24,
        float(bar_px[bi] / bar_px[bi - 1] - 1.0),
        float(bar_px[bi] / bar_px[bi - 4] - 1.0),
        float(bar_px[bi] / bar_px[bi - 12] - 1.0),
    ], dtype=np.float64)
    if feat.shape != (N_FEAT,):
        raise RuntimeError(f"feature width {feat.shape}")
    return feat, px, z, bi


def _btc_returns(btc_ts, btc_px, t: int):
    bi = completed_index(btc_ts, t)
    if bi < 0 or int(btc_ts[bi]) != t - HOUR:
        return None
    px = float(btc_px[bi])
    j1 = completed_index(btc_ts, t - DAY)
    j7 = completed_index(btc_ts, t - 7 * DAY)
    if j1 < 0 or j7 < 0:
        return None
    p1, p7 = float(btc_px[j1]), float(btc_px[j7])
    if min(px, p1, p7) <= 0:
        return None
    return px / p1 - 1.0, px / p7 - 1.0


def build_rows(sym: str, btc=None, with_labels: bool = True):
    """Rows at each funding time.

    Labels (ret, paid, min_ret, max_ret) look 24h forward and are omitted when
    with_labels is False. Features never use those fields.
    """
    bars, _vols, fund = load_coin(sym)
    if len(bars) < 200 or len(fund) < 100:
        return []
    bar_ts = np.array(sorted(bars), dtype=np.int64)
    bar_px = np.array([bars[int(t)] for t in bar_ts], dtype=np.float64)
    f_ts = np.array(sorted(fund), dtype=np.int64)
    f_rate = np.array([fund[int(t)] for t in f_ts], dtype=np.float64)
    if btc is None and sym != "BTCUSDT":
        btc = _btc_series()
    rows = []
    last = len(f_ts) - (3 if with_labels else 0)
    for i in range(90, last):
        t = int(f_ts[i])
        if with_labels and int(f_ts[i + 3]) - t > 30 * HOUR:
            continue
        built = _features(bar_ts, bar_px, f_ts, f_rate, i, t)
        if built is None:
            continue
        feat, px, z, bi = built
        btc_r = (0.0, 0.0) if btc is None else _btc_returns(btc[0], btc[1], t)
        if btc_r is None:
            continue
        row = {
            "t": t,
            "feat": feat,
            "z": float(z),
            "btc_r1d": float(btc_r[0]),
            "btc_r7d": float(btc_r[1]),
        }
        if not with_labels:
            rows.append(row)
            continue
        be = completed_index(bar_ts, t + DAY)
        if be <= bi or int(bar_ts[be]) != t + DAY - HOUR:
            continue
        path = bar_px[bi + 1:be + 1]
        if path.size < 20:
            continue
        px1 = float(bar_px[be])
        row["ret"] = px1 / px - 1.0
        row["paid"] = float(f_rate[i + 1] + f_rate[i + 2] + f_rate[i + 3])
        row["min_ret"] = float(path.min() / px - 1.0)
        row["max_ret"] = float(path.max() / px - 1.0)
        rows.append(row)
    return rows


def _btc_series():
    bars, _v, _f = load_coin("BTCUSDT")
    ts = np.array(sorted(bars), dtype=np.int64)
    px = np.array([bars[int(t)] for t in ts], dtype=np.float64)
    return ts, px


def build_all(with_labels: bool = True):
    btc = _btc_series()
    out = {}
    for sym in COINS:
        out[sym] = build_rows(sym, btc=btc, with_labels=with_labels)
    return out


def latest_feature(sym: str, now_ms: int, btc=None):
    """Feature row at the latest funding timestamp that has already settled."""
    _bars, _v, fund = load_coin(sym)
    times = [t for t in fund if t <= now_ms]
    if len(times) < 91:
        return None
    t = max(times)
    if btc is None:
        btc = _btc_series()
    bars, _v, fund = load_coin(sym)
    bar_ts = np.array(sorted(bars), dtype=np.int64)
    bar_px = np.array([bars[int(x)] for x in bar_ts], dtype=np.float64)
    f_ts = np.array(sorted(fund), dtype=np.int64)
    f_rate = np.array([fund[int(x)] for x in f_ts], dtype=np.float64)
    i = int(np.searchsorted(f_ts, t, side="left"))
    if i < 90 or int(f_ts[i]) != t:
        return None
    built = _features(bar_ts, bar_px, f_ts, f_rate, i, t)
    if built is None:
        return None
    feat, _px, z, _bi = built
    btc_r = _btc_returns(btc[0], btc[1], t)
    if btc_r is None:
        return None
    return {
        "t": t,
        "feat": feat,
        "z": float(z),
        "btc_r1d": float(btc_r[0]),
        "btc_r7d": float(btc_r[1]),
    }


def prove_no_lookahead(sym: str = "ETHUSDT") -> None:
    """Features at t stay identical if every later bar and funding print is removed."""
    bars, vols, fund = load_coin(sym)
    rows = build_rows(sym)
    if len(rows) < 50:
        raise SystemExit(f"prove: not enough rows for {sym}")
    row = rows[len(rows) // 2]
    t = int(row["t"])
    cut_bars = {k: v for k, v in bars.items() if k <= t - HOUR}
    cut_vols = {k: v for k, v in vols.items() if k <= t - HOUR}
    cut_fund = {k: v for k, v in fund.items() if k <= t}
    blob = {"bars": {str(k): [cut_bars[k], cut_vols[k]] for k in cut_bars},
            "funding": {str(k): cut_fund[k] for k in cut_fund}}
    scratch = LAB / "fresh" / f"{sym}_fresh.json"
    scratch.parent.mkdir(exist_ok=True)
    backup = scratch.read_text(encoding="utf-8") if scratch.exists() else None
    # Prove on a private copy by monkeypatching load through a temp overlay
    # that REPLACES history: write only the truncated series into fresh after
    # clearing is not enough because load merges. Rebuild features inline.
    bar_ts = np.array(sorted(cut_bars), dtype=np.int64)
    bar_px = np.array([cut_bars[int(x)] for x in bar_ts], dtype=np.float64)
    f_ts = np.array(sorted(cut_fund), dtype=np.int64)
    f_rate = np.array([cut_fund[int(x)] for x in f_ts], dtype=np.float64)
    i = int(np.searchsorted(f_ts, t, side="left"))
    built = _features(bar_ts, bar_px, f_ts, f_rate, i, t)
    if built is None:
        raise SystemExit("prove: truncated history could not rebuild features")
    feat, _px, z, bi = built
    if int(bar_ts[bi]) != t - HOUR:
        raise SystemExit("prove: entry bar is not the candle that closed at t")
    if not np.allclose(feat, row["feat"], atol=1e-12):
        raise SystemExit("prove: features changed when the future was removed")
    if abs(float(z) - row["z"]) > 1e-9:
        raise SystemExit("prove: funding z changed when the future was removed")
    # The bar that opens at t (close at t+1h) must not be the entry bar.
    if t in bars and int(bar_ts[bi]) == t:
        raise SystemExit("prove: entry used the still-open candle")
    del blob, scratch, backup
    print(f"prove_no_lookahead {sym} t={t} OK features={N_FEAT}", flush=True)
