# -*- coding: utf-8 -*-
import json
from pathlib import Path
import _chop_fade as F
import _regime_vol_cut as C
import _vol70_halfsize as H

def main():
    rows = C.with_rank()
    books = {"rules": F.stamp(rows), "half80": F.stamp(H.half(rows))}
    report = {}
    for name in ("2025", "2026Q"):
        report[name] = {
            "rules_top5": books["rules"][name]["top5"],
            "half_top5": books["half80"][name]["top5"],
            "rules_ret": books["rules"][name]["ret"],
            "half_ret": books["half80"][name]["ret"],
        }
        print(name, report[name], flush=True)
    high = all(report[k]["half_top5"] >= 0.9 for k in report)
    out = {"rows": report, "dependence_remains": high, "change_bot": False}
    Path(__file__).resolve().parent.joinpath("_half80_top5.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("dependence_remains", high, flush=True)

if __name__ == "__main__":
    main()
