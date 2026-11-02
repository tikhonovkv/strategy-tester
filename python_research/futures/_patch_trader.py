# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(__file__).with_name("schwartz_trader.py")
t = p.read_text(encoding="utf-8")

def sub(old, new):
    global t
    if old not in t:
        raise SystemExit("missing block:\n" + old[:180])
    t = t.replace(old, new, 1)

sub(
    "            if not reclaim and r4 <= 0.0:\n                continue",
    "            if not reclaim and r4 < 0.75:\n                continue",
)

sub(
'''def _best_threshold(val: list[dict]) -> float:
    if len(val) < 12:
        return -1e9
    scores = np.array([e["nn_score"] for e in val], dtype=np.float64)
    best_thr = float(np.min(scores) - 1e-6)
    best_ret = -1e9
    for q in (0.20, 0.40, 0.60, 0.75, 0.90):
        thr = float(np.quantile(scores, q))
        part = [e for e in val if e["nn_score"] >= thr]
        if len(part) < 4:
            continue
        res = simulate_window(part, core_gate=False, rank="nn")
        if res["n"] < 4:
            continue
        score = res["ret"] - max(0.0, res["dd"] - 35.0) * 1.5
        if score > best_ret:
            best_ret = score
            best_thr = thr
    return best_thr
''',
'''def _one_a_day(rows, thr):
    best = {}
    for e in rows:
        if e.get("nn_score", -1e9) < thr:
            continue
        day = (e["t"] // DAY) * DAY
        cur = best.get(day)
        if cur is None or e["nn_score"] > cur["nn_score"]:
            best[day] = e
    return list(best.values())


def _best_threshold(val: list[dict]) -> float:
    if len(val) < 12:
        return 1e9
    scores = np.array([e["nn_score"] for e in val], dtype=np.float64)
    best_thr = float(np.quantile(scores, 0.97))
    best_key = -1e9
    for q in (0.50, 0.70, 0.85, 0.93, 0.97):
        thr = float(np.quantile(scores, q))
        part = _one_a_day(val, thr)
        if not (3 <= len(part) <= 18):
            continue
        res = simulate_window(part, core_gate=False, rank="nn")
        if not (3 <= res["n"] <= 18):
            continue
        key = res["ret"] - max(0.0, res["dd"] - 25.0) * 2.0
        if key > best_key:
            best_key = key
            best_thr = thr
    return best_thr
'''
)

sub(
'''        _apply_scores(day_rows, net)
        for e in day_rows:
            if e["nn_score"] >= thr:
                row = dict(e)
                row.pop("nn_mult", None)
                picked.append(row)
    return picked
''',
'''        _apply_scores(day_rows, net)
        for e in _one_a_day(day_rows, thr):
            row = dict(e)
            row.pop("nn_mult", None)
            picked.append(row)
    return picked
'''
)

sub(
'''        alpha=2.5,
        max_iter=280,''',
'''        alpha=0.6,
        max_iter=320,'''
)

p.write_text(t, encoding="utf-8", newline="\n")
print("patched")
