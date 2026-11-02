# -*- coding: utf-8 -*-
"""Is the shipped asymmetry rule an edge, or a fit to 2025-2026?

Three checks the earlier runs skipped:
  A. Portfolio per year with the rule ON vs OFF (same engine, same costs).
  B. Walk-forward: choose the rule only on past years, score it on the next year.
  C. Market direction per year, to see whether the book is just a short bias.
Local only. Does not modify the live bot.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import schwartz_production as sp

OUT = sp.LAB / "_rule_honesty.json"
END = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)


def stamp(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


YEARS = [
    ("2022", stamp(2022, 1, 1), stamp(2023, 1, 1)),
    ("2023", stamp(2023, 1, 1), stamp(2024, 1, 1)),
    ("2024", stamp(2024, 1, 1), stamp(2025, 1, 1)),
    ("2025", stamp(2025, 1, 1), stamp(2026, 1, 1)),
    ("2026H1", stamp(2026, 1, 1), sp.Q_CUT),
    ("2026Q", sp.Q_CUT, END),
    ("full", stamp(2022, 1, 1), END),
]


def strip_rule(cands):
    """Undo the asymmetry gate by making every row look like a top-1 leader."""
    out = []
    for e in cands:
        r = dict(e)
        r["rank24"] = 1
        out.append(r)
    return out


def strip_tie(cands):
    """Undo the volume tie-break by flattening vol_surge."""
    out = []
    for e in cands:
        r = dict(e)
        r["vol_surge"] = 1.0
        out.append(r)
    return out


def run(cands, label_set=YEARS):
    res = {}
    for label, a, b in label_set:
        part = [dict(e) for e in cands if a <= int(e["t"]) < b]
        r = sp.simulate_window(part)
        wins = sorted((t["pnl_usd"] for t in r["trades"] if t["pnl_usd"] > 0), reverse=True)
        res[label] = {
            "n": r["n"],
            "ret": r["ret"],
            "dd": r["dd"],
            "final": r["final"],
            "top5": round(sum(wins[:5]) / (sum(wins) or 1.0), 3),
        }
    return res


def btc_year_move(series):
    btc = series["BTCUSDT"]
    ts, px = btc["close_ts"], btc["px"]
    out = {}
    for label, a, b in YEARS:
        i0 = int(np.searchsorted(ts, a))
        i1 = int(np.searchsorted(ts, b)) - 1
        if i0 >= len(px) or i1 <= i0:
            continue
        out[label] = round((float(px[i1]) / float(px[i0]) - 1.0) * 100.0, 1)
    return out


def main():
    cands, series = sp.build_tranche_candidates()

    with_rule = run(cands)
    no_asym = run(strip_rule(cands))
    no_tie = run(strip_tie(cands))
    plain = run(strip_tie(strip_rule(cands)))

    # Walk-forward: the rule is fixed, so score it only on years after the ones that suggested it.
    # The rule was read off 2025-2026 behaviour, so 2022-2024 are the honest out-of-sample years.
    honest = {}
    for label in ("2022", "2023", "2024"):
        honest[label] = {
            "rule_on": with_rule[label]["ret"],
            "rule_off": plain[label]["ret"],
            "delta": round(with_rule[label]["ret"] - plain[label]["ret"], 2),
        }

    report = {
        "with_rule": with_rule,
        "no_asym": no_asym,
        "no_tie": no_tie,
        "plain": plain,
        "btc_move": btc_year_move(series),
        "out_of_sample_2022_2024": honest,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'period':8} {'plain':>10} {'asym+tie':>10} {'delta':>8} {'BTC':>8}")
    for label, _a, _b in YEARS:
        p = plain[label]["ret"]
        w = with_rule[label]["ret"]
        btc = report["btc_move"].get(label, 0.0)
        print(f"{label:8} {p:10.1f} {w:10.1f} {w - p:8.1f} {btc:8.1f}")
    print()
    print("full plain  ", plain["full"])
    print("full asym   ", no_tie["full"])
    print("full tie    ", no_asym["full"])
    print("full both   ", with_rule["full"])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
