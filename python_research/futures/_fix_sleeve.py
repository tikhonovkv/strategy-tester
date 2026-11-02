from pathlib import Path
p = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_sleeve_recheck.py")
t = p.read_text(encoding="utf-8")
old = '''    raw = p1 / p0 - 1.0
    paid = funding_paid(s, t0, t1)
    return raw - paid if side == 1 else -raw + paid
'''
new = '''    raw = p1 / p0 - 1.0
    paid = funding_paid(s, t0, t1)
    ret = raw - paid if side == 1 else -raw + paid
    return max(ret, -1.0)
'''
if old not in t:
    raise SystemExit("ret missing")
t = t.replace(old, new, 1)
old = '''    btc = series["BTCUSDT"]
    by_t = {}
'''
new = '''    btc = series["BTCUSDT"]
    rebalance = set(rebalance_times(btc, start, end))
    by_t = {}
'''
if old not in t:
    raise SystemExit("btc missing")
t = t.replace(old, new, 1)
t = t.replace("times.add(t)\n", "times.add(t)\n", 1)
t = t.replace(
'''        for t in rebalance_times(btc, start, end):
            times.add(t)
''',
'''        for rt in rebalance:
            times.add(rt)
'''
)
old = '''        if t in set(rebalance_times(btc, start, end)):
            released, fee, sleeve = close_sleeve(sleeve, series, t, 1.0)
            sleeve_cash += released - fee
            if fee:
                trades.append(-fee)
            weights = target_weights(series, syms, t)
            if mode == "split":
                pot = sleeve_cash
            else:
                pot = cash_s if len(open_pos) == 0 else 0.0
                if mode == "idle" and pot > 0:
                    cash_s -= pot
                    sleeve_cash += pot
            if pot > 50.0 and weights:
                sleeve_cash, sleeve = deploy_sleeve(sleeve_cash, weights, t)
            elif mode == "idle" and pot > 0 and not weights:
                cash_s += sleeve_cash
                sleeve_cash = 0.0
'''
new = '''        if t in rebalance:
            released, fee, sleeve = close_sleeve(sleeve, series, t, 1.0)
            sleeve_cash += released - fee
            if fee:
                trades.append(-fee)
            weights = target_weights(series, syms, t)
            if mode == "idle" and open_pos:
                cash_s += sleeve_cash
                sleeve_cash = 0.0
                weights = {}
            elif mode == "idle":
                sleeve_cash += cash_s
                cash_s = 0.0
            if sleeve_cash > 50.0 and weights:
                sleeve_cash, sleeve = deploy_sleeve(sleeve_cash, weights, t)
'''
if old not in t:
    raise SystemExit("block missing")
t = t.replace(old, new, 1)
p.write_text(t, encoding="utf-8", newline="\n")
print("patched")
