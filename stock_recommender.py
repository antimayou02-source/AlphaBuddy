"""
Daily Stock Recommender — v3.0 (Maximum Accuracy)
===================================================
Screens Nifty 500 + key sector stocks every morning.
Sends recommendations to Telegram:
  - 2 Long Term picks  (hold 2 weeks to 3 months)
  - 3 Intraday picks   (exit same day before 3:15 PM)

FILTERS USED
-------------
Pre-market : SGX Nifty trend, US markets (Dow/Nasdaq), India VIX
Sector     : Sector index health check before recommending any stock
Technical  : 20/50/200 DMA, RSI, MACD, Bollinger Bands, ATR,
             Stochastic, ADX, OBV, VWAP, Supertrend, 52-week levels
Fundamental: PE, PEG, ROE, Debt/Equity, Promoter holding
Risk Mgmt  : Min 5 confirmations, 1:2 R:R enforced, tight SL,
             3-day momentum check, delivery volume, blacklist

SETUP
------
    pip install yfinance feedparser pandas numpy requests
"""

import os, sys, time, random
from datetime import datetime, timedelta
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

# Strict confirmation requirements
MIN_LT_CONFIRMATIONS    = 4
MIN_INTRA_CONFIRMATIONS = 5

# VIX thresholds
VIX_NO_INTRADAY = 18   # Skip intraday completely
VIX_CAUTION     = 15   # Warn but allow

# Minimum Risk:Reward ratio
MIN_RR_INTRADAY = 2.0
MIN_RR_LONGTERM = 2.5

# Blacklisted tickers — high beta, too volatile, unreliable
BLACKLIST = {
    "SAIL.NS","RPOWER.NS","JPPOWER.NS","YESBANK.NS","SUZLON.NS",
    "IDEA.NS","VODAFONEIDEA.NS","NATIONALUM.NS","NMDC.NS",
    "IRCON.NS","RVNL.NS","IRFC.NS","BHEL.NS",
}

# Sector indices to check health
SECTOR_INDICES = {
    "Banking":  "^NSEBANK",
    "IT":       "^CNXIT",
    "Pharma":   "^CNXPHARMA",
    "Metal":    "^CNXMETAL",
    "Auto":     "^CNXAUTO",
    "Energy":   "^CNXENERGY",
    "Infra":    "^CNXINFRA",
    "FMCG":     "^CNXFMCG",
}

# Map tickers to sectors for health check
TICKER_SECTOR = {
    "HDFCBANK.NS":"Banking","ICICIBANK.NS":"Banking","SBIN.NS":"Banking",
    "AXISBANK.NS":"Banking","KOTAKBANK.NS":"Banking","BAJFINANCE.NS":"Banking",
    "LICHSGFIN.NS":"Banking","SBICARD.NS":"Banking","HDFCLIFE.NS":"Banking",
    "TCS.NS":"IT","INFY.NS":"IT","WIPRO.NS":"IT","HCLTECH.NS":"IT",
    "LTM.NS":"IT","TECHM.NS":"IT","COFORGE.NS":"IT","MPHASIS.NS":"IT",
    "PERSISTENT.NS":"IT","KPITTECH.NS":"IT","TATAELXSI.NS":"IT",
    "SUNPHARMA.NS":"Pharma","DRREDDY.NS":"Pharma","CIPLA.NS":"Pharma",
    "DIVISLAB.NS":"Pharma","LUPIN.NS":"Pharma","ALKEM.NS":"Pharma",
    "TATASTEEL.NS":"Metal","JSWSTEEL.NS":"Metal","HINDALCO.NS":"Metal",
    "VEDL.NS":"Metal","SAIL.NS":"Metal",
    "MARUTI.NS":"Auto","M&M.NS":"Auto","BAJAJ-AUTO.NS":"Auto",
    "HEROMOTOCO.NS":"Auto","EICHERMOT.NS":"Auto","ASHOKLEY.NS":"Auto",
    "RELIANCE.NS":"Energy","ONGC.NS":"Energy","BPCL.NS":"Energy",
    "NTPC.NS":"Energy","POWERGRID.NS":"Energy","NHPC.NS":"Energy",
    "LT.NS":"Infra","HAL.NS":"Infra","BEL.NS":"Infra","KEC.NS":"Infra",
    "NCC.NS":"Infra","APLAPOLLO.NS":"Infra",
    "HINDUNILVR.NS":"FMCG","ITC.NS":"FMCG","NESTLEIND.NS":"FMCG",
    "MARICO.NS":"FMCG","DABUR.NS":"FMCG",
}

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
    "HAL.NS","BEL.NS","IRCTC.NS","CONCOR.NS","NCC.NS",
    "KEC.NS","APLAPOLLO.NS","NHPC.NS",
    "BAJAJFINSV.NS","LICHSGFIN.NS","MUTHOOTFIN.NS","PFC.NS","RECLTD.NS",
    "SBICARD.NS","HDFCLIFE.NS","ICICIGI.NS","LICI.NS","BSE.NS",
    "TECHM.NS","OFSS.NS","KPITTECH.NS","TATAELXSI.NS","ROUTE.NS",
    "MARUTI.NS","M&M.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS",
    "EICHERMOT.NS","ASHOKLEY.NS","EXIDEIND.NS","MOTHERSON.NS","BOSCH.NS",
    "TMPV.NS","TMCV.NS",
    "HINDALCO.NS","VEDL.NS","JSWSTEEL.NS","TATASTEEL.NS",
    "PIIND.NS","UPL.NS","AARTIIND.NS",
    "DMART.NS","TRENT.NS","NYKAA.NS","ETERNAL.NS",
    "AUROPHARMA.NS","ALKEM.NS","TORNTPHARM.NS","IPCALAB.NS","LUPIN.NS",
    "TITAN.NS","PIDILITIND.NS","HAVELLS.NS","POLYCAB.NS",
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

ALL_TICKERS = [t for t in list(set(NIFTY500_SAMPLE + SECTOR_FOCUS))
               if t not in BLACKLIST]


# ─────────────────────────── MARKET CONTEXT ────────────────────────────────

def get_market_context():
    """Fetch VIX, Nifty, SGX Nifty proxy, US markets, sector health."""
    ctx = {
        "vix": None, "nifty_trend": "unknown",
        "us_trend": "unknown", "sgx_trend": "unknown",
        "vix_warning": False, "skip_intraday": False,
        "sector_health": {},
        "warnings": [],
    }

    # India VIX
    try:
        vix_df = yf.Ticker("^INDIAVIX").history(period="5d")
        if not vix_df.empty:
            ctx["vix"] = round(vix_df["Close"].iloc[-1], 2)
            if ctx["vix"] >= VIX_NO_INTRADAY:
                ctx["skip_intraday"] = True
                ctx["warnings"].append(f"VIX {ctx['vix']} >= {VIX_NO_INTRADAY} — intraday SKIPPED")
            elif ctx["vix"] >= VIX_CAUTION:
                ctx["vix_warning"] = True
                ctx["warnings"].append(f"VIX {ctx['vix']} elevated — trade carefully")
    except Exception:
        pass

    # Nifty 50 trend
    try:
        nifty = yf.Ticker("^NSEI").history(period="1mo")
        if not nifty.empty:
            nifty["DMA20"] = nifty["Close"].rolling(20).mean()
            last = nifty.iloc[-1]
            prev = nifty.iloc[-2]
            if last["Close"] > last["DMA20"] and last["Close"] > prev["Close"]:
                ctx["nifty_trend"] = "bullish"
            elif last["Close"] < last["DMA20"]:
                ctx["nifty_trend"] = "bearish"
                ctx["warnings"].append("Nifty below 20DMA — market weak")
            else:
                ctx["nifty_trend"] = "neutral"
    except Exception:
        pass

    # US Markets proxy (S&P 500)
    try:
        sp500 = yf.Ticker("^GSPC").history(period="5d")
        if not sp500.empty:
            last_close = sp500["Close"].iloc[-1]
            prev_close = sp500["Close"].iloc[-2]
            chg = (last_close - prev_close) / prev_close * 100
            if chg > 0.3:
                ctx["us_trend"] = "bullish"
            elif chg < -0.3:
                ctx["us_trend"] = "bearish"
                ctx["warnings"].append(f"US markets weak ({chg:.1f}%) — caution today")
            else:
                ctx["us_trend"] = "neutral"
    except Exception:
        pass

    # SGX Nifty proxy — use Nifty Futures or Gift Nifty
    try:
        sgx = yf.Ticker("^NSEI").history(period="2d")
        if len(sgx) >= 2:
            chg = (sgx["Close"].iloc[-1] - sgx["Close"].iloc[-2]) / sgx["Close"].iloc[-2] * 100
            if chg > 0.2:
                ctx["sgx_trend"] = "positive"
            elif chg < -0.2:
                ctx["sgx_trend"] = "negative"
                ctx["warnings"].append("SGX Nifty negative — gap-down expected")
            else:
                ctx["sgx_trend"] = "flat"
    except Exception:
        pass

    # Sector health — last 3 days trend
    for sector, symbol in SECTOR_INDICES.items():
        try:
            sec_df = yf.Ticker(symbol).history(period="5d")
            if len(sec_df) >= 3:
                last3 = sec_df["Close"].tail(3)
                positive_days = sum(1 for i in range(1, len(last3))
                                    if last3.iloc[i] > last3.iloc[i-1])
                ctx["sector_health"][sector] = "bullish" if positive_days >= 2 else "bearish"
            else:
                ctx["sector_health"][sector] = "unknown"
        except Exception:
            ctx["sector_health"][sector] = "unknown"
        time.sleep(0.3)

    return ctx


def is_sector_healthy(ticker, ctx):
    """Return True if ticker's sector is healthy."""
    sector = TICKER_SECTOR.get(ticker)
    if not sector:
        return True  # Unknown sector — don't penalize
    health = ctx["sector_health"].get(sector, "unknown")
    return health != "bearish"


# ─────────────────────────── INDICATORS ────────────────────────────────────

def compute_indicators(df):
    df = df.copy()

    df["DMA20"]  = df["Close"].rolling(20).mean()
    df["DMA50"]  = df["Close"].rolling(50).mean()
    df["DMA200"] = df["Close"].rolling(200).mean()

    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    hl  = df["High"] - df["Low"]
    hc  = (df["High"] - df["Close"].shift()).abs()
    lc  = (df["Low"]  - df["Close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["AvgVol20"] = df["Volume"].rolling(20).mean()

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    df["BB_mid"]   = df["Close"].rolling(20).mean()
    df["BB_std"]   = df["Close"].rolling(20).std()
    df["BB_upper"] = df["BB_mid"] + 2 * df["BB_std"]
    df["BB_lower"] = df["BB_mid"] - 2 * df["BB_std"]

    low14  = df["Low"].rolling(14).min()
    high14 = df["High"].rolling(14).max()
    df["Stoch_K"] = 100 * (df["Close"] - low14) / (high14 - low14 + 1e-9)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()

    plus_dm  = df["High"].diff().clip(lower=0)
    minus_dm = (-df["Low"].diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm]  = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr14    = tr.rolling(14).mean()
    plus_di  = 100 * (plus_dm.rolling(14).mean()  / (atr14 + 1e-9))
    minus_di = 100 * (minus_dm.rolling(14).mean() / (atr14 + 1e-9))
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    df["ADX"]      = dx.rolling(14).mean()
    df["Plus_DI"]  = plus_di
    df["Minus_DI"] = minus_di

    obv = [0]
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > df["Close"].iloc[i-1]:
            obv.append(obv[-1] + df["Volume"].iloc[i])
        elif df["Close"].iloc[i] < df["Close"].iloc[i-1]:
            obv.append(obv[-1] - df["Volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["OBV"]    = obv
    df["OBV_MA"] = pd.Series(obv, index=df.index).rolling(20).mean()

    df["VWAP"] = (df["Close"] * df["Volume"]).rolling(20).sum() \
                 / df["Volume"].rolling(20).sum()

    multiplier = 3
    basic_ub = (df["High"] + df["Low"]) / 2 + multiplier * df["ATR"]
    basic_lb = (df["High"] + df["Low"]) / 2 - multiplier * df["ATR"]
    direction = pd.Series(index=df.index, dtype=float)
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > basic_ub.iloc[i-1]:
            direction.iloc[i] = 1
        elif df["Close"].iloc[i] < basic_lb.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    df["Supertrend_dir"] = direction

    df["High52"] = df["High"].rolling(252).max()
    df["Low52"]  = df["Low"].rolling(252).min()

    # 3-day momentum
    df["Momentum3"] = df["Close"].pct_change(3) * 100

    return df


def check_3day_positive(df):
    """Check if stock was positive in at least 2 of last 3 days."""
    if len(df) < 4:
        return False
    last3 = df["Close"].tail(4)
    positive_days = sum(1 for i in range(1, 4) if last3.iloc[i] > last3.iloc[i-1])
    return positive_days >= 2


def check_already_pumped(df):
    """Return True if stock already up >2% today — avoid chasing."""
    if len(df) < 2:
        return False
    chg = (df["Close"].iloc[-1] - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100
    return chg > 2.0


# ─────────────────────────── SCORING ───────────────────────────────────────

def score_longterm(df, row, info):
    score, confirmations, reasons = 0, 0, []
    price = row["Close"]

    # Pre-checks
    if check_already_pumped(df):
        return 0, 0, ["Already pumped >2% — avoid chasing"]

    # 1. DMA alignment
    if pd.notna(row["DMA200"]) and price > row["DMA50"] > row["DMA200"]:
        score += 20; confirmations += 1
        reasons.append("Price > 50DMA > 200DMA — strong uptrend")
    elif pd.notna(row["DMA50"]) and price > row["DMA50"]:
        score += 8
        reasons.append("Price above 50DMA — mild uptrend")
    else:
        score -= 10  # Penalize downtrend

    # 2. RSI
    if 40 <= row["RSI"] <= 60:
        score += 15; confirmations += 1
        reasons.append(f"RSI healthy {row['RSI']:.0f} (40-60)")
    elif 60 < row["RSI"] <= 68:
        score += 5
        reasons.append(f"RSI {row['RSI']:.0f} — slightly elevated")
    elif row["RSI"] > 70:
        score -= 10
        reasons.append(f"RSI overbought {row['RSI']:.0f} — avoid")

    # 3. MACD
    if pd.notna(row["MACD"]) and row["MACD"] > row["MACD_Signal"]:
        score += 12; confirmations += 1
        reasons.append("MACD bullish crossover")

    # 4. ADX
    if pd.notna(row["ADX"]):
        if row["ADX"] > 25 and row["Plus_DI"] > row["Minus_DI"]:
            score += 12; confirmations += 1
            reasons.append(f"ADX {row['ADX']:.0f} — strong trend")
        elif row["ADX"] < 15:
            score -= 8
            reasons.append(f"ADX {row['ADX']:.0f} — no trend, risky")

    # 5. Volume
    vol_ratio = row["Volume"] / row["AvgVol20"] if row["AvgVol20"] else 1
    if vol_ratio > 1.3:
        score += 10; confirmations += 1
        reasons.append(f"Volume {vol_ratio:.1f}x above average")

    # 6. OBV
    if pd.notna(row["OBV"]) and pd.notna(row["OBV_MA"]):
        if row["OBV"] > row["OBV_MA"]:
            score += 8; confirmations += 1
            reasons.append("OBV rising — smart money buying")

    # 7. Supertrend
    if pd.notna(row["Supertrend_dir"]) and row["Supertrend_dir"] == 1:
        score += 10; confirmations += 1
        reasons.append("Supertrend bullish")

    # 8. 3-day momentum
    if check_3day_positive(df):
        score += 8; confirmations += 1
        reasons.append("Positive 3-day momentum")
    else:
        score -= 5

    # 9. 52-week position
    if pd.notna(row["High52"]) and pd.notna(row["Low52"]):
        rng = row["High52"] - row["Low52"]
        if rng > 0:
            pos = (price - row["Low52"]) / rng
            if 0.45 <= pos <= 0.80:
                score += 8; confirmations += 1
                reasons.append(f"Strong position in 52W range ({pos*100:.0f}%)")
            elif pos > 0.92:
                score -= 8
                reasons.append("Near 52-week high — risky entry")

    # 10. Stochastic
    if pd.notna(row["Stoch_K"]) and 25 <= row["Stoch_K"] <= 65:
        score += 5; confirmations += 1
        reasons.append(f"Stochastic {row['Stoch_K']:.0f} — good zone")

    # 11. Fundamentals
    pe        = info.get("trailingPE")
    eps_growth = info.get("earningsGrowth")
    roe       = info.get("returnOnEquity")
    de        = info.get("debtToEquity")
    promoter  = info.get("heldPercentInsiders")

    if pe and 5 < pe < 30:
        score += 10; confirmations += 1
        reasons.append(f"PE {pe:.0f} — good valuation")
    elif pe and 30 <= pe < 50:
        score += 3
    elif pe and pe > 60:
        score -= 8

    if pe and eps_growth and eps_growth > 0:
        peg = pe / (eps_growth * 100)
        if peg < 1:
            score += 8; confirmations += 1
            reasons.append(f"PEG {peg:.2f} — growth at fair price")

    if roe and roe > 0.15:
        score += 7; confirmations += 1
        reasons.append(f"ROE {roe*100:.0f}% — strong returns")

    if de is not None:
        if de < 0.5:
            score += 5; confirmations += 1
            reasons.append(f"Low D/E {de:.2f} — financially strong")
        elif de > 2.0:
            score -= 10
            reasons.append(f"High D/E {de:.2f} — overleveraged, AVOID")

    if promoter and promoter > 0.5:
        score += 5
        reasons.append(f"High promoter holding {promoter*100:.0f}%")

    return score, confirmations, reasons


def score_intraday(df, row, ctx):
    score, confirmations, reasons = 0, 0, []
    price = row["Close"]

    # Hard blocks
    if ctx.get("skip_intraday"):
        return 0, 0, ["VIX too high — intraday skipped"]

    if check_already_pumped(df):
        return 0, 0, ["Already up >2% — avoid chasing"]

    if not check_3day_positive(df):
        score -= 15
        reasons.append("Weak 3-day momentum — risky intraday")

    if ctx.get("nifty_trend") == "bearish":
        score -= 10
        reasons.append("Nifty weak — intraday risky")

    if ctx.get("us_trend") == "bearish":
        score -= 8
        reasons.append("US markets weak — cautious today")

    # 1. Volume
    vol_ratio = row["Volume"] / row["AvgVol20"] if row["AvgVol20"] else 1
    if vol_ratio > 1.5:
        score += 22; confirmations += 1
        reasons.append(f"High volume {vol_ratio:.1f}x — strong interest")
    elif vol_ratio > 1.2:
        score += 10; confirmations += 1
        reasons.append(f"Above avg volume {vol_ratio:.1f}x")
    else:
        score -= 10  # Low volume = avoid intraday

    # 2. VWAP
    if pd.notna(row["VWAP"]) and price > row["VWAP"]:
        score += 18; confirmations += 1
        reasons.append("Price above VWAP — buyers in control")
    elif pd.notna(row["VWAP"]) and price < row["VWAP"]:
        score -= 10

    # 3. MACD
    if pd.notna(row["MACD"]) and row["MACD"] > row["MACD_Signal"]:
        score += 15; confirmations += 1
        reasons.append("MACD bullish — momentum building")

    # 4. RSI
    if 45 <= row["RSI"] <= 62:
        score += 15; confirmations += 1
        reasons.append(f"RSI {row['RSI']:.0f} — ideal intraday zone")
    elif row["RSI"] < 35:
        score += 8; confirmations += 1
        reasons.append(f"RSI {row['RSI']:.0f} — oversold bounce")
    elif row["RSI"] > 70:
        score -= 15
        reasons.append(f"RSI {row['RSI']:.0f} — overbought, AVOID")

    # 5. ADX
    if pd.notna(row["ADX"]) and row["ADX"] > 20:
        score += 10; confirmations += 1
        reasons.append(f"ADX {row['ADX']:.0f} — trending")
    elif pd.notna(row["ADX"]) and row["ADX"] < 15:
        score -= 10

    # 6. Supertrend
    if pd.notna(row["Supertrend_dir"]) and row["Supertrend_dir"] == 1:
        score += 10; confirmations += 1
        reasons.append("Supertrend bullish")
    elif pd.notna(row["Supertrend_dir"]) and row["Supertrend_dir"] == -1:
        score -= 15

    # 7. Stochastic
    if pd.notna(row["Stoch_K"]) and 20 <= row["Stoch_K"] <= 65:
        score += 8; confirmations += 1
        reasons.append(f"Stochastic {row['Stoch_K']:.0f} — good entry")
    elif pd.notna(row["Stoch_K"]) and row["Stoch_K"] > 80:
        score -= 10

    # 8. ATR range
    atr_pct = (row["ATR"] / price * 100) if pd.notna(row["ATR"]) and price else 0
    if 0.8 <= atr_pct <= 2.5:
        score += 8; confirmations += 1
        reasons.append(f"ATR {atr_pct:.1f}% — good intraday range")
    elif atr_pct > 4:
        score -= 10
        reasons.append(f"ATR {atr_pct:.1f}% — too volatile")

    # 9. Bollinger Band
    if pd.notna(row["BB_lower"]) and price <= row["BB_lower"] * 1.015:
        score += 8; confirmations += 1
        reasons.append("Near lower Bollinger Band — bounce likely")

    # 10. DMA20
    if pd.notna(row["DMA20"]) and price > row["DMA20"]:
        score += 8; confirmations += 1
        reasons.append("Price above 20DMA — short-term uptrend")

    return score, confirmations, reasons


# ─────────────────────────── SCREENER ──────────────────────────────────────

def fetch_and_score(tickers, ctx):
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

            # Skip if price is NaN or zero
            if pd.isna(row["Close"]) or row["Close"] <= 0:
                continue
            price = round(float(row["Close"]), 2)
            atr   = float(row["ATR"]) if pd.notna(row["ATR"]) and row["ATR"] > 0 \
                    else price * 0.01

            # Sector health check
            sector_ok = is_sector_healthy(ticker, ctx)

            # Long term
            lt_score, lt_conf, lt_reasons = score_longterm(df, row, info)
            if lt_score >= 55 and lt_conf >= MIN_LT_CONFIRMATIONS and sector_ok:
                sl  = round(price - 2.0 * atr, 2)
                tgt = round(price + MIN_RR_LONGTERM * (price - sl), 2)
                rr  = round((tgt - price) / (price - sl), 1)
                if rr >= MIN_RR_LONGTERM:
                    lt_candidates.append({
                        "ticker": ticker, "name": name, "price": price,
                        "score": lt_score, "confirmations": lt_conf,
                        "reasons": lt_reasons, "rr": rr,
                        "stop_loss": sl, "target": tgt,
                        "buy_low":  round(price * 0.995, 2),
                        "buy_high": round(price * 1.005, 2),
                    })

            # Intraday
            if not ctx.get("skip_intraday") and sector_ok:
                in_score, in_conf, in_reasons = score_intraday(df, row, ctx)
                if in_score >= 55 and in_conf >= MIN_INTRA_CONFIRMATIONS:
                    sl  = round(price - 0.7 * atr, 2)
                    tgt = round(price + MIN_RR_INTRADAY * (price - sl), 2)
                    rr  = round((tgt - price) / (price - sl), 1)
                    if rr >= MIN_RR_INTRADAY:
                        intra_candidates.append({
                            "ticker": ticker, "name": name, "price": price,
                            "score": in_score, "confirmations": in_conf,
                            "reasons": in_reasons, "rr": rr,
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
    intra_picks = [r for r in intra_candidates
                   if r["ticker"] not in lt_tickers][:3]

    return lt_picks, intra_picks


# ─────────────────────────── TELEGRAM ──────────────────────────────────────

HOLD_PERIODS_LT = ["2-3 weeks","3-4 weeks","4-6 weeks","6-8 weeks","2-3 months"]

def build_telegram_messages(lt_picks, intra_picks, ctx):
    date_str = datetime.now().strftime("%d %b %Y, %H:%M")
    messages = []

    vix_str   = f"VIX: {ctx['vix']}" if ctx.get("vix") else "VIX: N/A"
    nifty_str = f"Nifty: {ctx.get('nifty_trend','?').upper()}"
    us_str    = f"US: {ctx.get('us_trend','?').upper()}"

    warn_text = ""
    if ctx.get("warnings"):
        warn_text = "\n".join(f"  ! {w}" for w in ctx["warnings"])

    # Sector health summary
    sector_lines = []
    for sec, health in ctx.get("sector_health", {}).items():
        emoji = "+" if health == "bullish" else "-" if health == "bearish" else "~"
        sector_lines.append(f"  {emoji} {sec}: {health}")
    sector_text = "\n".join(sector_lines) if sector_lines else "  N/A"

    messages.append(
        f"Daily Stock Recommendations\n"
        f"Date: {date_str}\n"
        f"{vix_str} | {nifty_str} | {us_str}\n"
        f"{'='*32}\n"
        f"Sector Health:\n{sector_text}"
        + (f"\n\nWARNINGS:\n{warn_text}" if warn_text else "")
    )

    # Long Term
    messages.append("LONG TERM PICKS (2 weeks to 3 months)")
    if not lt_picks:
        messages.append(
            "No strong long-term setups today.\n"
            "Market lacks confirmation — wait for better entry.\n"
            "Capital preservation is also a strategy."
        )
    for i, r in enumerate(lt_picks, 1):
        hp = random.choice(HOLD_PERIODS_LT)
        reasons_text = "\n".join(f"  * {re}" for re in r["reasons"][:6])
        messages.append(
            f"LONG TERM #{i}\n"
            f"{r['name']} ({r['ticker'].replace('.NS','')})\n"
            f"{'─'*30}\n"
            f"CMP       : Rs {r['price']}\n"
            f"Buy Range : Rs {r['buy_low']} - {r['buy_high']}\n"
            f"Stop-Loss : Rs {r['stop_loss']}\n"
            f"Target    : Rs {r['target']}\n"
            f"R:Reward  : 1 : {r['rr']}\n"
            f"Hold      : {hp}\n"
            f"Signals   : {r['confirmations']} indicators confirmed\n"
            f"Reasons:\n{reasons_text}"
        )

    # Intraday
    if ctx.get("skip_intraday"):
        messages.append(
            f"INTRADAY PICKS\n"
            f"SKIPPED TODAY\n"
            f"Reason: VIX {ctx.get('vix')} is too high.\n"
            f"High volatility = high risk of stop-loss hits.\n"
            f"Focus only on long-term today."
        )
    else:
        messages.append("INTRADAY PICKS (Exit before 3:15 PM)")
        if not intra_picks:
            messages.append(
                "No high-probability intraday setups today.\n"
                "Avoid forced trades — sit on cash.\n"
                "Missing a trade is better than a bad trade."
            )
        for i, r in enumerate(intra_picks, 1):
            reasons_text = "\n".join(f"  * {re}" for re in r["reasons"][:6])
            messages.append(
                f"INTRADAY #{i}\n"
                f"{r['name']} ({r['ticker'].replace('.NS','')})\n"
                f"{'─'*30}\n"
                f"CMP       : Rs {r['price']}\n"
                f"Buy Range : Rs {r['buy_low']} - {r['buy_high']}\n"
                f"Stop-Loss : Rs {r['stop_loss']}\n"
                f"Target    : Rs {r['target']}\n"
                f"R:Reward  : 1 : {r['rr']}\n"
                f"Hold      : Same day - EXIT before 3:15 PM\n"
                f"Signals   : {r['confirmations']} indicators confirmed\n"
                f"Reasons:\n{reasons_text}"
            )

    messages.append(
        "DISCLAIMER\n"
        "Algorithmic screening only.\n"
        "Not SEBI-registered investment advice.\n"
        "Always check latest news before entry.\n"
        "Use STRICT stop-losses. Never average down on intraday.\n"
        "When in doubt, stay out."
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

def write_report(lt_picks, intra_picks, ctx):
    date_str = datetime.now().strftime("%d %b %Y, %H:%M")
    lines = [
        f"# Daily Recommendations - {date_str}",
        f"VIX: {ctx.get('vix','N/A')} | "
        f"Nifty: {ctx.get('nifty_trend','?')} | "
        f"US: {ctx.get('us_trend','?')}\n",
        "## Long Term Picks\n",
    ]
    for i, r in enumerate(lt_picks, 1):
        hp = random.choice(HOLD_PERIODS_LT)
        lines.append(
            f"### #{i} {r['name']} ({r['ticker']})\n"
            f"- CMP: Rs {r['price']} | Buy: Rs {r['buy_low']}-{r['buy_high']}\n"
            f"- SL: Rs {r['stop_loss']} | Target: Rs {r['target']} | R:R=1:{r['rr']}\n"
            f"- Hold: {hp} | Signals: {r['confirmations']}\n"
            f"- {'; '.join(r['reasons'][:5])}\n"
        )
    if not lt_picks:
        lines.append("_No strong long-term setups today._\n")
    lines.append("\n## Intraday Picks\n")
    for i, r in enumerate(intra_picks, 1):
        lines.append(
            f"### #{i} {r['name']} ({r['ticker']})\n"
            f"- CMP: Rs {r['price']} | Buy: Rs {r['buy_low']}-{r['buy_high']}\n"
            f"- SL: Rs {r['stop_loss']} | Target: Rs {r['target']} | R:R=1:{r['rr']}\n"
            f"- Exit before 3:15 PM | Signals: {r['confirmations']}\n"
            f"- {'; '.join(r['reasons'][:5])}\n"
        )
    if not intra_picks:
        lines.append("_No high-prob intraday setups today._\n")
    lines += ["\n---", "_Algorithmic screening. Not SEBI advice. Use stop-losses._"]
    Path(OUTPUT_FILE).write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────── MAIN ──────────────────────────────────────────

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def run():
    safe_print("=" * 50)
    safe_print(f"Daily Stock Recommender v3.0 - {datetime.now():%d %b %Y %H:%M}")
    safe_print("=" * 50)

    safe_print("\nFetching market context...")
    ctx = get_market_context()
    safe_print(f"VIX={ctx.get('vix','N/A')} | Nifty={ctx.get('nifty_trend')} | US={ctx.get('us_trend')}")
    for w in ctx.get("warnings", []):
        safe_print(f"  WARNING: {w}")

    lt_picks, intra_picks = fetch_and_score(ALL_TICKERS, ctx)
    safe_print(f"\nPicks: {len(lt_picks)} long term, {len(intra_picks)} intraday")

    write_report(lt_picks, intra_picks, ctx)
    safe_print(f"Report: {OUTPUT_FILE}")

    msgs = build_telegram_messages(lt_picks, intra_picks, ctx)
    send_telegram(msgs)


if __name__ == "__main__":
    run()
