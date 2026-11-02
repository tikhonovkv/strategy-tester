# -*- coding: utf-8 -*-
"""Decompose G1b_asym_top2 and test exact 48h-rebalance Carry/Momentum sleeve (from combo-momentum-carry)
combined with G1b_asym_top2.
"""
from __future__ import annotations

import json
import numpy as np
import schwartz_production as sp
import _gen_and_hybrid_audit as G


def build_exact_combo_sleeve(series: dict, syms: list[str], hold_h: int = 48, leverage: float = 1.5) -> dict[int, float]:
    """48h rebalance sleeve matching combo-momentum-carry:
    336h (14d) risk-adjusted momentum (gated by 60d BTC MA) + 21-funding-event carry (always on).
    Charged turnover-only fees when weights change!
    """
    btc = series["BTCUSDT"]
    ts_arr = btc["close_ts"]
    idx_map = {sym: {int(t): i for i, t in enumerate(series[sym]["close_ts"])} for sym in syms if sym in series}
    step_rets = {}
    prev_w = {}
    for k in range(24 * 65, len(ts_arr) - hold_h, hold_h):
        t = int(ts_arr[k])
        t_next = int(ts_arr[k + hold_h])
        if t_next != t + hold_h * sp.HOUR:
            continue
        rows = []
        for sym in syms:
            if sym not in idx_map:
                continue
            i = idx_map[sym].get(t)
            i_next = idx_map[sym].get(t_next)
            if i is None or i_next is None or i < 340:
                continue
            s = series[sym]
            px_now = float(s["px"][i])
            px_336 = float(s["px"][i - 336])
            atr = max(float(s["atr1h"][i]), 0.002)
            if px_336 <= 0 or px_now <= 0:
                continue
            mom = (px_now / px_336 - 1.0) / (atr * np.sqrt(336.0))
            fi = int(np.searchsorted(s["f_ts"], t, side="right")) - 1
            if fi < 25:
                continue
            # Carry: positive when shorts pay longs (f_rate < 0 -> long collects, f_rate > 0 -> short collects)
            mean_f = float(np.mean(s["f_rate"][fi - 20:fi + 1]))
            carry_score = -mean_f / (float(np.std(s["f_rate"][fi - 20:fi + 1])) + 1e-6)
            px_end = float(s["px"][i_next])
            raw_r = px_end / px_now - 1.0
            lo = int(np.searchsorted(s["f_ts"], t, side="right"))
            hi = int(np.searchsorted(s["f_ts"], t_next, side="right"))
            paid = float(s["f_rate"][lo:hi].sum()) if hi > lo else 0.0
            rows.append((sym, mom, carry_score, raw_r, paid))
        if len(rows) < 6:
            continue
        moms = np.array([x[1] for x in rows])
        carries = np.array([x[2] for x in rows])
        mz = (moms - moms.mean()) / (moms.std() + 1e-8)
        cz = (carries - carries.mean()) / (carries.std() + 1e-8)

        b_ma = float(np.mean(btc["px"][max(0, k - 24 * 60):k + 1]))
        mom_on = float(btc["px"][k]) > b_ma
        alpha = (0.75 * mz + 0.25 * cz) if mom_on else (0.35 * cz)
        order = np.argsort(alpha)
        k_leg = min(3, len(rows) // 3)
        new_w = {}
        for idx_s in order[:k_leg]:
            new_w[rows[int(idx_s)][0]] = -0.5 / k_leg
        for idx_s in order[-k_leg:]:
            new_w[rows[int(idx_s)][0]] = +0.5 / k_leg

        # Calculate turnover fee only on weight changes
        all_k = set(prev_w.keys()) | set(new_w.keys())
        turnover = sum(abs(new_w.get(sym, 0.0) - prev_w.get(sym, 0.0)) for sym in all_k)
        fee_cost = turnover * (0.00055 + 0.00015)
        prev_w = new_w

        gross_ret = 0.0
        by_sym = {r[0]: (r[3], r[4]) for r in rows}
        for sym, w in new_w.items():
            raw_r, paid = by_sym[sym]
            # If w > 0 (long): earns raw_r - paid. If w < 0 (short): earns -raw_r + paid = w * (raw_r - paid)
            gross_ret += w * (raw_r - paid)
        net_step = (gross_ret - fee_cost) * leverage
        step_rets[t] = float(np.clip(net_step, -0.25, 0.40))
    return step_rets


def main():
    series = G.load_all_series()
    alts7 = sp.ALTS_7
    base_cands_raw, _ = sp.build_tranche_candidates()
    base_cands = []
    for e in base_cands_raw:
        r = dict(e)
        r["net"] = float(e["net"]) - 2.0 * G.SLIP
        r["pyr_net"] = float(e.get("pyr_net") or 0.0) - G.SLIP if e.get("pyr") else 0.0
        base_cands.append(r)
    base_cands = G.add_causal_meta(base_cands, series, alts7)

    sleeve48 = build_exact_combo_sleeve(series, list(series.keys()), hold_h=48, leverage=1.5)

    variants = [
        ("V1_base", {"side_asym": "none", "tie_break": "default", "mode": "pure"}),
        ("G1_asym_only", {"side_asym": "eth_btc_top2", "tie_break": "default", "mode": "pure"}),
        ("G1_tie_only", {"side_asym": "none", "tie_break": "vol_surge", "mode": "pure"}),
        ("G1b_asym_top2_tie", {"side_asym": "eth_btc_top2", "tie_break": "vol_surge", "mode": "pure"}),
        ("H_sleeve48_split", {"side_asym": "eth_btc_top2", "tie_break": "vol_surge", "sleeve_rets": sleeve48, "mode": "split", "split_schwartz": 0.70}),
        ("H_sleeve48_idle", {"side_asym": "eth_btc_top2", "tie_break": "vol_surge", "sleeve_rets": sleeve48, "mode": "idle"}),
    ]

    data = json.loads(G.OUT_JSON.read_text(encoding="utf-8"))
    curves = json.loads(G.OUT_CURVES.read_text(encoding="utf-8"))
    for name, kw in variants:
        data["report"][name] = {}
        curves[name] = {}
        for label, a, b in G.WINDOWS:
            res = G.simulate_custom(base_cands, a, b, **kw)
            curves[name][label] = res.pop("curve")
            data["report"][name][label] = res
        f = data["report"][name]["full"]
        print(
            f"{name:20} | 2022:{data['report'][name]['2022']['ret']:6.1f}% | 2023:{data['report'][name]['2023']['ret']:6.1f}% | "
            f"2024:{data['report'][name]['2024']['ret']:6.1f}% | 2025:{data['report'][name]['2025']['ret']:6.1f}% | "
            f"2026H1:{data['report'][name]['2026H1']['ret']:6.1f}% | 2026Q:{data['report'][name]['2026Q']['ret']:6.1f}% | "
            f"FULL:${f['final']:9.0f} DD:{f['dd']:4.1f}% top5:{f['top5']:.3f}",
            flush=True,
        )
    G.OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    G.OUT_CURVES.write_text(json.dumps(curves), encoding="utf-8")


if __name__ == "__main__":
    main()
