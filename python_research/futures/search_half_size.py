"""Long z=1.5. After a losing calendar month, stake is halved until a month closes up.

The size rule is fixed. Nothing is chosen on 2025 or 2026.
"""
from collections import defaultdict
from datetime import datetime, timezone

from causal import FEE
from futures_9to4 import load_rows
from search_ma_one import Y2025, Y2026
from search_z_ma import accept_one, pack
import search_stable as s


def month_of(ts):
    d = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return d.year, d.month


def month_end(y, m):
    if m == 12:
        nxt = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(y, m + 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp() * 1000)


def year_return(points, year, start_eq):
    lo = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    hi = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    begin = start_eq
    end = start_eq
    for ts, eq in points:
        if ts < lo:
            begin = eq
        if ts < hi:
            end = eq
    return (end / begin - 1.0) if begin else 0.0, begin, end


def main():
    rows = load_rows()
    print(s.apply_schwartz(rows), flush=True)
    s.add_ma_gap(rows)
    events = [e for e in accept_one(pack(rows, 1.5)) if e["side"] == 1]
    events.sort(key=lambda e: (e["t"], e["ci"]))
    equity = 2000.0
    peak = equity
    max_dd = 0.0
    half = False
    month_pnl = defaultdict(float)
    applied = set()
    points = [(events[0]["t"] if events else 0, equity)]
    half_n = 0
    for e in events:
        t = e["t"]
        for ym in sorted(month_pnl):
            if ym in applied or month_end(*ym) > t:
                continue
            if month_pnl[ym] < 0:
                half = True
            elif month_pnl[ym] > 0:
                half = False
            applied.add(ym)
        stake = 0.1 if half else 0.2
        if half:
            half_n += 1
        if e["min"] <= -1.0:
            net = -1.0
        else:
            net = max(-1.0, e["ret"] - e["paid"] - FEE)
        equity += equity * stake * net
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        exit_t = t + e["hold"]
        month_pnl[month_of(exit_t)] += equity * 0  # placeholder to keep month key
        # Record the dollar result on the exit month. Recompute the increment just applied.
        month_pnl[month_of(exit_t)] += (equity - points[-1][1])
        points.append((exit_t, equity))
    print(f"half-size trades {half_n}/{len(events)} final={equity:.0f} dd={max_dd*100:.0f}%", flush=True)
    begin = 2000.0
    for year, nxt in ((2024, Y2025), (2025, Y2026), (2026, 10**18)):
        ret, b, e = year_return(points, year, begin)
        print(f"{year} ret={ret*100:+.1f}% from {b:.0f} to {e:.0f}", flush=True)


if __name__ == "__main__":
    main()
