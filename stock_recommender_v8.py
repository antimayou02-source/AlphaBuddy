"""
AlphaBuddy V8 intraday runner.

Keeps the proven V7 long-term logic untouched, but patches the intraday
scoring/window so delayed GitHub Actions runs have a better chance of finding
valid setups without weakening the core VWAP/EMA/R:R requirements.
"""

import pandas as pd
import stock_recommender_v7 as v7


# Intraday timing:
# GitHub Actions can start late, so allow the scanner to keep looking until
# noon IST. The actual trade still exits before 3:15 PM.
v7.INTRADAY_START_TIME = "09:35"
v7.INTRADAY_CUTOFF_TIME = "12:00"

# Keep R:R strict, but allow slightly wider normal intraday risk.
v7.INTRADAY_MAX_RISK_PCT = 1.00
v7.INTRADAY_MIN_TARGET_PCT = 0.75


def score_intraday_v8(df, ctx):
    """Less brittle V7 intraday score.

    Hard requirements:
      - real 5-minute data
      - price above VWAP
      - EMA9 > EMA20 and price above EMA9
      - adequate participation (softened to a score)
      - minimum 1:2 R:R is still enforced later by V7

    Volume, RSI, MACD, opening breakout, ATR and sector are scored rather
    than immediately killing a candidate.
    """
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

    # Core trend requirements remain hard.
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

    # Market regime: penalty, not a hard block.
    nifty = ctx.get("nifty_trend")
    if nifty == "bullish":
        score += 10
        confirmations += 1
        reasons.append("Nifty bullish — market supports long trades")
    elif nifty == "neutral":
        score += 3
        reasons.append("Nifty neutral — stock-level confirmation required")
    elif nifty == "bearish":
        score -= 6
        reasons.append("Nifty bearish — extra caution on long trades")

    # Volume: soft scoring.
    if pd.notna(relvol):
        rv = float(relvol)
        if rv >= 1.5:
            score += 18
            confirmations += 1
            reasons.append(f"Relative 5-min volume {rv:.1f}x")
        elif rv >= 1.1:
            score += 10
            confirmations += 1
            reasons.append(f"Relative 5-min volume {rv:.1f}x — acceptable participation")
        elif rv >= 0.85:
            score += 3
            reasons.append(f"Relative 5-min volume {rv:.1f}x — modest participation")
        else:
            score -= 5
            reasons.append(f"Relative 5-min volume {rv:.1f}x — weak participation")

    # RSI: avoid extremes but don't reject a healthy setup.
    if pd.notna(rsi):
        r = float(rsi)
        if 52 <= r <= 68:
            score += 12
            confirmations += 1
            reasons.append(f"5-min RSI {r:.0f} — bullish momentum zone")
        elif 48 <= r < 52:
            score += 5
            confirmations += 1
            reasons.append(f"5-min RSI {r:.0f} — early momentum")
        elif 68 < r <= 72:
            score += 5
            reasons.append(f"5-min RSI {r:.0f} — strong but extended")
        elif r > 72:
            score -= 8
            reasons.append(f"5-min RSI {r:.0f} — overextended")
        else:
            score -= 3

    # Opening-range breakout: bonus.
    if pd.notna(opening_high) and price > float(opening_high):
        score += 12
        confirmations += 1
        reasons.append("Breakout above first 15-minute high")

    # MACD: bonus.
    if pd.notna(macd) and pd.notna(macd_signal):
        if float(macd) > float(macd_signal):
            score += 8
            confirmations += 1
            reasons.append("5-min MACD bullish")
        else:
            score -= 2

    # ATR: usable volatility is positive; very high volatility is a penalty,
    # not an automatic rejection. V8 widens the final risk cap to 1%.
    if pd.notna(atr):
        atr_pct = float(atr) / price * 100
        if 0.15 <= atr_pct <= 0.75:
            score += 8
            confirmations += 1
            reasons.append(f"5-min ATR {atr_pct:.2f}% — tradable range")
        elif 0.75 < atr_pct <= 1.00:
            score += 3
            confirmations += 1
            reasons.append(f"5-min ATR {atr_pct:.2f}% — higher volatility")
        elif atr_pct > 1.00:
            score -= 6
            reasons.append(f"5-min ATR {atr_pct:.2f}% — volatile")
        else:
            score -= 2
            reasons.append(f"5-min ATR {atr_pct:.2f}% — low movement")

    # Sector context.
    sector = v7.TICKER_SECTOR.get(df.attrs.get("ticker", ""))
    if sector:
        health = ctx.get("sector_health", {}).get(sector, "unknown")
        if health == "bullish":
            score += 5
            confirmations += 1
            reasons.append(f"{sector} sector bullish")
        elif health == "bearish":
            score -= 4
            reasons.append(f"{sector} sector bearish — caution")

    # Bearish Nifty requires one extra confirmation, same principle as V7.
    min_conf = v7.MIN_INTRA_CONFIRMATIONS + (1 if nifty == "bearish" else 0)

    if confirmations < min_conf:
        return score, confirmations, reasons + [
            f"Need {min_conf} confirmations; got {confirmations}"
        ]

    return score, confirmations, reasons


# Patch only the intraday scorer; all V7 market context, ticker universe,
# long-term scoring, position sizing, Telegram formatting and R:R logic remain.
v7.score_intraday_5m = score_intraday_v8


if __name__ == "__main__":
    v7.run()
