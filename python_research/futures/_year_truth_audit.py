# -*- coding: utf-8 -*-
"""Where does the profit actually come from, year by year.

Checks three things the previous runs never separated:
  1. How much history each coin really has (survivorship and data start).
  2. Flat $100 risk expectancy per year, so compounding cannot hide a weak year.
  3. Whether the new side-asymmetry rule is a real edge or a fit to 2025-2026:
     the same rule is scored on the years it was never chosen on.
Local only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp

SLIP = 0.0003
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
OUT = sp.LAB / "_year_truth_audit.json"


def stamp(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


YEARS = [
    ("2022", stamp(2022, 1, 1), stamp(2023, 1, 1)),
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, END),
]


def coin_history(series):
    out = {}
    for sym in sp.ALTS_7 + ["BTCUSDT"]:
        s = series.get(sym)
        if s is None:
            continue
        ts = s["close_ts"]
        out[sym] = {
            "first": datetime.fromtimestamp(int(ts[0]) / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "bars": int(len(ts)),
        }
    return out


def flat_pnl(rows):
    """Fixed 100 USDT of risk per trade. No compounding, no slot queue."""
    out = {}
    for label, a, b in YEARS:
        part = [r for r in rows if a <= int(r["t"]) < b]
        if not part:
            out[label] = {"n": 0}
            continue
        pnl = []
        for r in part:
            stop = max(2.2 * float(r["atr"]), 0.008)
            net = float(r["net"]) - 2.0 * SLIP
            pnl.append(100.0 * net / stop)
        ordered = sorted(pnl, reverse=True)
        rest = ordered[5:] if len(ordered) > 5 else []
        wins = [x for x in pnl if x > 0]
        out[label] = {
            "n": len(pnl),
            "total": round(float(np.sum(pnl)), 1),
            "mean": round(float(np.mean(pnl)), 2),
            "ex5": round(float(np.mean(rest)), 2) if rest else None,
            "winrate": round(100.0 * len(wins) / len(pnl), 1),
            "best": round(max(pnl), 1),
        }
    return out


def side_split(rows):
    out = {}
    for label, a, b in YEARS:
        part = [r for r in rows if a <= int(r["t"]) < b]
        res = {}
        for name, side in (("long", 1), ("short", -1)):
            sub = [r for r in part if int(r["side"]) == side]
            if not sub:
                res[name] = {"n": 0, "total": 0.0}
                continue
            pnl = [100.0 * (float(r["net"]) - 2.0 * SLIP) / max(2.2 * float(r["atr"]), 0.008) for r in sub]
            res[name] = {"n": len(pnl), "total": round(float(np.sum(pnl)), 1), "mean": round(float(np.mean(pnl)), 2)}
        out[label] = res
    return out


def rule_keeps(r):
    """The shipped asymmetry rule: LONG only ETH/BTC or a top-2 24h leader."""
    if int(r.get("side") or 0) != 1:
        return True
    if r["sym"] in {"BTCUSDT", "ETHUSDT"}:
        return True
    return int(r.get("rank24", 1)) <= 2


def rule_effect(rows):
    """Flat-risk dollars the asymmetry rule removes, per year. Positive removed = rule helped."""
    out = {}
    for label, a, b in YEARS:
        part = [r for r in rows if a <= int(r["t"]) < b]
        dropped = [r for r in part if not rule_keeps(r)]
        if not part:
            out[label] = {"n_dropped": 0}
            continue
        pnl_drop = [100.0 * (float(r["net"]) - 2.0 * SLIP) / max(2.2 * float(r["atr"]), 0.008) for r in dropped]
        kept = [r for r in part if rule_keeps(r)]
        pnl_keep = [100.0 * (float(r["net"]) - 2.0 * SLIP) / max(2.2 * float(r["atr"]), 0.008) for r in kept]
        out[label] = {
            "n_total": len(part),
            "n_dropped": len(dropped),
            "dropped_total": round(float(np.sum(pnl_drop)), 1) if pnl_drop else 0.0,
            "kept_mean": round(float(np.mean(pnl_keep)), 2) if pnl_keep else None,
            "all_mean": round(float(np.mean(pnl_keep + pnl_drop)), 2),
        }
    return out


def main():
    cands, series = sp.build_tranche_candidates()
    report = {
        "history": coin_history(series),
        "flat": flat_pnl(cands),
        "sides": side_split(cands),
        "asymmetry": rule_effect(cands),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("HISTORY")
    for sym, v in report["history"].items():
        print(" ", sym, v["first"], v["bars"])
    print("FLAT 100 USDT risk")
    for label, v in report["flat"].items():
        print(" ", label, v)
    print("SIDES")
    for label, v in report["sides"].items():
        print(" ", label, "long", v["long"], "short", v["short"])
    print("ASYMMETRY RULE")
    for label, v in report["asymmetry"].items():
        print(" ", label, v)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
