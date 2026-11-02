from pathlib import Path
p = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_pitbull_master_audit.py")
t = p.read_text(encoding="utf-8")
old = '        if not allow or not leg["win"] or leg["reason"] not in REENTRY_OK:\n'
new = '        reasons = spec.get("reentry_reasons") or REENTRY_OK\n        if not allow or not leg["win"] or leg["reason"] not in reasons:\n'
if old not in t:
    raise SystemExit("missing")
p.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")
print("patched")
