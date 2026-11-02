"""Four-coin long z=1.5. After two losing trades, skip the next one."""
from causal import FEE
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import book
from search_z_ma import accept_one, pack
import search_stable as s


def net_of(e):
    if e["min"] <= -1.0:
        return -1.0
    return e["ret"] - e["paid"] - FEE


def skip_after_two(events):
    streak = 0
    skip = False
    kept = []
    skipped = 0
    for e in events:
        if skip:
            skip = False
            skipped += 1
            continue
        kept.append(e)
        if net_of(e) < 0:
            streak += 1
            if streak >= 2:
                skip = True
                streak = 0
        else:
            streak = 0
    return kept, skipped


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    events = [e for e in accept_one(pack(rows, 1.5)) if e["side"] == 1]
    events.sort(key=lambda e: (e["t"], e["ci"]))
    kept, skipped = skip_after_two(events)
    res = book(kept)
    print(
        f"taken={res['n']} skipped={skipped} final={res['final']:.0f} "
        f"ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%",
        flush=True,
    )
    halves = half_year_returns(res["curve"], 2000.0)
    plus = sum(1 for v in halves.values() if v > 0)
    for k, v in halves.items():
        print(f"{k} {v:+.1f}%", flush=True)
    print(f"positive halves {plus}/{len(halves)}", flush=True)


if __name__ == "__main__":
    main()
