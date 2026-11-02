p = r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_anatomy_top25.py"
t = open(p, encoding="utf-8").read()
old = "        n_waves, n_pull, deepest = waves_of(hold_moves, 1.0)\n"
new = old + "        w2, p2, _d2 = waves_of(hold_moves, 2.0)\n        w3, p3, _d3 = waves_of(hold_moves, 3.0)\n"
if old not in t:
    raise SystemExit("miss1")
t = t.replace(old, new, 1)
old = '            "waves": n_waves,\n            "pulls": n_pull,\n            "deepest_pull": round(deepest, 3),\n'
new = '            "waves": n_waves,\n            "pulls": n_pull,\n            "waves2": w2,\n            "pulls2": p2,\n            "waves3": w3,\n            "pulls3": p3,\n            "deepest_pull": round(deepest, 3),\n'
if old not in t:
    raise SystemExit("miss2")
t = t.replace(old, new, 1)
old = '        "waves": med([r["waves"] for r in rows]),\n        "pulls": med([r["pulls"] for r in rows]),\n'
new = old + '        "waves2": med([r["waves2"] for r in rows]),\n        "pulls2": med([r["pulls2"] for r in rows]),\n        "waves3": med([r["waves3"] for r in rows]),\n        "pulls3": med([r["pulls3"] for r in rows]),\n'
if old not in t:
    raise SystemExit("miss3")
t = t.replace(old, new, 1)
old = '                "waves": r["waves"],\n                "pulls": r["pulls"],\n'
new = old + '                "pulls2": r["pulls2"],\n                "pulls3": r["pulls3"],\n'
if old not in t:
    raise SystemExit("miss4")
t = t.replace(old, new, 1)
open(p, "w", encoding="utf-8", newline="\n").write(t)
print("patched")
