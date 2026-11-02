"""Same long z=1.5 rules on all 9 coins. No new threshold."""
from futures_9to4 import half_year_returns, load_rows
from search_ma_one import book
from search_z_ma import side_of
import search_stable as s


def events(rows):
    out = []
    for r in rows:
        if "ma_gap" not in r or "hold_l" not in r:
            continue
        side = side_of(r, 1.5)
        if side != 1 or side * r["ma_gap"] <= 0:
            continue
        out.append({
            "t": int(r["t"]), "ci": r["ci"], "side": 1, "score": abs(float(r["z"])),
            "ma_gap": r["ma_gap"], "hold": r["hold_l"],
            "ret": r["ret_l"], "paid": r["paid_l"], "min": r["min_l"], "max": r["max_l"],
            "ret_l": r["ret_l"], "paid_l": r["paid_l"], "min_l": r["min_l"], "max_l": r["max_l"], "hold_l": r["hold_l"],
            "ret_s": r["ret_s"], "paid_s": r["paid_s"], "min_s": r["min_s"], "max_s": r["max_s"], "hold_s": r["hold_s"],
        })
    open_until = 0
    kept = []
    for r in sorted(out, key=lambda x: (x["t"], -x["score"], x["ci"])):
        if r["t"] < open_until:
            continue
        kept.append(r)
        open_until = r["t"] + r["hold"]
    return kept


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    res = book(events(rows))
    print(
        f"n={res['n']} final={res['final']:.0f} ret={res['return']*100:+.1f}% dd={res['max_dd']*100:.0f}%",
        flush=True,
    )
    halves = half_year_returns(res["curve"], 2000.0)
    plus = sum(1 for v in halves.values() if v > 0)
    for k, v in halves.items():
        print(f"{k} {v:+.1f}%", flush=True)
    print(f"positive halves {plus}/{len(halves)}", flush=True)


if __name__ == "__main__":
    main()
