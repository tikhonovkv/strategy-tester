# -*- coding: utf-8 -*-
"""Docker entrypoint: run the Schwartz Pitbull Championship 7x pipeline every hour (+2 min).

On startup:
  1. Immediately exports precomputed backtest & live verification files (<5s) so the
     web dashboard is populated right away.
  2. Refreshes Bybit hourly bars & funding (`bootstrap.py`), re-exports windows, runs
     `live_bot.py` and `compare_live.py`.
Every hour at :02 UTC:
  Refreshes bars, checks open positions for fast_bail / TP1 / TP2 / 10d EMA exit,
  opens new signals, and updates `futures_compare.json` + `futures_signals.json`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

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
    """Populate output JSONs immediately on container boot using bundled data."""
    run(["schwartz_production.py"])
    run(["live_bot.py", MODE])
    run(["compare_live.py"])


def full_cycle() -> None:
    if os.environ.get("BOOTSTRAP", "1") != "0":
        run(["bootstrap.py"])
        run(["futures_dataset.py", "--no-oi"])
    run(["schwartz_production.py"])
    run(["live_bot.py", MODE])
    run(["compare_live.py"])


def next_hourly_target() -> datetime:
    now = datetime.now(timezone.utc)
    target = now.replace(minute=2, second=0, microsecond=0)
    if target <= now:
        target += timedelta(hours=1)
    return target


if __name__ == "__main__":
    fast_startup_seed()
    full_cycle()
    while True:
        target = next_hourly_target()
        sleep_s = (target - datetime.now(timezone.utc)).total_seconds()
        print(f"sleep {sleep_s/60:.1f}m until {target.isoformat()}", flush=True)
        time.sleep(max(sleep_s, 1.0))
        full_cycle()
