"""
AlphaBuddy V8 — reliable intraday runner.

Keeps stock_recommender_v7.py as the selection engine, but fixes the
automation layer:
- one GitHub run starts around 09:30 IST
- actual scans at 09:35, 10:05, 10:35, 11:05 and 11:30
- if GitHub starts late, the next missed slot is scanned immediately
- one qualifying trade alert per day
- Telegram heartbeat + failure messages
- no weekend scans
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import stock_recommender_v7 as v7

IST = ZoneInfo("Asia/Kolkata")
SCAN_TIMES = [(9, 35), (10, 5), (10, 35), (11, 5), (11, 30)]


def telegram(texts):
    if isinstance(texts, str):
        texts = [texts]
    v7.send_telegram(texts)


def run_silent_v7():
    captured = []
    original = v7.send_telegram
    try:
        v7.send_telegram = lambda messages: captured.extend(
            messages if isinstance(messages, list) else [messages]
        )
        v7.run()
    finally:
        v7.send_telegram = original
    return captured


def has_trade(messages):
    return any("INTRADAY #" in m for m in messages)


def trade_report(messages):
    return [
        m for m in messages
        if "Daily Stock Recommendations" in m
        or "INTRADAY #" in m
        or "DISCLAIMER" in m
    ]


def wait_until(target):
    while True:
        now = datetime.now(IST)
        if now >= target:
            return
        time.sleep(max(1, min(30, int((target - now).total_seconds()))))


def main():
    # V7 reads this at import time, so set the module value explicitly too.
    os.environ["STOCK_RECOMMENDER_MODE"] = "INTRADAY"
    v7.EXECUTION_MODE = "INTRADAY"
    v7.INTRADAY_START_TIME = "09:35"
    v7.INTRADAY_CUTOFF_TIME = "11:30"
    v7.INTRADAY_MAX_RISK_PCT = 1.00
    v7.INTRADAY_MIN_TARGET_PCT = 0.75

    now = datetime.now(IST)

    if now.weekday() >= 5:
        print(f"Weekend — no intraday scan: {now:%A}")
        return

    cutoff = now.replace(hour=11, minute=30, second=0, microsecond=0)

    if now > cutoff:
        telegram(
            "🔴 INTRADAY SCANNER MISSED TODAY\n"
            f"Workflow started at {now:%I:%M %p} IST, after the 09:35–11:30 IST window.\n"
            "No intraday scan was performed."
        )
        return

    telegram(
        "🟢 INTRADAY SCANNER STARTED\n"
        f"Time: {now:%I:%M %p} IST\n"
        "Scan window: 09:35–11:30 IST\n"
        "The scanner will retry through the morning if GitHub starts late."
    )

    sent_trade = False
    scanned_slots = set()

    while datetime.now(IST) <= cutoff:
        current = datetime.now(IST)

        # Find the next scheduled slot that has not been handled.
        pending = []
        for h, m in SCAN_TIMES:
            slot = current.replace(hour=h, minute=m, second=0, microsecond=0)
            if (h, m) not in scanned_slots and slot <= cutoff:
                pending.append(((h, m), slot))

        if not pending:
            break

        (h, m), target = pending[0]

        if current < target:
            wait_until(target)
            current = datetime.now(IST)

        # If GitHub started late, scan immediately rather than skipping the slot.
        if current > cutoff:
            break

        scanned_slots.add((h, m))
        print(f"--- Intraday scan at {current:%I:%M %p} IST ---")

        try:
            messages = run_silent_v7()

            if has_trade(messages):
                telegram(
                    "🟢 INTRADAY SETUP FOUND\n"
                    f"Scan time: {current:%I:%M %p} IST\n"
                    "A qualifying setup was found. Further trade alerts are locked for today."
                )
                telegram(trade_report(messages))
                sent_trade = True
                break

            # Heartbeat only; do not send the large V7 'no setup' report.
            telegram(
                "🔎 INTRADAY SCAN COMPLETED\n"
                f"Time: {current:%I:%M %p} IST\n"
                "No qualifying setup yet. Next scheduled scan will check again."
            )

        except Exception as exc:
            print(f"Scan failed: {exc}")
            telegram(
                "🔴 INTRADAY SCAN FAILED\n"
                f"Time: {current:%I:%M %p} IST\n"
                f"Error: {type(exc).__name__}: {exc}\n"
                "The next scheduled scan will try again."
            )

    if not sent_trade:
        telegram(
            "🔵 INTRADAY SCANNER FINISHED\n"
            f"Date: {now:%d %b %Y}\n"
            "No high-probability intraday setup met the current filters today.\n"
            "No forced trade."
        )


if __name__ == "__main__":
    main()
