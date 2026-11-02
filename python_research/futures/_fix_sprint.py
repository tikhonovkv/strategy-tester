from pathlib import Path
p = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_sprint_reentry.py")
t = p.read_text(encoding="utf-8")
t = t.replace("import heapq\r\nimport heapq\r\n", "import heapq\r\n")
t = t.replace("import heapq\nimport heapq\n", "import heapq\n")
old = "times.add(child[\"t\"])"
new = "wake(child[\"t\"])"
if old not in t:
    raise SystemExit("miss child")
t = t.replace(old, new)
t = t.replace("times.add(child[\"exit_ts\"])", "wake(child[\"exit_ts\"])")
t = t.replace("times.add(leg[\"exit_ts\"])", "wake(leg[\"exit_ts\"])")
if "times.add" in t:
    raise SystemExit("times.add remains")
p.write_text(t, encoding="utf-8", newline="\n")
print("patched", t.count("wake"))
