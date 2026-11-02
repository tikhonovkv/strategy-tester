# -*- coding: utf-8 -*-
"""Which extra majors the empty-slot net actually took, vs BASE."""
from collections import defaultdict
from schwartz_pitbull import build_hourly_series
from schwartz_production import CORE_4, SHORT_NAME, build_tranche_candidates, simulate_window
import schwartz_production as sp
from _fast_concept_improve import EXTRA_LOT, EXTRA_SYMS, add_concept_features, build_reclaim_candidates_for_syms
from _fast_concept_v2 import walk_forward_keep_all
from test_schwartz_hybrid import Y2025, Y2026, Q_CUT
from causal import DAY


def taken(part, **kw):
    return simulate_window([dict(e) for e in part], **kw)["trades"]


def main():
    sp.LOT_STEP.update(EXTRA_LOT)
    for sym in EXTRA_SYMS:
        SHORT_NAME.setdefault(sym, sym.replace("USDT", ""))
    cands7, series = build_tranche_candidates()
    for sym in EXTRA_SYMS:
        if sym not in series:
            series[sym] = build_hourly_series(sym)
    extra = build_reclaim_candidates_for_syms([s for s in EXTRA_SYMS if s in series], series)
    add_concept_features(cands7, series)
    add_concept_features(extra, series)
    s7 = walk_forward_keep_all(cands7)
    sx = walk_forward_keep_all(extra)
    pool = []
    for e in s7:
        r = dict(e)
        if r["sym"] not in CORE_4 and (r["hot_n"] < 4 or r["hot_avg"] < 0.015):
            continue
        r["nn_score"] = (100.0 if r["sym"] in CORE_4 else 50.0) + r["hot_avg"]
        pool.append(r)
    admitted = []
    for e in sx:
        r = dict(e)
        if r["hot_n"] >= 4 and r["hot_avg"] >= 0.025 and r["side_avg"] > 0.005 and r["pred_r"] >= 0.25:
            r["nn_score"] = 10.0 + r["pred_r"] + r["hot_avg"]
            admitted.append(r)
            pool.append(r)
    print("candidates admitted by net:", len(admitted), flush=True)
    base = taken(cands7, core_gate=True, rank="rules")
    model = taken(pool, core_gate=False, rank="nn")
    base_keys = {(t["t"], t["sym"], t["side"]) for t in base}
    extra_taken = [t for t in model if (t["t"], t["sym"], t["side"]) not in base_keys]
    missing = [t for t in base if (t["t"], t["sym"], t["side"]) not in {(x["t"], x["sym"], x["side"]) for x in model}]
    print("BASE n", len(base), "MODEL n", len(model), "added", len(extra_taken), "dropped", len(missing), flush=True)
    by = defaultdict(lambda: {"n": 0, "wins": 0, "usd": 0.0})
    for t in extra_taken:
        b = by[t["coin"] + (" L" if t["side"] == 1 else " S")]
        b["n"] += 1
        b["wins"] += 1 if t["pnl_usd"] > 0 else 0
        b["usd"] += t["pnl_usd"]
        print(
            "%s %s %s %s hold=%sh pnl=%+.0f eq=%.0f reason=%s"
            % (t["time"][:10], t["coin"], "LONG" if t["side"] == 1 else "SHORT", t["sym"], t["hold_h"], t["pnl_usd"], t["eq"], t["reason"]),
            flush=True,
        )
    print("by coin", {k: {**v, "usd": round(v["usd"], 1)} for k, v in by.items()}, flush=True)
    if missing:
        print("dropped from base", [(t["time"][:10], t["coin"], t["pnl_usd"]) for t in missing[:12]], flush=True)


if __name__ == "__main__":
    main()
