"""AlphaBuddy final stock runner.

Uses the existing V7 engine and keeps its long-term selection logic intact.
Adds a less-brittle intraday scorer and controlled Telegram status routing.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import stock_recommender_v7 as v7

IST = ZoneInfo("Asia/Kolkata")
MODE = os.environ.get("STOCK_RECOMMENDER_MODE", "ALL").upper()
SLOT = os.environ.get("INTRADAY_SLOT", "")

# Final intraday risk/timing settings.
v7.INTRADAY_START_TIME = "09:35"
v7.INTRADAY_CUTOFF_TIME = "11:30"
v7.INTRADAY_MAX_RISK_PCT = 1.00
v7.INTRADAY_MIN_TARGET_PCT = 0.75


def score_intraday_final(df, ctx):
    if df is None or df.empty:
        return 0, 0, ["No intraday data"]
    if ctx.get("skip_intraday"):
        return 0, 0, [ctx.get("skip_reason") or "Intraday skipped"]

    row = df.iloc[-1]
    price = float(row["Close"])
    vwap = row.get("VWAP")
    relvol = row.get("RelVolume")
    rsi = row.get("RSI")
    atr = row.get("ATR")
    ema9 = row.get("EMA9")
    ema20 = row.get("EMA20")
    macd = row.get("MACD")
    macd_signal = row.get("MACD_Signal")
    opening_high = row.get("OpeningHigh")

    if pd.isna(price) or price <= 0:
        return 0, 0, ["Invalid price"]
    if pd.isna(vwap) or price <= float(vwap):
        return 0, 0, ["Price not above intraday VWAP"]
    if pd.isna(ema9) or pd.isna(ema20) or not (ema9 > ema20 and price > ema9):
        return 0, 0, ["5-min trend not bullish"]

    score = 30
    confirmations = 2
    reasons = [
        f"Price above intraday VWAP ({float(vwap):.2f})",
        "5-min EMA9 > EMA20 and price above EMA9",
    ]

    nifty = ctx.get("nifty_trend")
    if nifty == "bullish":
        score += 10; confirmations += 1
        reasons.append("Nifty bullish — market supports long trades")
    elif nifty == "neutral":
        score += 3
        reasons.append("Nifty neutral — stock-level confirmation required")
    elif nifty == "bearish":
        score -= 6
        reasons.append("Nifty bearish — extra caution on long trades")

    if pd.notna(relvol):
        rv = float(relvol)
        if rv >= 1.5:
            score += 18; confirmations += 1
            reasons.append(f"Relative 5-min volume {rv:.1f}x")
        elif rv >= 1.1:
            score += 10; confirmations += 1
            reasons.append(f"Relative 5-min volume {rv:.1f}x — acceptable participation")
        elif rv >= 0.85:
            score += 3
            reasons.append(f"Relative 5-min volume {rv:.1f}x — modest participation")
        else:
            score -= 5
            reasons.append(f"Relative 5-min volume {rv:.1f}x — weak participation")

    if pd.notna(rsi):
        r = float(rsi)
        if 52 <= r <= 68:
            score += 12; confirmations += 1
            reasons.append(f"5-min RSI {r:.0f} — bullish momentum zone")
        elif 48 <= r < 52:
            score += 5; confirmations += 1
            reasons.append(f"5-min RSI {r:.0f} — early momentum")
        elif 68 < r <= 72:
            score += 5
            reasons.append(f"5-min RSI {r:.0f} — strong but extended")
        elif r > 72:
            score -= 8
            reasons.append(f"5-min RSI {r:.0f} — overextended")
        else:
            score -= 3

    if pd.notna(opening_high) and price > float(opening_high):
        score += 12; confirmations += 1
        reasons.append("Breakout above first 15-minute high")

    if pd.notna(macd) and pd.notna(macd_signal):
        if float(macd) > float(macd_signal):
            score += 8; confirmations += 1
            reasons.append("5-min MACD bullish")
        else:
            score -= 2

    if pd.notna(atr):
        atr_pct = float(atr) / price * 100
        if 0.15 <= atr_pct <= 0.75:
            score += 8; confirmations += 1
            reasons.append(f"5-min ATR {atr_pct:.2f}% — tradable range")
        elif 0.75 < atr_pct <= 1.00:
            score += 3; confirmations += 1
            reasons.append(f"5-min ATR {atr_pct:.2f}% — higher volatility")
        elif atr_pct > 1.00:
            score -= 6
            reasons.append(f"5-min ATR {atr_pct:.2f}% — volatile")
        else:
            score -= 2
            reasons.append(f"5-min ATR {atr_pct:.2f}% — low movement")

    sector = v7.TICKER_SECTOR.get(df.attrs.get("ticker", ""))
    if sector:
        health = ctx.get("sector_health", {}).get(sector, "unknown")
        if health == "bullish":
            score += 5; confirmations += 1
            reasons.append(f"{sector} sector bullish")
        elif health == "bearish":
            score -= 4
            reasons.append(f"{sector} sector bearish — caution")

    min_conf = v7.MIN_INTRA_CONFIRMATIONS + (1 if nifty == "bearish" else 0)
    if confirmations < min_conf:
        reasons.append(f"Need {min_conf} confirmations; got {confirmations}")

    return score, confirmations, reasons


v7.score_intraday_5m = score_intraday_final
ORIGINAL_SEND = v7.send_telegram
CAPTURE = []


def capture_messages(messages):
    if isinstance(messages, str):
        CAPTURE.append(messages)
    else:
        CAPTURE.extend(messages)


def send_status(now, final=False):
    if final:
        ORIGINAL_SEND([
            "🔵 INTRADAY SCANNER FINISHED",
            f"Time: {now:%I:%M %p} IST",
            "No valid high-probability intraday setup was found today.",
            "No trade is better than a forced trade.",
        ])
    else:
        ORIGINAL_SEND([
            "🟢 INTRADAY SCAN COMPLETED",
            f"Time: {now:%I:%M %p} IST",
            "No valid setup yet.",
            "Scanner will check again in the next scheduled run.",
        ])


def run_intraday():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return
    if not (9 * 60 + 35 <= now.hour * 60 + now.minute <= 11 * 60 + 30):
        # Manual runs outside market window should not produce a misleading trade report.
        print(f"Intraday skipped: outside 09:35–11:30 IST ({now:%I:%M %p}).")
        return

    global CAPTURE
    CAPTURE = []
    v7.send_telegram = capture_messages
    try:
        v7.run()
    except Exception:
        # Failure notification is best-effort; preserve the original exception for Actions.
        try:
            ORIGINAL_SEND([
                "🔴 INTRADAY SCANNER FAILED",
                f"Time: {now:%I:%M %p} IST",
                "The scanner encountered an error. Check GitHub Actions for details.",
            ])
        finally:
            raise
    finally:
        v7.send_telegram = ORIGINAL_SEND

    text = "\n".join(str(x) for x in CAPTURE)
    has_trade = "INTRADAY #" in text

    if has_trade:
        ORIGINAL_SEND(CAPTURE)
        return

    # Status checkpoints: first, middle and final. This gives visibility without
    # sending five repetitive messages every day.
    checkpoint = False
    if SLOT in {"09:35", "10:35", "11:30"}:
        checkpoint = True
    elif not SLOT and now.minute in {30, 31, 35, 36}:
        checkpoint = True

    if checkpoint:
        send_status(now, final=(SLOT == "11:30" or (not SLOT and now.hour == 11)))


def main():
    if MODE == "INTRADAY":
        run_intraday()
    else:
        # PREMARKET/long-term continues to use the proven V7 engine unchanged.
        v7.run()


if __name__ == "__main__":
    main()
