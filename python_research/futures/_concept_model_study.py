# -*- coding: utf-8 -*-
"""Diagnose WHY the net lost to Schwartz rules and test 4 concept-aligned training fixes:
1. Target anatomy: why MSE on raw/clipped net fails on fat-tail (fast_bail vs runner).
2. Concept-aligned targets:
   - Head A: P(fast_bail | entry) — teach the model to recognize chop / whipsaw BEFORE entry.
   - Head B: P(pyramid / runner >= +2 ATR | entry) — teach the model to spot clean follow-through.
   - Combined Expected Value: E[R] = p_run * E[run] - p_bail * E[bail] + ...
3. Role of the model:
   - Role 1: Satellite selector (keep CORE_4 reactive triggers untouched, let the net admit & rank satellite setups from 25 majors when slots are free or when score is top-decile).
   - Role 2: Dynamic risk / sizing multiplier (0.5x on high bail probability, 1.5x on high runner probability) instead of hard skipping core trend rides.
   - Role 3: Entry timing / setup expansion (24h EMA + 48h EMA reclaim across majors, filtered by learned bail/runner ratio).
"""
from __future__ import annotations

import json
from collections import defaultdict
import numpy as np
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from causal import DAY, HOUR, LAB
from schwartz_production import (
    ALTS_7,
    CORE_4,
    SHORT_NAME,
    build_tranche_candidates,
    simulate_window,
)
from schwartz_trader import _load_universe, build_events, windows, pack
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026


def analyze_schwartz_anatomy(cands: list[dict]):
    n = len(cands)
    bails = [e for e in cands if e["reason"] == "fast_bail"]
    stops = [e for e in cands if e["reason"] == "hard_stop"]
    tp_be = [e for e in cands if e["reason"] == "tp_scale+be"]
    ema_ex = [e for e in cands if e["reason"] == "ema10_exit"]
    ends = [e for e in cands if e["reason"] == "end"]
    pyrs = [e for e in cands if e["pyr"]]

    def avg_tot(lst):
        return round(float(np.mean([e["net"] + 1.5 * e["pyr_net"] for e in lst])) * 100.0, 2) if lst else 0.0

    def sum_tot(lst):
        return round(float(np.sum([e["net"] + 1.5 * e["pyr_net"] for e in lst])) * 100.0, 1) if lst else 0.0

    return {
        "total_signals": n,
        "fast_bail": {"n": len(bails), "share_pct": round(len(bails) / n * 100, 1), "avg_pct": avg_tot(bails), "sum_pct": sum_tot(bails)},
        "hard_stop": {"n": len(stops), "share_pct": round(len(stops) / n * 100, 1), "avg_pct": avg_tot(stops), "sum_pct": sum_tot(stops)},
        "tp_scale_be": {"n": len(tp_be), "share_pct": round(len(tp_be) / n * 100, 1), "avg_pct": avg_tot(tp_be), "sum_pct": sum_tot(tp_be)},
        "ema10_exit": {"n": len(ema_ex), "share_pct": round(len(ema_ex) / n * 100, 1), "avg_pct": avg_tot(ema_ex), "sum_pct": sum_tot(ema_ex)},
        "end_runner": {"n": len(ends), "share_pct": round(len(ends) / n * 100, 1), "avg_pct": avg_tot(ends), "sum_pct": sum_tot(ends)},
        "pyramid_reached": {"n": len(pyrs), "share_pct": round(len(pyrs) / n * 100, 1), "avg_pct": avg_tot(pyrs), "sum_pct": sum_tot(pyrs)},
    }


def enrich_cross_sectional_features(events: list[dict], series: dict) -> None:
    """Add market-breadth & regime features that tell the model IF the market is trending or chopping:
    - Breadth: fraction of coins currently above their 10d EMA (market synchrony).
    - Dispersion / BTC dominance of move: coin 24h return minus BTC 24h return in ATR units (relative strength!).
    - Volatility compression/expansion: 24h ATR vs 7d ATR.
    - Funding crowding across market: median funding rate across universe.
    """
    syms = sorted(series.keys())
    btc = series["BTCUSDT"]
    idx_map = {sym: {int(ts): i for i, ts in enumerate(s["close_ts"])} for sym, s in series.items()}

    # Precompute hourly breadth (% of coins > ema240 and with positive slope)
    all_ts = sorted(set(int(ts) for ts in btc["close_ts"]))
    breadth_bull = {}
    breadth_bear = {}
    for t in all_ts:
        up = 0
        dn = 0
        tot = 0
        for sym in syms:
            i = idx_map[sym].get(t)
            if i is None or i < 240:
                continue
            s = series[sym]
            px = float(s["px"][i])
            es = float(s["ema240"][i])
            slope = es / float(s["ema240"][i - 24]) - 1.0
            tot += 1
            if px > es and slope > 0:
                up += 1
            elif px < es and slope < 0:
                dn += 1
        if tot > 0:
            breadth_bull[t] = up / tot
            breadth_bear[t] = dn / tot

    # Precompute 7d ATR per coin
    atr7d = {}
    for sym, s in series.items():
        a1 = s["atr1h"]
        n = len(a1)
        out = np.zeros(n, dtype=np.float64)
        cs = np.cumsum(a1)
        for i in range(168, n):
            out[i] = (cs[i] - cs[i - 168]) / 168.0
        atr7d[sym] = out

    for e in events:
        sym = e["sym"]
        t = int(e["t"])
        s = series[sym]
        i = idx_map[sym].get(t)
        bi = idx_map["BTCUSDT"].get(t)
        side = float(e["side"])
        if i is None or bi is None or i < 168 or bi < 168:
            e["xfeat"] = np.zeros(18, dtype=np.float64)
            continue

        px = s["px"]
        atr = max(float(s["atr1h"][i]), 0.002)
        a7 = max(float(atr7d[sym][i]), 0.002)
        vol_comp = atr / a7  # <1 means coiled spring, >1.5 means already exhausted

        # Relative strength vs BTC over 4h, 24h, 72h
        c_r4 = side * (float(px[i]) / float(px[i - 4]) - 1.0)
        b_r4 = side * (float(btc["px"][bi]) / float(btc["px"][bi - 4]) - 1.0)
        rs_4 = (c_r4 - b_r4) / atr

        c_r24 = side * (float(px[i]) / float(px[i - 24]) - 1.0)
        b_r24 = side * (float(btc["px"][bi]) / float(btc["px"][bi - 24]) - 1.0)
        rs_24 = (c_r24 - b_r24) / atr

        c_r72 = side * (float(px[i]) / float(px[i - 72]) - 1.0)
        b_r72 = side * (float(btc["px"][bi]) / float(btc["px"][bi - 72]) - 1.0)
        rs_72 = (c_r72 - b_r72) / atr

        # Market synchrony in trade direction
        sync = breadth_bull.get(t, 0.5) if side == 1 else breadth_bear.get(t, 0.5)

        # Pullback depth from 48h high/low in direction of trend
        win48 = px[i - 48 : i + 1]
        if side == 1:
            dd_from_peak = (float(np.max(win48)) - float(px[i])) / float(px[i]) / atr
        else:
            dd_from_peak = (float(px[i]) - float(np.min(win48))) / float(px[i]) / atr

        # Base features from e["feat"][:13]
        base_f = e["feat"][:13]
        extra = np.array([
            sync,
            vol_comp,
            rs_4,
            rs_24,
            rs_72,
            dd_from_peak,
            1.0 if sym in CORE_4 else 0.0,
            1.0 if sym == "BTCUSDT" else 0.0,
        ], dtype=np.float64)
        e["xfeat"] = np.concatenate([base_f, extra])
        # Binary labels aligned with Schwartz concept:
        # 1) Did it reach +2 ATR (pyramid / breakeven ratchet)? -> True trend follow-through!
        e["y_pyr"] = 1 if e["pyr"] else 0
        # 2) Did it die in 4 hours (fast_bail) or hard_stop? -> Whipsaw!
        e["y_whip"] = 1 if e["reason"] in ("fast_bail", "hard_stop") else 0


def walk_forward_dual_head(events: list[dict], refit_days: int = 14, train_days: int = 540):
    """Train a Two-Head model (Runner Classifier + Bail Classifier + Expected R) strictly causally."""
    ordered = sorted(events, key=lambda x: x["t"])
    t_min = min(e["t"] for e in ordered)
    t_max = max(e["t"] for e in ordered)
    day0 = ((t_min // DAY) + 240) * DAY
    by_day = defaultdict(list)
    for e in ordered:
        by_day[(e["t"] // DAY) * DAY].append(e)

    days = list(range(day0, t_max + DAY, DAY))
    clf_pyr = None
    clf_whip = None
    mu = None
    sd = None
    scored = []

    for idx, d in enumerate(days):
        if clf_pyr is None or idx % refit_days == 0:
            lo = d - train_days * DAY
            train = [e for e in ordered if lo <= e["t"] and e["exit_ts"] <= d]
            if len(train) >= 120:
                X = np.vstack([e["xfeat"] for e in train])
                y_p = np.array([e["y_pyr"] for e in train], dtype=np.int32)
                y_w = np.array([e["y_whip"] for e in train], dtype=np.int32)
                mu = X.mean(axis=0)
                sd = X.std(axis=0)
                sd[sd < 1e-6] = 1.0
                Z = (X - mu) / sd
                if len(np.unique(y_p)) == 2 and len(np.unique(y_w)) == 2:
                    m_p = MLPClassifier(hidden_layer_sizes=(12, 6), activation="tanh", solver="lbfgs", alpha=2.0, max_iter=250, random_state=0)
                    m_w = MLPClassifier(hidden_layer_sizes=(12, 6), activation="tanh", solver="lbfgs", alpha=2.0, max_iter=250, random_state=1)
                    m_p.fit(Z, y_p)
                    m_w.fit(Z, y_w)
                    clf_pyr = m_p
                    clf_whip = m_w

        if clf_pyr is None:
            continue
        day_rows = by_day.get(d, [])
        if not day_rows:
            continue
        X_d = np.vstack([e["xfeat"] for e in day_rows])
        Z_d = (X_d - mu) / sd
        p_pyr = clf_pyr.predict_proba(Z_d)[:, 1]
        p_whip = clf_whip.predict_proba(Z_d)[:, 1]
        for e, pp, pw in zip(day_rows, p_pyr, p_whip):
            row = dict(e)
            row["p_pyr"] = float(pp)
            row["p_whip"] = float(pw)
            # Edge score: probability of hitting pyramid (+2 ATR ratchet) minus 0.7 * probability of whipsaw
            row["edge_score"] = float(pp - 0.65 * pw)
            scored.append(row)
    return scored


def main():
    print("1) Analyzing Schwartz candidate anatomy...", flush=True)
    base_cands, base_series = build_tranche_candidates()
    anat = analyze_schwartz_anatomy(base_cands)
    print("Anatomy of 768 Schwartz candidates:", json.dumps(anat, indent=2), flush=True)

    print("\n2) Building reclaim candidates across all 25 liquid majors (incl. BTC)...", flush=True)
    series25 = _load_universe()
    ev_all = build_events(series25)
    reclaim25 = [e for e in ev_all if e.get("reclaim") and e.get("feat")[1] >= 0.0]  # carry_z >= 0 + reclaim
    print("Reclaim25 count (btc_ok/10dEMA + 48h reclaim + carry_z>=0):", len(reclaim25), flush=True)

    # Also test on the 9 major futures (BTC, ETH, SOL, XRP, BNB, ADA, AVAX, LINK, SUI)
    MAJ_9 = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT"}
    reclaim9 = [dict(e) for e in reclaim25 if e["sym"] in MAJ_9]
    reclaim7 = [dict(e) for e in reclaim25 if e["sym"] in set(ALTS_7)]

    print("Enriching cross-sectional features (Breadth, RS vs BTC, Vol compression, Pullback depth)...", flush=True)
    enrich_cross_sectional_features(reclaim7, series25)
    enrich_cross_sectional_features(reclaim9, series25)
    enrich_cross_sectional_features(reclaim25, series25)

    print("Running Dual-Head MLP (P(pyramid) vs P(whipsaw)) on ALTS_7, MAJ_9, and MAJ_25...", flush=True)
    sc7 = walk_forward_dual_head(reclaim7)
    sc9 = walk_forward_dual_head(reclaim9)
    sc25 = walk_forward_dual_head(reclaim25)

    # Check calibration: does high edge_score actually have higher pyramid rate and higher net return?
    for name, sc in (("ALTS_7", sc7), ("MAJ_9 (incl BTC/BNB)", sc9), ("MAJ_25", sc25)):
        arr = np.array([e["edge_score"] for e in sc])
        q25, q50, q75 = np.quantile(arr, [0.25, 0.50, 0.75])
        bot = [e for e in sc if e["edge_score"] <= q25]
        top = [e for e in sc if e["edge_score"] >= q75]
        print(
            f"  [{name}] Bottom 25% edge: n={len(bot)} pyr%={np.mean([e['y_pyr'] for e in bot])*100:.1f}% "
            f"whip%={np.mean([e['y_whip'] for e in bot])*100:.1f}% avg_ret={np.mean([e['target'] for e in bot])*100:+.2f}% | "
            f"Top 25% edge: n={len(top)} pyr%={np.mean([e['y_pyr'] for e in top])*100:.1f}% "
            f"whip%={np.mean([e['y_whip'] for e in top])*100:.1f}% avg_ret={np.mean([e['target'] for e in top])*100:+.2f}%",
            flush=True,
        )

    # Test Concept-Aligned Architectures in the exact same realistic portfolio simulator:
    results = {}
    results["0_BASE_rules"] = windows(base_cands, core_gate=True, rank="rules")

    # Architecture A: Keep Core 4 reactive triggers, use Dual-Head NN to scale size (nn_mult) + rank collisions
    for mult_strength in (0.5, 0.8):
        mod7 = []
        for e in sc7:
            r = dict(e)
            # Center edge_score around 0: if p_pyr > 0.65*p_whip, boost size; if high whipsaw risk, cut size to 0.55x
            edge = r["edge_score"]
            r["nn_score"] = edge + (0.05 if r["sym"] in CORE_4 else 0.0)
            r["nn_mult"] = float(np.clip(1.0 + mult_strength * (edge / 0.25), 0.55, 1.45))
            mod7.append(r)
        results[f"A_DualHead_Size_ALTS7_s{mult_strength}"] = windows(mod7, core_gate=True, rank="nn", score_key="nn_score")

    # Architecture B: Vetoes only extreme whipsaw traps (bottom 15-20% edge_score) + dynamic size
    for q_cut in (0.15, 0.25):
        # causal rolling quantile veto
        vetoed = []
        hist_scores = []
        for e in sc7:
            if len(hist_scores) >= 60:
                thr = float(np.quantile(hist_scores[-200:], q_cut))
            else:
                thr = -999.0
            hist_scores.append(e["edge_score"])
            if e["edge_score"] < thr:
                continue
            r = dict(e)
            r["nn_score"] = r["edge_score"] + (0.05 if r["sym"] in CORE_4 else 0.0)
            r["nn_mult"] = float(np.clip(1.0 + 0.6 * (r["edge_score"] / 0.25), 0.60, 1.40))
            vetoed.append(r)
        results[f"B_DualHead_Veto{int(q_cut*100)}_ALTS7"] = windows(vetoed, core_gate=True, rank="nn", score_key="nn_score")

    # Architecture C: Expand universe to MAJ_9 (adds BTC, BNB) or MAJ_25, where non-CORE coins are admitted ONLY if NN edge_score is in top 25% of recent history!
    for label, sc_pool in (("MAJ9_BTC_BNB", sc9), ("MAJ25", sc25)):
        admitted = []
        hist_scores = []
        for e in sc_pool:
            if len(hist_scores) >= 80:
                q75 = float(np.quantile(hist_scores[-300:], 0.75))
                q20 = float(np.quantile(hist_scores[-300:], 0.20))
            else:
                q75 = 0.05
                q20 = -0.25
            hist_scores.append(e["edge_score"])
            is_core = e["sym"] in CORE_4
            if is_core:
                if e["edge_score"] < q20:
                    continue
            else:
                # Satellite / BTC / BNB / OP / NEAR admitted when NN sees a top-quartile runner setup AND coin isn't cold
                if e["edge_score"] < q75 or e.get("hot_avg", 0.0) < 0.0:
                    continue
            r = dict(e)
            r["nn_score"] = r["edge_score"] + (0.03 if is_core else 0.0)
            r["nn_mult"] = float(np.clip(1.0 + 0.6 * (r["edge_score"] / 0.25), 0.60, 1.40))
            admitted.append(r)
        results[f"C_DualHead_Expand_{label}"] = windows(admitted, core_gate=False, rank="nn", score_key="nn_score")

    print("\n=== SUMMARY ACROSS ALL WINDOWS ===", flush=True)
    for k, res in results.items():
        bits = [f"{k:32s}"]
        for w in ("30", "90", "365", "2025", "2026", "full"):
            if w in res:
                bits.append(f"{w}={res[w]['ret']:+7.1f}%/{res[w]['dd']:4.1f}%n{res[w]['n']}")
        print("  ".join(bits), flush=True)

    out_path = LAB / "concept_model_study.json"
    out_path.write_text(json.dumps({"anatomy": anat, "results": results}, indent=2), encoding="utf-8")
    print("Saved", out_path, flush=True)


if __name__ == "__main__":
    main()
