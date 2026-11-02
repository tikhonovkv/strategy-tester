p = r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_stabilize_audit.py"
t = open(p, encoding="utf-8").read()
old = """    lock_ts = int(s["close_ts"][i])
    for j in range(i + 1, min(len(s["px"]), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
            break
"""
new = """    lock_ts = int(s["close_ts"][i])
    exit_ts = int(full["exit_ts"])
    for j in range(i + 1, min(len(s["px"]), i + 96)):
        if int(s["close_ts"][j]) != int(s["close_ts"][j - 1]) + sp.HOUR:
            break
        if int(s["close_ts"][j]) > exit_ts:
            break
"""
if old not in t:
    raise SystemExit("missing")
open(p, "w", encoding="utf-8", newline="\n").write(t.replace(old, new, 1))
print("patched")
