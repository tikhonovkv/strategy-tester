"""Docker entrypoint: run live_bot.py at each Bybit funding settlement (+5 min).

Funding settles at 00:00 / 08:00 / 16:00 UTC. Mode from env:
  LIVE_MODE=1  -> live orders
  else         -> dry-run (logs only)
"""
import os, subprocess, sys, time
from datetime import datetime, timedelta, timezone

MODE = "--live" if os.environ.get("LIVE_MODE") == "1" else "--dry-run"
FUNDING_MINUTES = [5, 8*60+5, 16*60+5]  # 00:05, 08:05, 16:05 UTC

def next_target():
    now = datetime.now(timezone.utc)
    for m in FUNDING_MINUTES:
        t = now.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        if t > now:
            return t
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)

def run_once():
    print(f"[{datetime.now(timezone.utc).isoformat()}] run live_bot.py {MODE}", flush=True)
    subprocess.run([sys.executable, "live_bot.py", MODE], check=False)

if __name__ == "__main__":
    if os.environ.get("BOOTSTRAP") == "1":
        print("bootstrapping data...", flush=True)
        subprocess.run([sys.executable, "bootstrap.py"], check=False)
    while True:
        target = next_target()
        sleep_s = (target - datetime.now(timezone.utc)).total_seconds()
        print(f"sleep {sleep_s/3600:.2f}h until {target.isoformat()}", flush=True)
        time.sleep(max(sleep_s, 1.0))
        run_once()
