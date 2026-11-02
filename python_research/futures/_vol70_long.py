# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _chop_fade as F
import _regime_vol_cut as C
import _profit_lock as L
import schwartz_production as sp

def main():
    rows = C.with_rank()
    kept = [r for r in rows if r["rank"] <= 0.70]
    rules = F.stamp(rows)
    filt = F.stamp(kept)
    print("rules", rules["full"], flush=True)
    print("skip70", filt["full"], flush=True)
    for name, _, _ in F.periods():
        print(name, "rules", rules[name]["ret"], "skip", filt[name]["ret"], "top5", filt[name]["top5"], flush=True)
    out = {"rules": rules, "skip70": filt, "change_bot": False}
    Path(__file__).resolve().parent.joinpath("_vol70_long.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    main()
