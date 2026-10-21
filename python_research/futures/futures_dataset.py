"""Build the causal futures dataset and write futures_dataset.json.

Each row is one coin at one funding timestamp. Features are known at that
timestamp. ret / paid / min_ret / max_ret are the 24h outcome and are not
features.
"""
from __future__ import annotations

import json
import sys
import time

from causal import COINS, LAB, build_all, prove_no_lookahead


def pack(row):
    return [
        int(row["t"]),
        [round(float(v), 8) for v in row["feat"]],
        round(float(row["ret"]), 6),
        round(float(row["paid"]), 8),
        round(float(row["z"]), 6),
        round(float(row["min_ret"]), 6),
        round(float(row["max_ret"]), 6),
        round(float(row["btc_r1d"]), 6),
        round(float(row["btc_r7d"]), 6),
    ]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--prove" in sys.argv:
        prove_no_lookahead()
        return
    t0 = time.time()
    built = build_all(with_labels=True)
    out = {}
    total = 0
    for sym in COINS:
        rows = built[sym]
        total += len(rows)
        out[sym] = [pack(r) for r in rows]
        print(f"{sym}: rows={len(rows)}", flush=True)
    print(f"TOTAL rows={total} ({time.time()-t0:.0f}s)", flush=True)
    (LAB / "futures_dataset.json").write_text(json.dumps(out), encoding="utf-8")
    print("saved", LAB / "futures_dataset.json", flush=True)
    prove_no_lookahead()


if __name__ == "__main__":
    main()
