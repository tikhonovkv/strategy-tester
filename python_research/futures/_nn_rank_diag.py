# -*- coding: utf-8 -*-
"""How often do two CORE signals collide, and does the net pick the better one?"""
from collections import defaultdict
from schwartz_nn import attach_entry_features, walk_forward_score
from schwartz_production import CORE_4, build_tranche_candidates
from test_schwartz_hybrid import Q_CUT, Y2026


def main():
    cands, series = build_tranche_candidates()
    attach_entry_features(cands, series)
    scored = walk_forward_score(cands)
    by_t = defaultdict(list)
    for e in scored:
        if e["sym"] in CORE_4:
            by_t[e["t"]].append(e)
    collisions = [v for v in by_t.values() if len(v) >= 2]
    hits = 0
    for batch in collisions:
        pick = max(batch, key=lambda x: x["nn_score"])
        best = max(batch, key=lambda x: x["target"])
        if pick["sym"] == best["sym"] and pick["side"] == best["side"]:
            hits += 1
    print("CORE hours=%d collisions=%d nn_picks_best=%d (%.1f%%)" % (
        len(by_t), len(collisions), hits, 100.0 * hits / max(1, len(collisions))
    ))
    for label, lo in (("2026", Y2026), ("QTR", Q_CUT)):
        col = [v for t, v in by_t.items() if t >= lo and len(v) >= 2]
        h = 0
        for batch in col:
            pick = max(batch, key=lambda x: x["nn_score"])
            best = max(batch, key=lambda x: x["target"])
            if pick["sym"] == best["sym"] and pick["side"] == best["side"]:
                h += 1
        print("%s collisions=%d hits=%d (%.1f%%)" % (label, len(col), h, 100.0 * h / max(1, len(col))))


if __name__ == "__main__":
    main()
