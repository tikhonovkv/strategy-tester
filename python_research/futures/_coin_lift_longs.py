# -*- coding: utf-8 -*-
from collections import defaultdict
from causal import DAY
from schwartz_production import ALTS_7, CORE_4, build_tranche_candidates, simulate_window
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026
import schwartz_production as sp


def run(cands, skip, **kw):
    old = set(sp.CORE_4)
    try:
        part = [dict(e) for e in cands if not skip(e)]
        return simulate_window(part, **kw)
    finally:
        sp.CORE_4 = old


def pack(r):
    return f"ret={r['ret']:+7.1f} dd={r['dd']:5.1f} n={r['n']:3d} wr={r['wins']/max(1,r['n'])*100:4.1f}"


def main():
    cands, _ = build_tranche_candidates()
    t_max = max(e["t"] for e in cands)
    slices = {
        "30": [e for e in cands if e["t"] >= t_max - 30 * DAY],
        "90": [e for e in cands if e["t"] >= Q_CUT],
        "365": [e for e in cands if e["t"] >= t_max - 365 * DAY],
        "full": cands,
        "2025": [e for e in cands if Y2025 <= e["t"] < Y2026],
        "2026": [e for e in cands if e["t"] >= Y2026],
    }
    rules = [
        ("BASE", lambda e: False),
        ("no ADA longs", lambda e: e["sym"] == "ADAUSDT" and e["side"] == 1),
        ("no SUI longs", lambda e: e["sym"] == "SUIUSDT" and e["side"] == 1),
        ("no ADA+SUI longs", lambda e: e["sym"] in {"ADAUSDT", "SUIUSDT"} and e["side"] == 1),
        ("no alt longs (ETH ok)", lambda e: e["side"] == 1 and e["sym"] != "ETHUSDT"),
        ("no XRP ever", lambda e: e["sym"] == "XRPUSDT"),
        ("no AVAX ever", lambda e: e["sym"] == "AVAXUSDT"),
        ("no XRP+AVAX", lambda e: e["sym"] in {"XRPUSDT", "AVAXUSDT"}),
    ]
    for name, skip in rules:
        bits = [name]
        for w, part in slices.items():
            r = run(part, skip)
            bits.append(f"{w}={r['ret']:+6.1f}/{r['dd']:.0f}")
        print(" | ".join(bits), flush=True)

    # 365 reason x coin
    r = run(slices["365"], lambda e: False)
    rc = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for t in r["trades"]:
        rc[t["coin"]][t["reason"]][0] += 1
        rc[t["coin"]][t["reason"]][1] += t["pnl_usd"]
    print("\n365 reason x coin (n / usd)")
    for c, rs in rc.items():
        print(c, {k: (v[0], round(v[1], 1)) for k, v in rs.items()})


if __name__ == "__main__":
    main()
