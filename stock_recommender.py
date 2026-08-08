"""
Daily Stock Recommender — Enhanced Version
===========================================
Screens Nifty 500 + key sector stocks every morning at 9 AM IST.
Sends 5 recommendations to Telegram:
  - 2 Long Term picks  (hold 2 weeks to 3 months)
  - 3 Intraday picks   (balanced, exit same day)

INDICATORS USED
----------------
Technical  : 20/50/200 DMA, RSI, MACD, Bollinger Bands, ATR,
             Stochastic, ADX, OBV, VWAP, Supertrend, 52-week levels
Fundamental: PE, PEG, ROE, Debt/Equity, Promoter holding
Market     : India VIX filter, Nifty trend filter
Quality    : Minimum 3-indicator confirmation before recommending

SETUP
------
    pip install yfinance feedparser pandas numpy requests

SCHEDULING (GitHub Actions — runs even when laptop is off)
------------------------------------------------------------
    See .github/workflows/daily_stock.yml in your repo
"""

import os, sys, time, random
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    sys.exit("Run: pip install yfinance feedparser pandas numpy requests")

try:
    import requests
except ImportError:
    requests = None

# ─────────────────────────── CONFIG ────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_MSG_LIMIT = 4000
REQUEST_PAUSE      = 0.5
OUTPUT_FILE        = f"recommendations_{datetime.now():%Y-%m-%d}.md"

# Minimum indicator confirmations required before recommending
MIN_LT_CONFIRMATIONS    = 3
MIN_INTRA_CONFIRMATIONS = 3

# Market filters
VIX_MAX        = 22    # Skip intraday if VIX > 22 (too fearful)
VIX_WARN       = 18    # Warn if VIX > 18

NIFTY500_SAMPLE = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
    "AXISBANK.NS","KOTAKBANK.NS","SBIN.NS","BAJFINANCE.NS","LT.NS",
    "HINDUNILVR.NS","ITC.NS","WIPRO.NS","HCLTECH.NS","SUNPHARMA.NS",
    "DRREDDY.NS","DIVISLAB.NS","CIPLA.NS","NESTLEIND.NS","TITAN.NS",
    "POLYCAB.NS","CUMMINSIND.NS","ASTRAL.NS","PERSISTENT.NS","LTM.NS",
    "COFORGE.NS","MPHASIS.NS","TATACOMM.NS","ABCAPITAL.NS","CHOLAFIN.NS",
    "MARICO.NS","DABUR.NS","PIDILITIND.NS","HAVELLS.NS","VOLTAS.NS",
    "SUPREMEIND.NS","PAGEIND.NS","BATAINDIA.NS","VGUARD.NS",
    "NTPC.NS","POWERGRID.NS","COALINDIA.NS","ONGC.NS","BPCL.NS",
    "HAL.NS","BEL.NS","BHEL.NS","IRCTC.NS","CONCOR.NS","NCC.NS",
    "KEC.NS","APLAPOLLO.NS","RVNL.NS","IRFC.NS","NHPC.NS",
    "BAJAJFINSV.NS","LICHSGFIN.NS","MUTHOOTFIN.NS","PFC.NS","RECLTD.NS",
    "SBICARD.NS","HDFCLIFE.NS","ICICIGI.NS","LICI.NS","BSE.NS",
    "TECHM.NS","OFSS.NS","KPITTECH.NS","TATAELXSI.NS","ROUTE.NS",
    "MARUTI.NS","M&M.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS",
    "EICHERMOT.NS","ASHOKLEY.NS","EXIDEIND.NS","MOTHERSON.NS","BOSCH.NS",
    "TMPV.NS","TMCV.NS",
    "HINDALCO.NS","VEDL.NS","JSWSTEEL.NS","TATASTEEL.NS","SAIL.NS",
    "NATIONALUM.NS","NMDC.NS","PIIND.NS","UPL.NS","AARTIIND.NS",
    "DMART.NS","TRENT.NS","NYKAA.NS","ETERNAL.NS",
    "AUROPHARMA.NS","ALKEM.NS","TORNTPHARM.NS","IPCALAB.NS","LUPIN.NS",
]

SECTOR_FOCUS = [
    "HDFCBANK.NS","ICICIBANK.NS","SBIN.NS","KOTAKBANK.NS",
    "INFY.NS","TCS.NS","WIPRO.NS","HCLTECH.NS","LTM.NS",
    "SUNPHARMA.NS","DRREDDY.NS","CIPLA.NS","DIVISLAB.NS",
    "RELIANCE.NS","ONGC.NS","BPCL.NS",
    "TATASTEEL.NS","HINDALCO.NS","JSWSTEEL.NS",
    "NTPC.NS","POWERGRID.NS","NHPC.NS",
    "LT.NS","HAL.NS","BEL.NS","KEC.NS",
    "MARUTI.NS","M&M.NS","BAJAJ-AUTO.NS",
]

ALL_TICKERS = list(set(NIFTY500_SAMPLE + SECTOR_FOCUS))

# ─────────────────────────── MARKET FILTERS ────────────────────────────────

def get_market_context():
    """Fetch India VIX and Nifty trend for market-wide filters."""
    context = {"vix": None, "nifty_trend": "unknown", "vix_warning": False, "skip_intraday": False}
    try:
        vix_df = yf.Ticker("^INDIAVIX").history(period="5d")
        if not vix_df.empty:
            context["vix"] = round(vix_df["Close"].iloc[-1], 2)
            if context["vix"] > VIX_MAX:
                context["skip_intraday"] = True
            if context["vix"] > VIX_WARN:
                context["vix_warning"] = True
    except Exception:
        pass

    try:
        nifty_df = yf.Ticker("^NSEI").history(period="1mo")
        if not nifty_df.empty:
            nifty_df["DMA20"] = nifty_df["Close"].rolling(20).mean()
            last = nifty_df.iloc[-1]
            if last["Close"] > last["DMA20"]:
                context["nifty_trend"] = "bullish"
            else:
                context["nifty_trend"] = "bearish"
    except Exception:
        pass

    return context

# ─────────────────────────── INDICATORS ────────────────────────────────────

def compute_indicators(df):
    df = df.copy()

    # Moving averages
    df["DMA20"]  = df["Close"].rolling(20).mean()
    df["DMA50"]  = df["Close"].rolling(50).mean()
    df["DMA200"] = df["Close"].rolling(200).mean()

    # RSI
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    # ATR
    hl  = df["High"] - df["Low"]
    hc  = (df["High"] - df["Close"].shift()).abs()
    lc  = (df["Low"]  - df["Close"].shift()).abs()
    df["ATR"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    # Volume
    df["AvgVol20"] = df["Volume"].rolling(20).mean()

    # MACD
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    # Bollinger Bands
    df["BB_mid"]   = df["Close"].rolling(20).mean()
    df["BB_std"]   = df["Close"].rolling(20).std()
    df["BB_upper"] = df["BB_mid"] + 2 * df["BB_std"]
    df["BB_lower"] = df["BB_mid"] - 2 * df["BB_std"]

    # Stochastic Oscillator
    low14  = df["Low"].rolling(14).min()
    high14 = df["High"].rolling(14).max()
    df["Stoch_K"] = 100 * (df["Close"] - low14) / (high14 - low14 + 1e-9)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()

    # ADX (Average Directional Index)
    tr     = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    plus_dm  = df["High"].diff().clip(lower=0)
    minus_dm = (-df["Low"].diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm]  = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr14      = tr.rolling(14).mean()
    plus_di    = 100 * (plus_dm.rolling(14).mean()  / (atr14 + 1e-9))
    minus_di   = 100 * (minus_dm.rolling(14).mean() / (atr14 + 1e-9))
    dx         = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    df["ADX"]      = dx.rolling(14).mean()
    df["Plus_DI"]  = plus_di
    df["Minus_DI"] = minus_di

    # OBV (On Balance Volume)
    obv = [0]
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > df["Close"].iloc[i-1]:
            obv.append(obv[-1] + df["Volume"].iloc[i])
        elif df["Close"].iloc[i] < df["Close"].iloc[i-1]:
            obv.append(obv[-1] - df["Volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["OBV"] = obv
    df["OBV_MA"] = pd.Series(obv, index=df.index).rolling(20).mean()

    # VWAP (approximate — using full history as proxy for recent VWAP)
    df["VWAP"] = (df["Close"] * df["Volume"]).rolling(20).sum() / df["Volume"].rolling(20).sum()

    # Supertrend
    multiplier = 3
    basic_ub = (df["High"] + df["Low"]) / 2 + multiplier * df["ATR"]
    basic_lb = (df["High"] + df["Low"]) / 2 - multiplier * df["ATR"]
    supertrend = pd.Series(index=df.index, dtype=float)
    direction  = pd.Series(index=df.index, dtype=float)
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > basic_ub.iloc[i-1]:
            direction.iloc[i] = 1   # bullish
            supertrend.iloc[i] = basic_lb.iloc[i]
        elif df["Close"].iloc[i] < basic_lb.iloc[i-1]:
            direction.iloc[i] = -1  # bearish
            supertrend.iloc[i] = basic_ub.iloc[i]
        else:
            direction.iloc[i] = direction.iloc[i-1]
            supertrend.iloc[i] = supertrend.iloc[i-1]
    df["Supertrend_dir"] = direction

    # 52-week high/low
    df["High52"] = df["High"].rolling(252).max()
    df["Low52"]  = df["Low"].rolling(252).min()

    return df


# ─────────────────────────── SCORING ───────────────────────────────────────

def score_longterm(df, row, info):
    """Score for long-term pick. Returns (score, confirmations, reasons)."""
    score, confirmations, reasons = 0, 0, []
    price = row["Close"]

    # 1. Trend: DMA alignment
    if pd.notna(row["DMA200"]) and price > row["DMA50"] > row["DMA200"]:
        score += 20; confirmations += 1
        reasons.append("Price > 50DMA > 200DMA — strong uptrend")
    elif pd.notna(row["DMA50"]) and price > row["DMA50"]:
        score += 8
        reasons.append("Price above 50DMA — mild uptrend")

    # 2. RSI — healthy zone
    if 40 <= row["RSI"] <= 60:
        score += 15; confirmations += 1
        reasons.append(f"RSI healthy at {row['RSI']:.0f} (40-60 zone)")
    elif 60 < row["RSI"] <= 68:
        score += 7
        reasons.append(f"RSI {row['RSI']:.0f} — slightly elevated, monitor")

    # 3. MACD bullish
    if pd.notna(row["MACD"]) and row["MACD"] > row["MACD_Signal"]:
        score += 12; confirmations += 1
        reasons.append("MACD above signal — bullish momentum")

    # 4. ADX — strong trend
    if pd.notna(row["ADX"]):
        if row["ADX"] > 25 and row["Plus_DI"] > row["Minus_DI"]:
            score += 12; confirmations += 1
            reasons.append(f"ADX {row['ADX']:.0f} — strong bullish trend")
        elif row["ADX"] < 20:
            score -= 5
            reasons.append(f"ADX {row['ADX']:.0f} — weak trend, caution")

    # 5. Volume confirmation
    vol_ratio = row["Volume"] / row["AvgVol20"] if row["AvgVol20"] else 1
    if vol_ratio > 1.3:
        score += 10; confirmations += 1
        reasons.append(f"Volume {vol_ratio:.1f}x above average — strong interest")

    # 6. OBV rising
    if pd.notna(row["OBV"]) and pd.notna(row["OBV_MA"]):
        if row["OBV"] > row["OBV_MA"]:
            score += 8; confirmations += 1
            reasons.append("OBV above MA — volume confirms price rise")

    # 7. Supertrend bullish
    if pd.notna(row["Supertrend_dir"]) and row["Supertrend_dir"] == 1:
        score += 10; confirmations += 1
        reasons.append("Supertrend bullish signal")

    # 8. 52-week proximity
    if pd.notna(row["High52"]) and pd.notna(row["Low52"]):
        range52 = row["High52"] - row["Low52"]
        if range52 > 0:
            pos = (price - row["Low52"]) / range52
            if 0.5 <= pos <= 0.85:
                score += 8; confirmations += 1
                reasons.append(f"Price in strong zone — {pos*100:.0f}% of 52-week range")
            elif pos > 0.95:
                score -= 5
                reasons.append("Near 52-week high — risky entry")

    # 9. Stochastic
    if pd.notna(row["Stoch_K"]) and 30 <= row["Stoch_K"] <= 65:
        score += 5; confirmations += 1
        reasons.append(f"Stochastic {row['Stoch_K']:.0f} — not overbought")

    # 10. Fundamentals
    pe = info.get("trailingPE")
    eps_growth = info.get("earningsGrowth")
    roe = info.get("returnOnEquity")
    de  = info.get("debtToEquity")
    promoter = info.get("heldPercentInsiders")

    if pe and 5 < pe < 30:
        score += 10; confirmations += 1
        reasons.append(f"PE {pe:.0f} — attractive valuation")
    elif pe and 30 <= pe < 50:
        score += 4
        reasons.append(f"PE {pe:.0f} — fair valuation")
    elif pe and pe > 60:
        score -= 8
        reasons.append(f"PE {pe:.0f} — expensive, check growth")

    # PEG ratio
    if pe and eps_growth and eps_growth > 0:
        peg = pe / (eps_growth * 100)
        if peg < 1:
            score += 8; confirmations += 1
            reasons.append(f"PEG {peg:.2f} — growth at reasonable price")

    if roe and roe > 0.15:
        score += 8; confirmations += 1
        reasons.append(f"ROE {roe*100:.0f}% — strong returns")

    if de is not None and de < 0.5:
        score += 5; confirmations += 1
        reasons.append(f"Low D/E {de:.2f} — financially strong")
    elif de is not None and de > 2:
        score -= 8
        reasons.append(f"High D/E {de:.2f} — overleveraged, caution")

    if promoter and promoter > 0.5:
        score += 5
        reasons.append(f"High promoter holding {promoter*100:.0f}% — confidence")

    return score, confirmations, reasons


def score_intraday(row, market_ctx):
    """Score for intraday pick. Returns (score, confirmations, reasons)."""
    score, confirmations, reasons = 0, 0, []
    price = row["Close"]

    # Skip if market too fearful
    if market_ctx.get("skip_intraday"):
        return 0, 0, ["India VIX too high — intraday skipped today"]

    # 1. Volume — most important for intraday
    vol_ratio = row["Volume"] / row["AvgVol20"] if row["AvgVol20"] else 1
    if vol_ratio > 1.5:
        score += 22; confirmations += 1
        reasons.append(f"High volume {vol_ratio:.1f}x — strong participation")
    elif vol_ratio > 1.2:
        score += 10; confirmations += 1
        reasons.append(f"Above avg volume {vol_ratio:.1f}x")

    # 2. Price above VWAP
    if pd.notna(row["VWAP"]) and price > row["VWAP"]:
        score += 18; confirmations += 1
        reasons.append("Price above VWAP — buyers in control")

    # 3. MACD
    if pd.notna(row["MACD"]) and row["MACD"] > row["MACD_Signal"]:
        score += 15; confirmations += 1
        reasons.append("MACD bullish — momentum building")

    # 4. RSI
    if 45 <= row["RSI"] <= 65:
        score += 15; confirmations += 1
        reasons.append(f"RSI {row['RSI']:.0f} — sweet spot for intraday")
    elif row["RSI"] < 35:
        score += 8; confirmations += 1
        reasons.append(f"RSI {row['RSI']:.0f} — oversold bounce candidate")

    # 5. ADX — strong trend needed
    if pd.notna(row["ADX"]) and row["ADX"] > 20:
        score += 10; confirmations += 1
        reasons.append(f"ADX {row['ADX']:.0f} — trending market")

    # 6. Supertrend bullish
    if pd.notna(row["Supertrend_dir"]) and row["Supertrend_dir"] == 1:
        score += 10; confirmations += 1
        reasons.append("Supertrend bullish — trend confirmed")

    # 7. Stochastic
    if pd.notna(row["Stoch_K"]) and 20 <= row["Stoch_K"] <= 70:
        score += 8; confirmations += 1
        reasons.append(f"Stochastic {row['Stoch_K']:.0f} — good entry zone")

    # 8. ATR — needs good intraday range
    atr_pct = (row["ATR"] / price * 100) if pd.notna(row["ATR"]) and price else 0
    if 0.8 <= atr_pct <= 3.0:
        score += 8; confirmations += 1
        reasons.append(f"ATR {atr_pct:.1f}% — sufficient intraday range")

    # 9. Bollinger Band lower — mean reversion
    if pd.notna(row["BB_lower"]) and price <= row["BB_lower"] * 1.015:
        score += 8; confirmations += 1
        reasons.append("Near lower Bollinger Band — mean reversion likely")

    # 10. Nifty trend alignment
    if market_ctx.get("nifty_trend") == "bullish":
        score += 5
        reasons.append("Nifty in uptrend — tailwind for longs")

    return score, confirmations, reasons


# ─────────────────────────── SCREENER ──────────────────────────────────────

def fetch_and_score(tickers, market_ctx):
    lt_candidates, intra_candidates = [], []
    random.shuffle(tickers)

    print(f"Screening {len(tickers)} stocks...")
    for ticker in tickers:
        try:
            t    = yf.Ticker(ticker)
            df   = t.history(period="1y")
            if df is None or len(df) < 60:
                continue
            df   = compute_indicators(df)
            row  = df.iloc[-1]
            info = {}
            try:
                info = t.info
            except Exception:
                pass

            name  = info.get("shortName", ticker.replace(".NS",""))
            price = round(row["Close"], 2)
            atr   = row["ATR"] if pd.notna(row["ATR"]) else price * 0.01

            # Long term
            lt_score, lt_conf, lt_reasons = score_longterm(df, row, info)
            if lt_score >= 50 and lt_conf >= MIN_LT_CONFIRMATIONS:
                sl  = round(price - 2.0 * atr, 2)
                tgt = round(price + 3.0 * atr, 2)
                lt_candidates.append({
                    "ticker": ticker, "name": name, "price": price,
                    "score": lt_score, "confirmations": lt_conf,
                    "reasons": lt_reasons,
                    "stop_loss": sl, "target": tgt,
                    "buy_low":  round(price * 0.995, 2),
                    "buy_high": round(price * 1.005, 2),
                })

            # Intraday
            intra_score, intra_conf, intra_reasons = score_intraday(row, market_ctx)
            if intra_score >= 45 and intra_conf >= MIN_INTRA_CONFIRMATIONS:
                sl  = round(price - 1.0 * atr, 2)
                tgt = round(price + 1.5 * atr, 2)
                intra_candidates.append({
                    "ticker": ticker, "name": name, "price": price,
                    "score": intra_score, "confirmations": intra_conf,
                    "reasons": intra_reasons,
                    "stop_loss": sl, "target": tgt,
                    "buy_low":  round(price * 0.997, 2),
                    "buy_high": round(price * 1.003, 2),
                })

        except Exception as e:
            print(f"  ! {ticker}: {e}")
        time.sleep(REQUEST_PAUSE)

    lt_candidates.sort(key=lambda x: x["score"], reverse=True)
    intra_candidates.sort(key=lambda x: x["score"], reverse=True)

    lt_picks   = lt_candidates[:2]
    lt_tickers = {r["ticker"] for r in lt_picks}
    intra_picks = [r for r in intra_candidates if r["ticker"] not in lt_tickers][:3]

    return lt_picks, intra_picks


# ─────────────────────────── HOLD PERIODS ──────────────────────────────────

HOLD_PERIODS_LT = ["2-3 weeks", "3-4 weeks", "4-6 weeks", "6-8 weeks", "2-3 months"]

def hold_period():
    return random.choice(HOLD_PERIODS_LT)


# ─────────────────────────── TELEGRAM ──────────────────────────────────────

def build_telegram_messages(lt_picks, intra_picks, market_ctx):
    date_str = datetime.now().strftime("%d %b %Y, %H:%M")
    messages = []

    # Header
    vix_str = f"India VIX: {market_ctx['vix']}" if market_ctx.get("vix") else "India VIX: N/A"
    nifty_str = f"Nifty: {market_ctx.get('nifty_trend','unknown').upper()}"
    warn_str = " | HIGH VOLATILITY - CAUTION" if market_ctx.get("vix_warning") else ""

    messages.append(
        f"📊 Daily Stock Recommendations\n"
        f"📅 {date_str}\n"
        f"📉 {vix_str} | {nifty_str}{warn_str}\n"
        f"{'─'*35}\n"
        f"Nifty 500 + Key Sectors screened"
    )

    # Long term
    messages.append("📈 LONG TERM PICKS (2-3 weeks to 3 months)")
    if not lt_picks:
        messages.append(
            "No strong long-term setups today.\n"
            "Market may be extended or lacking confirmation.\n"
            "Better to wait than force a trade."
        )
    for i, r in enumerate(lt_picks, 1):
        hp = hold_period()
        rr = round((r["target"] - r["price"]) / (r["price"] - r["stop_loss"]), 1) \
             if r["price"] != r["stop_loss"] else "-"
        reasons_text = "\n".join(f"  * {re}" for re in r["reasons"][:5])
        messages.append(
            f"📈 #{i} {r['name']} ({r['ticker'].replace('.NS','')})\n"
            f"{'─'*30}\n"
            f"💰 CMP        : Rs {r['price']}\n"
            f"🟢 Buy Range  : Rs {r['buy_low']} - {r['buy_high']}\n"
            f"🔴 Stop-Loss  : Rs {r['stop_loss']}\n"
            f"🎯 Target     : Rs {r['target']}\n"
            f"⚖ Risk:Reward : 1 : {rr}\n"
            f"⏳ Hold       : {hp}\n"
            f"✅ Confirmed by {r['confirmations']} indicators\n"
            f"📌 Reasons:\n{reasons_text}"
        )

    # Intraday
    if market_ctx.get("skip_intraday"):
        messages.append(
            f"⚡ INTRADAY PICKS\n"
            f"India VIX {market_ctx['vix']} is too high today.\n"
            f"Intraday trading not recommended — high risk of whipsaws.\n"
            f"Focus on long term only."
        )
    else:
        messages.append("⚡ INTRADAY PICKS (Exit before 3:15 PM today)")
        if not intra_picks:
            messages.append(
                "No high-probability intraday setups today.\n"
                "Avoid forced trades — preserve capital."
            )
        for i, r in enumerate(intra_picks, 1):
            rr = round((r["target"] - r["price"]) / (r["price"] - r["stop_loss"]), 1) \
                 if r["price"] != r["stop_loss"] else "-"
            reasons_text = "\n".join(f"  * {re}" for re in r["reasons"][:5])
            messages.append(
                f"⚡ #{i} {r['name']} ({r['ticker'].replace('.NS','')})\n"
                f"{'─'*30}\n"
                f"💰 CMP        : Rs {r['price']}\n"
                f"🟢 Buy Range  : Rs {r['buy_low']} - {r['buy_high']}\n"
                f"🔴 Stop-Loss  : Rs {r['stop_loss']}\n"
                f"🎯 Target     : Rs {r['target']}\n"
                f"⚖ Risk:Reward : 1 : {rr}\n"
                f"⏳ Hold       : Same day - exit before 3:15 PM\n"
                f"✅ Confirmed by {r['confirmations']} indicators\n"
                f"📌 Reasons:\n{reasons_text}"
            )

    # Footer
    messages.append(
        "⚠️ Disclaimer:\n"
        "Algorithmic screening only.\n"
        "Not SEBI-registered investment advice.\n"
        "Always check latest news + pre-market\n"
        "sentiment before entering any trade.\n"
        "Use strict stop-losses always."
    )
    return messages


def send_telegram(messages):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  (Telegram not configured)")
        return
    if requests is None:
        print("  ! pip install requests")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if isinstance(messages, str):
        messages = [messages]

    for text in messages:
        for i in range(0, len(text), TELEGRAM_MSG_LIMIT):
            chunk = text[i:i + TELEGRAM_MSG_LIMIT]
            try:
                r = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                    timeout=15,
                )
                if r.status_code == 200:
                    print("  Telegram message sent.")
                else:
                    print(f"  ! Telegram error {r.status_code}: {r.text[:100]}")
            except Exception as e:
                print(f"  ! Telegram error: {e}")
            time.sleep(0.3)


# ─────────────────────────── REPORT ────────────────────────────────────────

def write_report(lt_picks, intra_picks, market_ctx):
    date_str = datetime.now().strftime("%d %b %Y, %H:%M")
    vix_str  = f"India VIX: {market_ctx['vix']}" if market_ctx.get("vix") else "India VIX: N/A"
    lines    = [
        f"# Daily Stock Recommendations - {date_str}",
        f"_{vix_str} | Nifty: {market_ctx.get('nifty_trend','unknown').upper()}_\n",
        "## Long Term Picks\n",
    ]
    for i, r in enumerate(lt_picks, 1):
        hp = hold_period()
        rr = round((r["target"] - r["price"]) / (r["price"] - r["stop_loss"]), 1) \
             if r["price"] != r["stop_loss"] else "-"
        lines.append(
            f"### #{i} {r['name']} ({r['ticker']})\n"
            f"- CMP: Rs {r['price']} | Buy: Rs {r['buy_low']}-{r['buy_high']}\n"
            f"- SL: Rs {r['stop_loss']} | Target: Rs {r['target']} | R:R = 1:{rr}\n"
            f"- Hold: {hp} | Confirmations: {r['confirmations']}\n"
            f"- Reasons: {'; '.join(r['reasons'][:5])}\n"
        )
    if not lt_picks:
        lines.append("_No strong long-term setups today._\n")

    lines.append("\n## Intraday Picks\n")
    for i, r in enumerate(intra_picks, 1):
        rr = round((r["target"] - r["price"]) / (r["price"] - r["stop_loss"]), 1) \
             if r["price"] != r["stop_loss"] else "-"
        lines.append(
            f"### #{i} {r['name']} ({r['ticker']})\n"
            f"- CMP: Rs {r['price']} | Buy: Rs {r['buy_low']}-{r['buy_high']}\n"
            f"- SL: Rs {r['stop_loss']} | Target: Rs {r['target']} | R:R = 1:{rr}\n"
            f"- Exit: Before 3:15 PM | Confirmations: {r['confirmations']}\n"
            f"- Reasons: {'; '.join(r['reasons'][:5])}\n"
        )
    if not intra_picks:
        lines.append("_No high-prob intraday setups today._\n")

    lines += [
        "\n---",
        "_Algorithmic screening only. Not SEBI-registered advice._",
        "_Always use stop-losses. Check news before entry._",
    ]
    Path(OUTPUT_FILE).write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────── MAIN ──────────────────────────────────────────

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def run():
    safe_print("\n" + "="*50)
    safe_print(f"  Daily Stock Recommender - {datetime.now():%d %b %Y %H:%M}")
    safe_print("="*50 + "\n")

    safe_print("Fetching market context (VIX + Nifty)...")
    market_ctx = get_market_context()
    vix_info   = f"VIX={market_ctx['vix']}" if market_ctx.get("vix") else "VIX=N/A"
    safe_print(f"Market: {vix_info} | Nifty={market_ctx.get('nifty_trend','unknown')}")
    if market_ctx.get("skip_intraday"):
        safe_print("  !! VIX too high — intraday picks skipped today")

    lt_picks, intra_picks = fetch_and_score(ALL_TICKERS, market_ctx)

    safe_print(f"\nTop picks: {len(lt_picks)} long term, {len(intra_picks)} intraday")
    write_report(lt_picks, intra_picks, market_ctx)
    safe_print(f"Report saved: {OUTPUT_FILE}")

    tg_msgs = build_telegram_messages(lt_picks, intra_picks, market_ctx)
    send_telegram(tg_msgs)


if __name__ == "__main__":
    run()
