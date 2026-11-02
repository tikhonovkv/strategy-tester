"""One account: long z=1.5, above the 10-day average, exit on the average."""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import book
from search_z_ma import accept_one, pack
import search_stable as s


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    events = [e for e in accept_one(pack(rows, 1.5)) if e["side"] == 1]
    res = book(events)
    print(
        f"n={res['n']} final={res['final']:.0f} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%",
        flush=True,
    )
    halves = half_year_returns(res["curve"], 2000.0)
    plus = 0
    for k, v in halves.items():
        if v > 0:
            plus += 1
        print(f"{k} {v:+.1f}%", flush=True)
    print(f"positive halves {plus}/{len(halves)}", flush=True)


if __name__ == "__main__":
    main()
