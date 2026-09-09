#!/bin/bash
cd "C:\Users\Praneeth\Desktop\Agent\pipecat" || exit 1
PY="./venv/Scripts/python.exe"
MAX_RETRIES=6
LOGDIR="bot_processors/pricing/backfill_logs"

echo "===== QUEUE WATCHER START ($(date)) - waiting for 08-10 to finish ====="
while ! grep -q "BACKFILL DONE: 2026-08-10" "$LOGDIR/backfill_2026-08-10.log" 2>/dev/null; do
    sleep 60
done
echo "===== 08-10 finished ($(date)) - proceeding in order: 07, 06, 05, 03, 02, 01 ====="

for d in 2026-08-07 2026-08-06 2026-08-05 2026-08-03 2026-08-02 2026-08-01; do
    LOGFILE="$LOGDIR/backfill_${d}.log"
    {
        echo "===== BACKFILL START: $d ($(date)) ====="
        attempt=1
        while [ $attempt -le $MAX_RETRIES ]; do
            echo "--- $d attempt $attempt/$MAX_RETRIES ---"
            if [ $attempt -eq 1 ]; then
                "$PY" -m bot_processors.pricing.agmarknet_scraper --date="$d"
            else
                "$PY" -m bot_processors.pricing.agmarknet_scraper --date="$d" --resume
            fi
            status=$?
            if [ $status -eq 0 ]; then
                echo "--- $d succeeded on attempt $attempt ---"
                break
            fi
            echo "--- $d attempt $attempt failed (exit $status), retrying ---"
            attempt=$((attempt + 1))
        done
        echo "===== BACKFILL DONE: $d ====="
    } > "$LOGFILE" 2>&1
done
echo "===== QUEUE COMPLETE (07,06,05,03,02,01) ($(date)) ====="
