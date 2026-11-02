# -*- coding: utf-8 -*-
"""Reclaim-only book: same net, classic Schwartz trigger, all majors."""
from schwartz_trader import (
    _load_universe,
    build_events,
    walk_forward,
    windows,
    coin_stats,
    save_net,
)
from schwartz_production import build_tranche_candidates, simulate_window
from test_schwartz_hybrid import Q_CUT, Y2026

def show(name, res):
    bits = ["%-16s" % name]
    for w in ("30", "90", "365", "2025", "2026", "full"):
        bits.append("%s=%+6.1f/%0.fn%d" % (w, res[w]["ret"], res[w]["dd"], res[w]["n"]))
    print("  ".join(bits), flush=True)

def main():
    series = _load_universe()
    events = build_events(series)
    reclaim = [e for e in events if e.get("reclaim")]
    print("reclaim=%d of %d" % (len(reclaim), len(events)), flush=True)
    picked = walk_forward(reclaim)
    print("picked=%d" % len(picked), flush=True)
    show("RECLAIM+NN", windows(picked, core_gate=False, rank="nn"))
    base, _ = build_tranche_candidates()
    show("BASE", windows(base, core_gate=True, rank="rules"))
    y26 = [e for e in picked if e["t"] >= Y2026]
    qtr = [e for e in picked if e["t"] >= Q_CUT]
    print("2026", coin_stats(simulate_window(y26, core_gate=False, rank="nn")["trades"]), flush=True)
    print("qtr", coin_stats(simulate_window(qtr, core_gate=False, rank="nn")["trades"]), flush=True)
    btc = [t for t in simulate_window(y26, core_gate=False, rank="nn")["trades"] if t["coin"] == "BTC"]
    print("btc 2026 n", len(btc), "usd", round(sum(t["pnl_usd"] for t in btc), 1), flush=True)

if __name__ == "__main__":
    main()
