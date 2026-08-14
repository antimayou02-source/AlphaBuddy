"""
IPO Analyzer V1
================
Runs at 2:00 PM IST and sends a Telegram summary of:
- IPOs currently open / closing today or next working day
- Issue price, dates, lot size and issue size when available
- Subscription by QIB / NII / Retail / Total when available
- GMP and estimated listing gain from a clearly-labelled external GMP source
- A rule-based APPLY / APPLY SELECTIVELY / AVOID signal

IMPORTANT:
1. NSE/BSE/official offer documents are the source of truth for issue facts and subscription data.
2. GMP is unofficial grey-market information. It is NOT an NSE figure.
3. If a field cannot be verified, the script shows N/A rather than inventing a value.
4. This is an algorithmic screening tool, not SEBI-registered investment advice.
"""

import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

IST = ZoneInfo("Asia/Kolkata")
NSE_HOME = "https://www.nseindia.com"
NSE_IPO_PAGE = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
NSE_IPO_API = "https://www.nseindia.com/api/ipo-current-issue"

REQUEST_TIMEOUT = 20
TELEGRAM_LIMIT = 4000
OUTPUT_FILE = f"ipo_report_{datetime.now(IST):%Y-%m-%d}.md"

# GMP is unofficial. Keep this configurable so the source can be changed
# without changing the analyzer logic.
GMP_SOURCE_URL = os.environ.get(
    "IPO_GMP_SOURCE_URL",
    "https://www.google.com/search?q=IPO+GMP+today+India"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/151.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": NSE_IPO_PAGE,
}

session = requests.Session()
session.headers.update(HEADERS)


def safe_float(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace("₹", "").strip()
    if not s or s.lower() in {"na", "n/a", "-", "none", "null"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def parse_date(value):
    if value is None:
        return None
    try:
        return pd.to_datetime(value, dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def get_nse_session():
    """Prime NSE cookies before calling its API."""
    try:
        session.get(NSE_HOME, timeout=REQUEST_TIMEOUT)
        session.get(NSE_IPO_PAGE, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print(f"  ! NSE session warning: {e}")


def fetch_nse_ipo_data():
    """
    Fetch current IPO data from NSE's public IPO endpoint.
    NSE can change endpoint fields; unknown fields are preserved where possible.
    """
    get_nse_session()
    try:
        r = session.get(NSE_IPO_API, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"  ! NSE IPO API failed: {e}")
        return []

    if isinstance(payload, dict):
        for key in ("data", "records", "ipoData", "currentIssue"):
            if isinstance(payload.get(key), list):
                return payload[key]
        if all(isinstance(v, (str, int, float, type(None))) for v in payload.values()):
            return [payload]
    if isinstance(payload, list):
        return payload

    return []


def normalize_ipo(raw):
    """Normalize common NSE field-name variants without guessing missing values."""
    def pick(*keys):
        for k in keys:
            if k in raw and raw[k] not in (None, "", "-", "NA", "N/A"):
                return raw[k]
        return None

    name = pick(
        "companyName", "company", "issuerName", "name", "symbol",
        "company_name", "issuer"
    )
    symbol = pick("symbol", "ticker", "issueSymbol")
    open_date = parse_date(pick("issueStartDate", "openDate", "startDate", "fromDate"))
    close_date = parse_date(pick("issueEndDate", "closeDate", "endDate", "toDate"))
    listing_date = parse_date(pick("listingDate", "listDate"))
    issue_size = safe_float(pick("issueSize", "issueSizeValue", "totalIssueSize"))
    price_low = safe_float(pick("priceBandLow", "minPrice", "priceLow", "floorPrice"))
    price_high = safe_float(pick("priceBandHigh", "maxPrice", "priceHigh", "capPrice"))
    lot_size = safe_float(pick("lotSize", "marketLot", "minimumLot"))

    # Subscription fields are intentionally broad because NSE field names can vary.
    qib = safe_float(pick("qib", "qibSubscription", "QIB", "qualifiedInstitutional"))
    nii = safe_float(pick("nii", "niiSubscription", "NII", "hni", "HNI"))
    retail = safe_float(pick("retail", "retailSubscription", "RII", "retailIndividual"))
    total = safe_float(pick("total", "totalSubscription", "overallSubscription", "subscription"))

    return {
        "name": str(name or symbol or "Unknown IPO"),
        "symbol": str(symbol or ""),
        "open_date": open_date,
        "close_date": close_date,
        "listing_date": listing_date,
        "issue_size": issue_size,
        "price_low": price_low,
        "price_high": price_high,
        "lot_size": lot_size,
        "qib": qib,
        "nii": nii,
        "retail": retail,
        "total": total,
        "raw": raw,
    }


def is_open_or_closing_soon(ipo, today):
    """Keep currently open IPOs and IPOs closing today/next working day."""
    o = ipo["open_date"]
    c = ipo["close_date"]
    if o and c:
        return o <= today <= c
    # If dates are unavailable, retain it for visibility rather than guessing.
    return True


def extract_gmp_from_text(text):
    """
    Best-effort GMP parser. This deliberately returns None if confidence is low.
    Expected patterns include:
      GMP: ₹120
      GMP Rs 120
      grey market premium 120
    """
    if not text:
        return None

    patterns = [
        r"(?:GMP|grey market premium)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([0-9]+(?:\.[0-9]+)?)",
        r"(?:premium)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return safe_float(m.group(1))
    return None


def fetch_gmp(ipo_name):
    """
    GMP V1 uses an external search page because GMP is not an NSE field.
    If parsing is not reliable, return N/A rather than fabricating a value.
    """
    try:
        query = requests.utils.quote(f'"{ipo_name}" IPO GMP today')
        url = f"https://www.google.com/search?q={query}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.ok:
            gmp = extract_gmp_from_text(r.text)
            if gmp is not None:
                return {
                    "gmp": gmp,
                    "source": "External web search (unofficial GMP)",
                }
    except Exception as e:
        print(f"  ! GMP lookup failed for {ipo_name}: {e}")

    return {"gmp": None, "source": "N/A — GMP not verified"}


def estimate_listing(ipo):
    """Estimate listing price/gain only when issue price and GMP are both known."""
    issue = ipo["price_high"] or ipo["price_low"]
    gmp = ipo.get("gmp")
    if issue is None or gmp is None:
        return None, None
    listing = issue + gmp
    gain_pct = gmp / issue * 100 if issue else None
    return listing, gain_pct


def score_ipo(ipo):
    """
    Conservative score out of 100.
    Subscription/GMP are signals, not guarantees.
    Fundamental/valuation score is intentionally not invented in V1.
    """
    score = 0
    reasons = []

    total = ipo.get("total")
    qib = ipo.get("qib")
    nii = ipo.get("nii")
    retail = ipo.get("retail")
    gmp_pct = ipo.get("gmp_pct")

    if qib is not None:
        if qib >= 5:
            score += 25
            reasons.append(f"QIB {qib:.1f}x — strong")
        elif qib >= 2:
            score += 18
            reasons.append(f"QIB {qib:.1f}x — healthy")
        elif qib >= 1:
            score += 10
            reasons.append(f"QIB {qib:.1f}x — covered")
        else:
            reasons.append(f"QIB {qib:.1f}x — weak")
    else:
        reasons.append("QIB data unavailable")

    if nii is not None:
        if nii >= 5:
            score += 15
            reasons.append(f"NII/HNI {nii:.1f}x — strong")
        elif nii >= 1:
            score += 10
            reasons.append(f"NII/HNI {nii:.1f}x — covered")

    if retail is not None:
        if retail >= 3:
            score += 10
            reasons.append(f"Retail {retail:.1f}x — strong")
        elif retail >= 1:
            score += 6
            reasons.append(f"Retail {retail:.1f}x — covered")

    if total is not None:
        if total >= 10:
            score += 10
            reasons.append(f"Overall {total:.1f}x — very strong")
        elif total >= 3:
            score += 7
            reasons.append(f"Overall {total:.1f}x — healthy")
        elif total >= 1:
            score += 4
            reasons.append(f"Overall {total:.1f}x — covered")
        else:
            reasons.append(f"Overall {total:.1f}x — weak")

    if gmp_pct is not None:
        if gmp_pct >= 20:
            score += 25
            reasons.append(f"GMP implies ~{gmp_pct:.1f}% listing premium")
        elif gmp_pct >= 10:
            score += 18
            reasons.append(f"GMP implies ~{gmp_pct:.1f}% listing premium")
        elif gmp_pct >= 5:
            score += 10
            reasons.append(f"GMP implies ~{gmp_pct:.1f}% listing premium")
        elif gmp_pct > 0:
            score += 5
            reasons.append(f"GMP implies ~{gmp_pct:.1f}% listing premium")
        else:
            reasons.append("GMP is flat/negative")

    # V1 is deliberately conservative when key data is missing.
    known = sum(x is not None for x in (qib, nii, retail, total, gmp_pct))
    if known < 3:
        return min(score, 59), reasons + ["Insufficient verified data"]

    if score >= 70:
        action = "APPLY"
    elif score >= 50:
        action = "APPLY SELECTIVELY"
    else:
        action = "AVOID"

    return score, reasons + [f"Rule-based action: {action}"]


def action_from_score(score):
    if score >= 70:
        return "🟢 APPLY"
    if score >= 50:
        return "🟡 APPLY SELECTIVELY"
    return "🔴 AVOID"


def format_sub(value):
    return f"{value:.1f}x" if value is not None else "N/A"


def analyze():
    today = datetime.now(IST).date()
    raw = fetch_nse_ipo_data()
    ipos = [normalize_ipo(x) for x in raw]
    ipos = [x for x in ipos if is_open_or_closing_soon(x, today)]

    results = []
    for ipo in ipos:
        gmp_info = fetch_gmp(ipo["name"])
        ipo["gmp"] = gmp_info["gmp"]
        ipo["gmp_source"] = gmp_info["source"]

        listing, gain_pct = estimate_listing(ipo)
        ipo["estimated_listing"] = listing
        ipo["gmp_pct"] = gain_pct

        score, reasons = score_ipo(ipo)
        ipo["score"] = score
        ipo["reasons"] = reasons
        ipo["action"] = action_from_score(score)

        results.append(ipo)
        time.sleep(0.5)

    # Closing soon first, then highest score.
    results.sort(
        key=lambda x: (
            x["close_date"] != today,
            -(x["score"] or 0),
        )
    )
    return results


def build_messages(results):
    now = datetime.now(IST)
    lines = [
        "🏦 IPO ANALYZER",
        f"Checked: {now:%d %b %Y, %I:%M %p} IST",
        "=" * 32,
        "Official issue/subscription facts: NSE/official data",
        "GMP: unofficial external grey-market signal",
        "",
    ]

    if not results:
        lines += [
            "No currently open IPOs were found from the NSE feed.",
            "If an IPO is missing, verify the NSE issue page manually.",
        ]
    else:
        for i, ipo in enumerate(results, 1):
            close_text = ipo["close_date"].strftime("%d %b") if ipo["close_date"] else "N/A"
            price_text = (
                f"₹{ipo['price_low']:.0f}–₹{ipo['price_high']:.0f}"
                if ipo["price_low"] is not None and ipo["price_high"] is not None
                else "N/A"
            )
            gmp_text = f"₹{ipo['gmp']:.0f}" if ipo["gmp"] is not None else "N/A"
            listing_text = (
                f"₹{ipo['estimated_listing']:.0f}"
                if ipo["estimated_listing"] is not None else "N/A"
            )
            gain_text = (
                f"{ipo['gmp_pct']:.1f}%"
                if ipo["gmp_pct"] is not None else "N/A"
            )

            lines += [
                f"{ipo['action']} #{i} — {ipo['name']}",
                "─" * 30,
                f"Close      : {close_text}",
                f"Price Band : {price_text}",
                f"Lot Size   : {ipo['lot_size'] or 'N/A'}",
                f"QIB        : {format_sub(ipo['qib'])}",
                f"NII/HNI    : {format_sub(ipo['nii'])}",
                f"Retail     : {format_sub(ipo['retail'])}",
                f"Overall    : {format_sub(ipo['total'])}",
                f"GMP        : ₹{gmp_text.replace('₹','')} (unofficial)",
                f"Est Listing: {listing_text}",
                f"Est Gain   : {gain_text}",
                f"IPO Score  : {ipo['score']}/100",
                "Why:",
            ]
            lines += [f"  * {r}" for r in ipo["reasons"][:5]]
            lines.append("")

    lines += [
        "⚠️ GMP is unofficial and can change rapidly.",
        "Listing-gain estimates are not guaranteed.",
        "This is algorithmic screening, not SEBI-registered investment advice.",
    ]

    text = "\n".join(lines)
    return [text[i:i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)]


def send_telegram(messages):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ! Telegram secrets not configured")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for message in messages:
        try:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  ! Telegram error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"  ! Telegram error: {e}")


def write_report(results):
    lines = [
        f"# IPO Analyzer — {datetime.now(IST):%d %b %Y %H:%M IST}",
        "",
        "GMP is unofficial. Official issue/subscription data should be verified against NSE/offer documents.",
        "",
    ]

    for ipo in results:
        lines += [
            f"## {ipo['name']}",
            f"- Close: {ipo['close_date'] or 'N/A'}",
            f"- Price band: {ipo['price_low'] or 'N/A'} - {ipo['price_high'] or 'N/A'}",
            f"- QIB: {format_sub(ipo['qib'])}",
            f"- NII/HNI: {format_sub(ipo['nii'])}",
            f"- Retail: {format_sub(ipo['retail'])}",
            f"- Overall: {format_sub(ipo['total'])}",
            f"- GMP: {ipo['gmp'] if ipo['gmp'] is not None else 'N/A'} (unofficial)",
            f"- Estimated listing: {ipo['estimated_listing'] or 'N/A'}",
            f"- Estimated gain: {ipo['gmp_pct'] if ipo['gmp_pct'] is not None else 'N/A'}%",
            f"- Score: {ipo['score']}/100 — {ipo['action']}",
            "",
        ]

    Path(OUTPUT_FILE).write_text("\n".join(lines), encoding="utf-8")


def main():
    print("=" * 50)
    print(f"IPO Analyzer V1 — {datetime.now(IST):%d %b %Y %H:%M IST}")
    print("=" * 50)

    results = analyze()
    print(f"Found {len(results)} open IPO(s).")

    write_report(results)
    messages = build_messages(results)
    send_telegram(messages)


if __name__ == "__main__":
    main()
