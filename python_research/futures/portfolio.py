"""Position accounting.

PnL is realized when the position exits. A Schwartz exit can be earlier than
24h. Stake is a fraction of equity at the entry timestamp. Future PnL is not
included in that equity. Loss is capped at the isolated margin.
"""
from __future__ import annotations

import bisect

from causal import DAY, FEE


def _leg(side: int, row: dict):
    if side == 1 and "ret_l" in row:
        return row["ret_l"], row["paid_l"], row["min_l"], row["max_l"]
    if side == -1 and "ret_s" in row:
        return row["ret_s"], row["paid_s"], row["min_s"], row["max_s"]
    return row["ret"], row["paid"], row["min_ret"], row["max_ret"]


def hold_ms(row: dict, side: int) -> int:
    if side == 1 and "hold_l" in row:
        return int(row["hold_l"])
    if side == -1 and "hold_s" in row:
        return int(row["hold_s"])
    return DAY


def trade_pnl(side: int, row: dict, margin: float, leverage: float, fee: float = FEE) -> float:
    ret, paid, min_ret, max_ret = _leg(side, row)
    if side == 1:
        if min_ret * leverage <= -1.0:
            return -margin
        actual = ret - paid - fee
    else:
        if (-max_ret) * leverage <= -1.0:
            return -margin
        actual = -ret + paid - fee
    pnl = margin * leverage * actual
    if pnl < -margin:
        return -margin
    return pnl


def simulate(events: list[dict], choose, leverage: float, stake: float, start: float = 2000.0):
    """choose(row) -> (side, score). side 0 skips. Events are labeled rows."""
    by_t: dict[int, list] = {}
    times = set()
    for e in events:
        by_t.setdefault(int(e["t"]), []).append(e)
        times.add(int(e["t"]))
        times.add(int(e["t"]) + DAY)
    free = float(start)
    reserved = 0.0
    open_pos = []
    trades = []
    curve = [(min(times) if times else 0, free)]
    peak = free
    max_dd = 0.0

    def mark(ts, equity):
        nonlocal peak, max_dd
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        curve.append((int(ts), equity))

    clock = sorted(times)
    seen = set(clock)
    idx = 0
    while idx < len(clock):
        t = clock[idx]
        idx += 1
        still = []
        for p in open_pos:
            if p["exit"] <= t:
                pnl = trade_pnl(p["side"], p["row"], p["margin"], leverage)
                free += p["margin"] + pnl
                reserved -= p["margin"]
                equity = free + reserved
                trades.append({
                    "t": p["row"]["t"],
                    "exit": p["exit"],
                    "ci": p["row"]["ci"],
                    "side": p["side"],
                    "contrib": pnl / p["equity_at_entry"] if p["equity_at_entry"] else 0.0,
                    "pnl": pnl,
                    "equity": equity,
                })
                mark(t, equity)
            else:
                still.append(p)
        open_pos = still
        equity = free + reserved
        if equity <= 0:
            break
        cands = []
        for row in by_t.get(t, []):
            side, score = choose(row)
            if side != 0 and score > 0:
                cands.append((score, side, row))
        cands.sort(key=lambda x: x[0], reverse=True)
        busy = {p["ci"] for p in open_pos}
        for score, side, row in cands:
            ci = row["ci"]
            if ci in busy:
                continue
            margin = stake * equity
            if margin <= 1e-9 or free < margin - 1e-6:
                continue
            free -= margin
            reserved += margin
            exit_at = int(row["t"]) + hold_ms(row, side)
            if exit_at not in seen:
                seen.add(exit_at)
                bisect.insort(clock, exit_at)
            open_pos.append({
                "ci": ci,
                "side": side,
                "row": row,
                "margin": margin,
                "exit": exit_at,
                "equity_at_entry": equity,
            })
            busy.add(ci)
        if free + reserved <= 0:
            break

    if open_pos:
        for p in sorted(open_pos, key=lambda x: x["exit"]):
            pnl = trade_pnl(p["side"], p["row"], p["margin"], leverage)
            free += p["margin"] + pnl
            reserved -= p["margin"]
            equity = free + reserved
            trades.append({
                "t": p["row"]["t"],
                "exit": p["exit"],
                "ci": p["row"]["ci"],
                "side": p["side"],
                "contrib": pnl / p["equity_at_entry"] if p["equity_at_entry"] else 0.0,
                "pnl": pnl,
                "equity": equity,
            })
            mark(p["exit"], equity)
    final = free + reserved
    return {
        "final": final,
        "start": start,
        "return": final / start - 1.0 if start else 0.0,
        "trades": trades,
        "max_dd": max_dd,
        "curve": curve,
        "n": len(trades),
    }
