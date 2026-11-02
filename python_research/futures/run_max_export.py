"""Inspect per-coin performance in Championship Schwartz mode, filter out weak coins,
push the Championship configuration to maximum on clean liquid majors,
and export full trade list + equity curves to JSON for plotting and Canvas.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from causal import DAY, HOUR, LAB
from max_championship import iso, sim_championship_max
from schwartz_boost import collect_active_events
from test_schwartz_hybrid import TRADE_SYMS, Y2024, Y2025, Y2026, Q_CUT
from test_schwartz_ml import walk_forward_reactive


def main():
    events = collect_active_events(TRADE_SYMS)
    ev48 = [e for e in events if e["setup_type"] == 1]
    scored = walk_forward_reactive(ev48, train_win_days=365)
    pos = [e for e in scored if e["pred"] > 0.0]

    print("=== PER-COIN AUDIT (in `model pred > 0`, 9 pairs) ===", flush=True)
    coin_stats = {}
    for sym in TRADE_SYMS:
        c_all = [e for e in pos if e["sym"] == sym]
        c_dev = [e for e in c_all if e["t"] < Y2025]
        c_25 = [e for e in c_all if Y2025 <= e["t"] < Y2026]
        c_26 = [e for e in c_all if e["t"] >= Y2026]
        c_qtr = [e for e in c_all if e["t"] >= Q_CUT]
        m_all = float(np.mean([e["net"] + e["pyr_net"] for e in c_all]) * 100) if c_all else 0.0
        m_dev = float(np.mean([e["net"] + e["pyr_net"] for e in c_dev]) * 100) if c_dev else 0.0
        m_25 = float(np.mean([e["net"] + e["pyr_net"] for e in c_25]) * 100) if c_25 else 0.0
        m_26 = float(np.mean([e["net"] + e["pyr_net"] for e in c_26]) * 100) if c_26 else 0.0
        m_qtr = float(np.mean([e["net"] + e["pyr_net"] for e in c_qtr]) * 100) if c_qtr else 0.0
        coin_stats[sym] = {
            "n_all": len(c_all), "avg_all": round(m_all, 2),
            "n_dev": len(c_dev), "avg_dev": round(m_dev, 2),
            "n_25": len(c_25), "avg_25": round(m_25, 2),
            "n_26": len(c_26), "avg_26": round(m_26, 2),
            "n_qtr": len(c_qtr), "avg_qtr": round(m_qtr, 2),
        }
        print(
            f"  {sym:8s} | total n={len(c_all):3d} avg={m_all:+.2f}% | "
            f"<2025({len(c_dev):2d})={m_dev:+.2f}% | 2025({len(c_25):2d})={m_25:+.2f}% | "
            f"2026({len(c_26):2d})={m_26:+.2f}% | QTR({len(c_qtr):2d})={m_qtr:+.2f}%",
            flush=True,
        )

    good_pre25 = {sym for sym, st in coin_stats.items() if st["n_dev"] > 0 and st["avg_dev"] > 0}
    # Also check core 4 liquid alts (ETH, SOL, ADA, SUI) and top majors
    core_4 = {"ETHUSDT", "SOLUSDT", "ADAUSDT", "SUIUSDT"}
    core_6 = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "SUIUSDT"}

    summary = {"coin_stats": coin_stats, "runs": {}}
    for label_u, syms_u in (
        ("ALL_9_PAIRS", set(TRADE_SYMS)),
        ("PRE2025_POSITIVE_PAIRS", good_pre25),
        ("CORE_6_MAJORS", core_6),
        ("CORE_4_ALTS", core_4),
    ):
        print(f"\n================ {label_u}: {sorted(syms_u)} ================", flush=True)
        for cfg_name, r_pct, m_frac, lv, mc, w_b, p_m in (
            ("Mode5_5x", 0.055, 0.50, 5.0, 3, 1.50, 1.0),
            ("MaxPitbull_6x", 0.065, 0.55, 6.0, 2, 1.65, 1.4),
            ("UltraChamp_7x", 0.075, 0.60, 7.0, 2, 1.80, 1.5),
            ("TitanChamp_8x", 0.085, 0.65, 8.0, 2, 1.90, 1.6),
        ):
            key = f"{label_u}__{cfg_name}"
            summary["runs"][key] = {"syms": sorted(syms_u), "periods": {}}
            print(f"--- {cfg_name} ---", flush=True)
            for lbl, lo, hi in (
                ("FULL", 0, 10**18),
                ("<2025", 0, Y2025),
                ("2025", Y2025, Y2026),
                ("2026", Y2026, 10**18),
                ("QTR", Q_CUT, 10**18),
            ):
                part = [dict(e) for e in pos if lo <= e["t"] < hi]
                res = sim_championship_max(
                    part,
                    allowed_syms=syms_u,
                    risk_pct=r_pct,
                    max_margin_frac=m_frac,
                    lev=lv,
                    max_conc=mc,
                    loss_cut=0.45,
                    win_boost=w_b,
                    pyr_mult=p_m,
                )
                summary["runs"][key]["periods"][lbl] = res
                print(
                    f"  {lbl:5s} n={res['n']:3d} ret={res['ret']:+8.2f}% dd={res['dd']:5.1f}% final=${res['final']:,.2f}",
                    flush=True,
                )

    (LAB / "championship_dump.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print("SAVED championship_dump.json", flush=True)


if __name__ == "__main__":
    main()
