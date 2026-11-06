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

from accounts_config import enabled_accounts, write_account_json

MODE = "--live" if os.environ.get("LIVE_MODE") == "1" else "--dry-run"


def set_calc_status(calculating: bool, stage: str = "") -> None:
    try:
        write_account_json(
            "futures_calc_status",
            "main",
            {
                "calculating": bool(calculating),
                "stage": stage,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            },
            persist=True,
        )
    except Exception:
        pass


def run(args: list[str]) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] RUN {' '.join(args)}", flush=True)
    r = subprocess.run([sys.executable, *args], check=False, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-1500:], flush=True)
    if r.returncode != 0:
        print(f"[ERROR] {' '.join(args)} exit={r.returncode}", flush=True)
        print((r.stderr or "")[-2500:], flush=True)


def get_enabled_accounts() -> list[dict]:
    try:
        accounts = enabled_accounts()
    except Exception as exc:
        print(f"[WARN] enabled_accounts fallback to main: {exc}", flush=True)
        accounts = [{"id": "main"}]
    return accounts or [{"id": "main"}]


def run_live_bot_accounts(is_hourly: bool = False) -> None:
    for acc in get_enabled_accounts():
        acc_id = str(acc.get("id") or "main")
        cmd = ["live_bot.py", MODE, "--account", acc_id]
        if is_hourly:
            cmd.append("--hourly-close")
        run(cmd)


def run_compare_accounts() -> None:
    for acc in get_enabled_accounts():
        acc_id = str(acc.get("id") or "main")
        run(["compare_live.py", "--account", acc_id])


def run_all_accounts(is_hourly: bool = False) -> None:
    for acc in get_enabled_accounts():
        acc_id = str(acc.get("id") or "main")
        cmd = ["live_bot.py", MODE, "--account", acc_id]
        if is_hourly:
            cmd.append("--hourly-close")
        run(cmd)
        run(["compare_live.py", "--account", acc_id])


def fast_startup_seed() -> None:
    # Do NOT run schwartz_production before bootstrap.py, otherwise stale seed bars overwrite S3!
    set_calc_status(True, "Идёт расчёт стратегии и загрузка свежих свечей после запуска — подождите 1–2 минуты...")


def hourly_bar_refresh() -> None:
    set_calc_status(True, "Идёт обновление часовых свечей и пересчёт стратегии — подождите 1–2 минуты...")
    try:
        if os.environ.get("BOOTSTRAP", "1") != "0":
            run(["bootstrap.py"])
        run(["schwartz_production.py"])
    finally:
        set_calc_status(False, "Актуально")


def five_minute_verification(is_hourly: bool = False) -> None:
    run_all_accounts(is_hourly=is_hourly)
    set_calc_status(False, "Актуально")


def sleep_until_next_tick() -> None:
    """Sleep until the next 5-minute mark, waking up at HH:00:02 UTC on the hour boundary."""
    now_ts = time.time()
    next_hour = (int(now_ts) // 3600 + 1) * 3600 + 2.0
    next_5m = (int(now_ts) // 300 + 1) * 300 + 2.0
    target = min(next_hour, next_5m)
    wait_s = max(1.0, min(305.0, target - now_ts))
    time.sleep(wait_s)


if __name__ == "__main__":
    if os.environ.get("BOOTSTRAP", "1") != "0":
        run(["bootstrap.py", "--fast"])
    run_live_bot_accounts(is_hourly=True)
    hourly_bar_refresh()
    run_compare_accounts()
    last_bar_hour = datetime.now(timezone.utc).hour
    while True:
        sleep_until_next_tick()
        now = datetime.now(timezone.utc)
        if now.hour != last_bar_hour:
            # 1. Fast pull of the newly closed hourly candle (~1s) + immediate live trade execution at HH:00:02 UTC!
            if os.environ.get("BOOTSTRAP", "1") != "0":
                run(["bootstrap.py", "--fast"])
            run_live_bot_accounts(is_hourly=True)
            last_bar_hour = now.hour
            # 2. Full 25-coin refresh + backtest + live comparison table update after trade is placed
            hourly_bar_refresh()
            run_compare_accounts()
        else:
            five_minute_verification(is_hourly=False)
