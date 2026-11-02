from pathlib import Path
p = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_pitbull_reaction_nn.py")
t = p.read_text(encoding="utf-8")
needle = "def nn_cascade(s, btc, btc_map, series, i, side, models, thr):\n"
insert = '''_PROB = {}


def cached_probs(s, series, i, side, models):
    key = (id(models.get("manage")), s["sym"], int(i), int(side))
    hit = _PROB.get(key)
    if hit is not None:
        return hit
    x0, atr = entry_x(s, series, i, side)
    moves = forward_moves(s, i, side, atr)
    vol = s["vol"]
    hour_x = []
    peak = -1e9
    worst = 1e9
    for h, cur in enumerate(moves, start=1):
        j = i + h
        peak = max(peak, cur)
        worst = min(worst, cur)
        base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
        hour_x.append(np.concatenate([x0, np.array([
            h / 96.0, clip(cur, -6, 12), clip(peak, -2, 12), clip(peak - cur, 0, 12),
            clip(worst, -6, 2), clip(moves[0], -6, 8), clip(moves[min(2, len(moves) - 1)], -6, 8),
            clip(float(vol[j]) / max(base, 1e-8), 0, 5),
        ], dtype=np.float64)]))
    X = np.vstack(hour_x) if hour_x else np.zeros((0, 16))
    packed = (class_p(models["manage"], X, CUT), class_p(models["manage"], X, LOCK), moves, atr)
    _PROB[key] = packed
    return packed


'''
if needle not in t:
    raise SystemExit("no def")
t = t.replace(needle, insert + needle, 1)
old = '''        rows, packet = manage_rows(s, series, cursor, side)
        if packet is None:
            break
        x0, moves, atr = packet
        xs = np.vstack([r[0] for r in rows]) if rows else np.zeros((0, 16))
        # rows are a subset of hours; recompute proba on every hour so the walk aligns
        hour_x = []
        peak = -1e9
        worst = 1e9
        vol = s["vol"]
        for h, cur in enumerate(moves, start=1):
            j = cursor + h
            peak = max(peak, cur)
            worst = min(worst, cur)
            base = float(np.median(vol[max(0, j - 24):j])) if j > 1 else float(vol[j])
            hour_x.append(np.concatenate([x0, np.array([
                h / 96.0, clip(cur, -6, 12), clip(peak, -2, 12), clip(peak - cur, 0, 12),
                clip(worst, -6, 2), clip(moves[0], -6, 8), clip(moves[min(2, len(moves) - 1)], -6, 8),
                clip(float(vol[j]) / max(base, 1e-8), 0, 5),
            ], dtype=np.float64)]))
        X = np.vstack(hour_x) if hour_x else np.zeros((0, 16))
        p_cut = class_p(models["manage"], X, CUT)
        p_lock = class_p(models["manage"], X, LOCK)
'''
new = '''        p_cut, p_lock, moves, atr = cached_probs(s, series, cursor, side, models)
        if len(moves) < 2:
            break
'''
if old not in t:
    raise SystemExit("no body")
t = t.replace(old, new, 1)
p.write_text(t, encoding="utf-8", newline="\n")
print("ok", t.count("cached_probs"))
