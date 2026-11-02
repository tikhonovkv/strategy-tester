"""ETH only, same long rules and the same skip after two losses."""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import book
from search_skip import skip_after_two
from search_z_ma import accept_one, pack
import search_stable as s

ETH = 1


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    events = [e for e in accept_one(pack(rows, 1.5)) if e["side"] == 1 and e["ci"] == ETH]
    events.sort(key=lambda e: e["t"])
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
