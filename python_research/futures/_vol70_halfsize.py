# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _chop_fade as F
import _regime_vol_cut as C

def half(rows):
    out = []
    for r in rows:
        c = dict(r)
        if r["rank"] > 0.80:
            c["net"] = c["net"] * 0.5
            c["pyr_net"] = c["pyr_net"] * 0.5
        out.append(c)
    return out

def main():
    rows = C.with_rank()
    skip = [r for r in rows if r["rank"] <= 0.80]
    books = {"rules": F.stamp(rows), "skip70": F.stamp(skip), "half70": F.stamp(half(rows))}
    print("FULL", {k: (v["full"]["final"], v["full"]["top5"], v["full"]["dd"]) for k, v in books.items()}, flush=True)
    for name, _, _ in F.periods():
        print(name, "rules", books["rules"][name]["ret"], "half", books["half70"][name]["ret"], "skip", books["skip70"][name]["ret"], flush=True)
    Path(__file__).resolve().parent.joinpath("_vol70_halfsize.json").write_text(
        json.dumps(books, ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    main()
