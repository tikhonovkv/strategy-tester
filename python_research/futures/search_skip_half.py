"""Four-coin long. Skip one trade after two losses.

If a half-year closes negative, the next half-year is not traded.
Both rules are fixed.
"""
from datetime import datetime, timezone

from causal import FEE
from futures_9to4 import load_rows
from search_z_ma import accept_one, pack
import search_stable as s


def half_of(ts):
    d = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return d.year, 1 if d.month <= 6 else 2


def half_end(key):
    y, h = key
    if h == 1:
        nxt = datetime(y, 7, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp() * 1000)


def next_half(key):
    y, h = key
    return (y, 2) if h == 1 else (y + 1, 1)


def net_of(e):
    if e["min"] <= -1.0:
        return -1.0
    return e["ret"] - e["paid"] - FEE


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    events = [e for e in accept_one(pack(rows, 1.5)) if e["side"] == 1]
    events.sort(key=lambda e: (e["t"], e["ci"]))
    equity = 2000.0
    peak = equity
    max_dd = 0.0
    half_pnl = {}
    applied = set()
    blocked = set()
    streak = 0
    skip_trade = False
    taken = skipped_trade = skipped_half = 0
    points = [(events[0]["t"] if events else 0, equity)]
    for e in events:
        t = e["t"]
        for key in sorted(half_pnl):
            if key in applied or half_end(key) > t:
                continue
            if half_pnl[key] < 0:
                blocked.add(next_half(key))
            applied.add(key)
        if half_of(t) in blocked:
            skipped_half += 1
            continue
        if skip_trade:
            skip_trade = False
            skipped_trade += 1
            continue
        before = equity
        equity += equity * 0.2 * net_of(e)
        peak = max(peak, equity)
        if peak:
            max_dd = max(max_dd, (peak - equity) / peak)
        half_pnl[half_of(t + e["hold"])] = half_pnl.get(half_of(t + e["hold"]), 0.0) + (equity - before)
        points.append((t + e["hold"], equity))
        taken += 1
        if net_of(e) < 0:
            streak += 1
            if streak >= 2:
                skip_trade = True
                streak = 0
        else:
            streak = 0
    print(
        f"taken={taken} skip_trade={skipped_trade} skip_half={skipped_half} "
        f"final={equity:.0f} ret={(equity/2000-1)*100:+.1f}% dd={max_dd*100:.0f}%",
        flush=True,
    )
    plus = 0
    for y in range(2021, 2027):
        for h, start_m, end_y, end_m in ((1, 1, y, 7), (2, 7, y + 1, 1)):
            lo = int(datetime(y, start_m, 1, tzinfo=timezone.utc).timestamp() * 1000)
            hi = int(datetime(end_y, end_m, 1, tzinfo=timezone.utc).timestamp() * 1000)
            begin = end = 2000.0
            for ts, eq in points:
                if ts < lo:
                    begin = eq
                if ts < hi:
                    end = eq
            ret = (end / begin - 1.0) * 100 if begin else 0.0
            if ret > 0.05:
                plus += 1
            mark = " skip" if (y, h) in blocked else ""
            print(f"{y}H{h} {ret:+.1f}%{mark}", flush=True)
    print(f"positive halves {plus}/12", flush=True)


if __name__ == "__main__":
    main()
