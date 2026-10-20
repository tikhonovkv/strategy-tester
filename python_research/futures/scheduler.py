"""Docker entrypoint: run the full pipeline at each Bybit funding settlement (+5 min).

Funding settles at 00:00 / 08:00 / 16:00 UTC. Each cycle:
  bootstrap -> dataset -> backtest 30d/7d -> live_bot (signals+orders) -> compare.
Mode from env: LIVE_MODE=1 -> live orders, else dry-run.
"""
import os, subprocess, sys, time
from datetime import datetime, timedelta, timezone

MODE = "--live" if os.environ.get("LIVE_MODE") == "1" else "--dry-run"
FUNDING_MINUTES = [5, 8*60+5, 16*60+5]

def run(args):
    print(f"[{datetime.now(timezone.utc).isoformat()}] RUN {' '.join(args)}", flush=True)
    r = subprocess.run([sys.executable, *args], check=False, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[ERROR] {' '.join(args)} exit={r.returncode}", flush=True)
        print((r.stderr or "")[-2500:], flush=True)

def full_cycle():
    run(["bootstrap.py"])                                        # 200d history -> data/
    run(["futures_dataset.py", "--no-oi"])                       # -> futures_dataset.json
    run(["futures_9to4.py", "--recent-days=30", "--leverage=3", "--tag=recent30"])
    run(["futures_9to4.py", "--recent-days=90", "--leverage=3", "--retrain-step=3", "--tag=recent90"])
    run(["futures_9to4.py", "--recent-days=365", "--leverage=3", "--retrain-step=3", "--tag=recent365"])
    run(["live_bot.py", MODE])                                   # signals + orders + futures_signals.json
    run(["compare_live.py"])                                     # -> futures_compare.json

def next_target():
    now = datetime.now(timezone.utc)
    for m in FUNDING_MINUTES:
        t = now.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        if t > now:
            return t
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)

if __name__ == "__main__":
    full_cycle()
    while True:
        target = next_target()
        sleep_s = (target - datetime.now(timezone.utc)).total_seconds()
        print(f"sleep {sleep_s/3600:.2f}h until {target.isoformat()}", flush=True)
        time.sleep(max(sleep_s, 1.0))
        full_cycle()
