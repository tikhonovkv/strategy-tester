# -*- coding: utf-8 -*-
"""Honest lift audit: leave-one-out coins + walk-forward bans. Same simulator as live."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from causal import DAY, LAB
from schwartz_production import (
    ALTS_7,
    CORE_4,
    SHORT_NAME,
    build_tranche_candidates,
    simulate_window,
)
from test_schwartz_hybrid import Q_CUT, Y2025, Y2026
import schwartz_production as sp

COINS = list(ALTS_7)


def run_sim(cands, allow, core=None, extra_skip=None, **kw):
    extra_skip = extra_skip or (lambda e: False)
    old = set(sp.CORE_4)
    if core is not None:
        sp.CORE_4 = set(core)
    part = [dict(e) for e in cands if e["sym"] in allow and not extra_skip(e)]
    try:
        return simulate_window(part, **kw)
    finally:
        sp.CORE_4 = old


def pack(res):
    return {
        "ret": res["ret"],
        "dd": res["dd"],
        "n": res["n"],
        "wins": res["wins"],
        "final": res["final"],
        "wr": round(res["wins"] / max(1, res["n"]) * 100.0, 1),
    }


def coin_pnl(trades):
    by = defaultdict(lambda: {"n": 0, "wins": 0, "usd": 0.0, "pct": 0.0})
    for t in trades:
        b = by[t["coin"]]
        b["n"] += 1
        b["wins"] += 1 if t["pnl_usd"] > 0 else 0
        b["usd"] += t["pnl_usd"]
        b["pct"] += t["pnl_pct"]
    return {k: {**v, "usd": round(v["usd"], 2), "pct": round(v["pct"], 2)} for k, v in by.items()}


def reason_coin(trades):
    out = defaultdict(lambda: defaultdict(lambda: {"n": 0, "usd": 0.0}))
    for t in trades:
        r = out[t["coin"]][t["reason"]]
        r["n"] += 1
        r["usd"] += t["pnl_usd"]
    return {
        c: {r: {"n": v["n"], "usd": round(v["usd"], 1)} for r, v in rs.items()}
        for c, rs in out.items()
    }


def side_coin(trades):
    out = defaultdict(lambda: {1: {"n": 0, "usd": 0.0}, -1: {"n": 0, "usd": 0.0}})
    for t in trades:
        s = out[t["coin"]][t["side"]]
        s["n"] += 1
        s["usd"] += t["pnl_usd"]
    return {
        c: {("LONG" if k == 1 else "SHORT"): {"n": v["n"], "usd": round(v["usd"], 1)} for k, v in d.items()}
        for c, d in out.items()
    }


def main():
    print("building candidates...", flush=True)
    cands, _ = build_tranche_candidates()
    t_max = max(e["t"] for e in cands)
    windows = {
        "30": t_max - 30 * DAY,
        "90": Q_CUT,
        "365": t_max - 365 * DAY,
        "full": 0,
        "2025": Y2025,
        "2026": Y2026,
    }
    # 2025-only slice
    slices = {
        "30": [e for e in cands if e["t"] >= windows["30"]],
        "90": [e for e in cands if e["t"] >= windows["90"]],
        "365": [e for e in cands if e["t"] >= windows["365"]],
        "full": list(cands),
        "2025": [e for e in cands if Y2025 <= e["t"] < Y2026],
        "2026": [e for e in cands if e["t"] >= Y2026],
        "h1_2026": [e for e in cands if Y2026 <= e["t"] < Q_CUT],
        "qtr": [e for e in cands if e["t"] >= Q_CUT],
    }

    variants = []
    # baseline
    variants.append(("BASE all7", set(COINS), set(CORE_4), None))
    # leave-one-out
    for sym in COINS:
        name = SHORT_NAME[sym]
        variants.append((f"drop {name}", set(COINS) - {sym}, set(CORE_4) - {sym}, None))
    # grouped
    variants.append(("core4 only", set(CORE_4), set(CORE_4), None))
    variants.append(("no sats XRP/AVAX/LINK", set(CORE_4), set(CORE_4), None))
    variants.append(("ETH+ADA+SUI", {"ETHUSDT", "ADAUSDT", "SUIUSDT"}, {"ETHUSDT", "ADAUSDT", "SUIUSDT"}, None))
    variants.append(("ETH+ADA", {"ETHUSDT", "ADAUSDT"}, {"ETHUSDT", "ADAUSDT"}, None))
    variants.append(("no SOL+SUI", set(COINS) - {"SOLUSDT", "SUIUSDT"}, set(CORE_4) - {"SOLUSDT", "SUIUSDT"}, None))
    variants.append(("no SOL longs", set(COINS), set(CORE_4), lambda e: e["sym"] == "SOLUSDT" and e["side"] == 1))
    variants.append(("no SUI longs", set(COINS), set(CORE_4), lambda e: e["sym"] == "SUIUSDT" and e["side"] == 1))
    variants.append(("shorts only", set(COINS), set(CORE_4), lambda e: e["side"] == 1))
    variants.append(("core no SOL", set(CORE_4) - {"SOLUSDT"}, set(CORE_4) - {"SOLUSDT"}, None))

    rows = []
    focus = ["30", "90", "365", "full", "2025", "2026"]
    print("running variants...", flush=True)
    for vname, allow, core, skip in variants:
        rec = {"name": vname}
        for w in focus:
            res = run_sim(slices[w], allow, core=core, extra_skip=skip)
            rec[w] = pack(res)
            if w == "90" and vname == "BASE all7":
                rec["base90_coins"] = coin_pnl(res["trades"])
                rec["base90_reason"] = reason_coin(res["trades"])
                rec["base90_side"] = side_coin(res["trades"])
            if w == "365" and vname == "BASE all7":
                rec["base365_coins"] = coin_pnl(res["trades"])
                rec["base365_reason"] = reason_coin(res["trades"])
                rec["base365_side"] = side_coin(res["trades"])
            if w == "full" and vname == "BASE all7":
                rec["basefull_coins"] = coin_pnl(res["trades"])
                rec["basefull_side"] = side_coin(res["trades"])
        rows.append(rec)
        print(
            f"{vname:22s} 30={rec['30']['ret']:+7.1f} 90={rec['90']['ret']:+7.1f} "
            f"365={rec['365']['ret']:+7.1f} full={rec['full']['ret']:+8.1f} "
            f"2025={rec['2025']['ret']:+7.1f} 2026={rec['2026']['ret']:+7.1f}",
            flush=True,
        )

    # walk-forward: ban coins that were net-negative USD in prior window
    print("walk-forward bans...", flush=True)
    wf = {}
    base_2025 = run_sim(slices["2025"], set(COINS), core=set(CORE_4))
    pnl_2025 = coin_pnl(base_2025["trades"])
    ban_2025 = {sym for sym in COINS if pnl_2025.get(SHORT_NAME[sym], {}).get("usd", 0) < 0}
    wf_2026 = run_sim(slices["2026"], set(COINS) - ban_2025, core=set(CORE_4) - ban_2025)
    base_2026 = run_sim(slices["2026"], set(COINS), core=set(CORE_4))
    wf["ban_neg_2025_on_2026"] = {
        "banned": sorted(SHORT_NAME[s] for s in ban_2025),
        "pnl_2025": pnl_2025,
        "base_2026": pack(base_2026),
        "after": pack(wf_2026),
    }

    base_h1 = run_sim(slices["h1_2026"], set(COINS), core=set(CORE_4))
    pnl_h1 = coin_pnl(base_h1["trades"])
    ban_h1 = {sym for sym in COINS if pnl_h1.get(SHORT_NAME[sym], {}).get("usd", 0) < 0}
    wf_qtr = run_sim(slices["qtr"], set(COINS) - ban_h1, core=set(CORE_4) - ban_h1)
    base_qtr = run_sim(slices["qtr"], set(COINS), core=set(CORE_4))
    wf["ban_neg_h1_2026_on_qtr"] = {
        "banned": sorted(SHORT_NAME[s] for s in ban_h1),
        "pnl_h1": pnl_h1,
        "base_qtr": pack(base_qtr),
        "after": pack(wf_qtr),
    }

    # rolling: in 365d, ban coin if previous 90d USD < 0 (causal by trade time)
    # approximate: split 365 into 4 quarters, ban from prior quarter
    print("rolling 90d causal ban...", flush=True)
    y = slices["365"]
    if y:
        t0 = min(e["t"] for e in y)
        t1 = max(e["t"] for e in y)
        cuts = [t0 + i * 90 * DAY for i in range(5)]
        # simulate sequentially with carried equity? more honest for return but
        # we only want to see if banning last-quarter losers helps next quarter
        # vs independent $2000 starts (matches existing window style)
        rolling = []
        for i in range(1, 4):
            prior = [e for e in y if cuts[i - 1] <= e["t"] < cuts[i]]
            nxt = [e for e in y if cuts[i] <= e["t"] < (cuts[i + 1] if i + 1 < len(cuts) else t1 + 1)]
            if not prior or not nxt:
                continue
            rp = run_sim(prior, set(COINS), core=set(CORE_4))
            cp = coin_pnl(rp["trades"])
            ban = {sym for sym in COINS if cp.get(SHORT_NAME[sym], {}).get("usd", 0) < 0}
            rb = run_sim(nxt, set(COINS), core=set(CORE_4))
            ra = run_sim(nxt, set(COINS) - ban, core=set(CORE_4) - ban)
            rolling.append({
                "i": i,
                "banned": sorted(SHORT_NAME[s] for s in ban),
                "base": pack(rb),
                "after": pack(ra),
            })
        wf["rolling_90"] = rolling

    # extra realistic knobs on baseline universe
    knobs = []
    for name, kw in [
        ("conc=1", {"max_conc": 1}),
        ("sat_min=0.03", {"sat_min": 0.03}),
        ("risk=5%", {"risk_pct": 0.05}),
        ("risk=10%", {"risk_pct": 0.10}),
        ("no win_boost", {"win_boost": 1.0}),
    ]:
        rec = {"name": name}
        for w in ["90", "365", "full"]:
            rec[w] = pack(run_sim(slices[w], set(COINS), core=set(CORE_4), **kw))
        knobs.append(rec)
        print(f"knob {name:16s} 90={rec['90']['ret']:+7.1f} 365={rec['365']['ret']:+7.1f} full={rec['full']['ret']:+8.1f}", flush=True)

    out = {
        "variants": [
            {k: v for k, v in r.items() if not str(k).startswith("base")}
            for r in rows
        ],
        "base_detail": next((r for r in rows if r["name"] == "BASE all7"), {}),
        "walk_forward": wf,
        "knobs": knobs,
    }
    dest = LAB / "coin_lift_audit.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", dest, flush=True)


if __name__ == "__main__":
    main()
