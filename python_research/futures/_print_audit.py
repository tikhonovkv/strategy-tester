import json
d = json.load(open(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_stabilize_audit.json", encoding="utf-8"))["report"]
labels = ["2022", "2023", "2024", "2025", "2026H1", "2026Q", "full"]
for name, rows in d.items():
    print("==", name)
    for lab in labels:
        r = rows[lab]
        print(" ", lab, r)
