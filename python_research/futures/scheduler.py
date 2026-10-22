# -*- coding: utf-8 -*-
"""Docker entrypoint: Continuous 5-minute Live Account Verification + Hourly Bar Refresh.

Schedule:
  - On startup: immediately seeds JSONs, refreshes Bybit candles (`bootstrap.py`), runs
    `live_bot.py` and `compare_live.py`.
  - Every 5 minutes: runs `live_bot.py` (checks open positions for fast_bail / stops / TP
    and updates live account status) and `compare_live.py` (refreshes real Bybit closed PnL
    and trade verification).
  - Every hour at :02 UTC: runs `bootstrap.py` + `schwartz_production.py` to pull the newly
    closed hourly candle and evaluate new entry signals.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

MODE = "--live" if os.environ.get("LIVE_MODE") == "1" else "--dry-run"


def run(args: list[str]) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] RUN {' '.join(args)}", flush=True)
    r = subprocess.run([sys.executable, *args], check=False, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-1500:], flush=True)
    if r.returncode != 0:
        print(f"[ERROR] {' '.join(args)} exit={r.returncode}", flush=True)
        print((r.stderr or "")[-2500:], flush=True)


def fast_startup_seed() -> None:
    run(["schwartz_production.py"])
    run(["live_bot.py", MODE, "--hourly-close"])
    run(["compare_live.py"])


def hourly_bar_refresh() -> None:
    if os.environ.get("BOOTSTRAP", "1") != "0":
        run(["bootstrap.py"])
        run(["futures_dataset.py", "--no-oi"])
    run(["schwartz_production.py"])


def five_minute_verification(is_hourly: bool = False) -> None:
    cmd = ["live_bot.py", MODE]
    if is_hourly:
        cmd.append("--hourly-close")
    run(cmd)
    run(["compare_live.py"])


if __name__ == "__main__":
    fast_startup_seed()
    hourly_bar_refresh()
    five_minute_verification(is_hourly=True)
    last_bar_hour = datetime.now(timezone.utc).hour
    while True:
        time.sleep(300)  # Check every 5 minutes
        now = datetime.now(timezone.utc)
        did_hourly = False
        if now.hour != last_bar_hour and now.minute >= 2:
            hourly_bar_refresh()
            last_bar_hour = now.hour
            did_hourly = True
        five_minute_verification(is_hourly=did_hourly)
