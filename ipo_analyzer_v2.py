"""
IPO Analyzer V4
================
Runs at 2:00 PM IST and sends a Telegram summary of:
- IPOs closing today
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
import html
import sys
import time
from urllib.parse import quote, urljoin
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

    # Optional verified fundamentals/offer details. Never invent missing values.
    revenue_growth = safe_float(pick("revenueGrowth", "revenue_growth", "revenueGrowthPct"))
    profit_growth = safe_float(pick("profitGrowth", "profit_growth", "profitGrowthPct"))
    pe = safe_float(pick("pe", "PE", "priceEarnings", "peRatio"))
    debt_equity = safe_float(pick("debtEquity", "debtToEquity", "debt_equity"))
    fresh_issue_pct = safe_float(pick("freshIssuePct", "fresh_issue_pct"))
    ofs_pct = safe_float(pick("ofsPct", "ofs_pct"))

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
        "revenue_growth": revenue_growth,
        "profit_growth": profit_growth,
        "pe": pe,
        "debt_equity": debt_equity,
        "fresh_issue_pct": fresh_issue_pct,
        "ofs_pct": ofs_pct,
        "qib": qib,
        "nii": nii,
        "retail": retail,
        "total": total,
        "raw": raw,
    }




def google_search_html(query):
    """Fetch a Google result page. Used only to discover public source URLs."""
    try:
        url = "https://www.google.com/search?q=" + quote(query)
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.ok:
            return html.unescape(r.text)
    except Exception as e:
        print(f"  ! Search lookup failed: {e}")
    return ""


def strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def discover_source_urls(name):
    """
    Discover direct public IPO pages from established IPO-information sources.
    The search page is only used to locate the source; values are parsed from
    the source page itself.
    """
    queries = [
        f'site:zerodha.com/ipo/ "{name}" IPO',
        f'site:indmoney.com/ipo/ "{name}" IPO',
        f'site:livemint.com/market/ipo/ "{name}" IPO',
        f'site:kotakneo.com/ipo/ "{name}" IPO',
        f'site:ipostation.in/ipo/ "{name}" IPO',
    ]
    urls = []
    patterns = [
        r'https?://(?:www\.)?zerodha\.com/ipo/[^\s"<>\\]+',
        r'https?://(?:www\.)?indmoney\.com/ipo/[^\s"<>\\]+',
        r'https?://(?:www\.)?livemint\.com/market/ipo/[^\s"<>\\]+',
        r'https?://(?:www\.)?kotakneo\.com/ipo/[^\s"<>\\]+',
        r'https?://(?:www\.)?ipostation\.in/ipo/[^\s"<>\\]+',
    ]
    for q in queries:
        page = google_search_html(q)
        for pat in patterns:
            for u in re.findall(pat, page, flags=re.I):
                u = u.rstrip(")&'\\")
                if u not in urls:
                    urls.append(u)
    return urls[:8]


def fetch_source_text(url):
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.ok and len(r.text) > 500:
            return strip_html(r.text), r.url
    except Exception as e:
        print(f"  ! Source page failed {url}: {e}")
    return "", url


def parse_source_facts(ipo, text):
    """Parse common IPO facts/subscription figures from a source page."""
    if not text:
        return ipo

    # Price band
    if ipo.get("price_low") is None or ipo.get("price_high") is None:
        patterns = [
            r'(?:price range|price band)[^₹0-9]{0,50}₹?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:-|–|to)\s*₹?\s*([0-9][0-9,]*(?:\.[0-9]+)?)',
            r'₹\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*[–-]\s*₹\s*([0-9][0-9,]*(?:\.[0-9]+)?)',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                ipo["price_low"] = float(m.group(1).replace(",", ""))
                ipo["price_high"] = float(m.group(2).replace(",", ""))
                break

    # Lot size
    if ipo.get("lot_size") is None:
        for pat in [
            r'lot size[^0-9]{0,30}([0-9]{1,5})\s*(?:shares?)?',
            r'minimum investment[^0-9]{0,30}[₹]?\s*[0-9,]+\s*/\s*([0-9]{1,5})\s*shares?',
        ]:
            m = re.search(pat, text, re.I)
            if m:
                ipo["lot_size"] = float(m.group(1))
                break

    # Issue size
    if ipo.get("issue_size") is None:
        for pat in [
            r'(?:total issue size|issue size|funds raised)[^₹0-9]{0,40}(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:cr|crore)',
        ]:
            m = re.search(pat, text, re.I)
            if m:
                ipo["issue_size"] = float(m.group(1).replace(",", ""))
                break

    # Subscription values. These patterns work with both prose and table text.
    sub_patterns = {
        "qib": [
            r'(?:institutional|QIB|qualified institutional buyers?)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)\s*x',
        ],
        "nii": [
            r'(?:NII|non[- ]institutional investors?|HNI)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)\s*x',
        ],
        "retail": [
            r'(?:retail(?: individual investors?)?|RII)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)\s*x',
        ],
        "total": [
            r'(?:total|overall)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)\s*x',
        ],
    }
    for key, pats in sub_patterns.items():
        if ipo.get(key) is None:
            for pat in pats:
                m = re.search(pat, text, re.I)
                if m:
                    ipo[key] = float(m.group(1))
                    break

    # GMP: only from explicit GMP wording; never treat generic numbers as GMP.
    if ipo.get("gmp") is None:
        for pat in [
            r'(?:grey market premium|GMP)[^₹0-9]{0,40}(?:₹|Rs\.?|INR)?\s*([0-9]+(?:\.[0-9]+)?)',
        ]:
            m = re.search(pat, text, re.I)
            if m:
                ipo["gmp"] = float(m.group(1))
                ipo["gmp_source"] = "External IPO source (unofficial GMP)"
                break

    return ipo


def google_search_text(query):
    return strip_html(google_search_html(query))


def enrich_from_web(ipo):
    """
    Fill missing fields from direct public IPO pages discovered via search.
    Official NSE data remains preferred. Secondary pages are used only to
    fill fields absent from the NSE payload. GMP is always unofficial.
    """
    today = datetime.now(IST).date()
    date_text = today.strftime("%d %B %Y")
    urls = discover_source_urls(ipo["name"])

    # Prefer Zerodha/IndMoney/Livemint/KotakNeo pages because they publish
    # structured issue and subscription tables.
    for url in urls:
        text, final_url = fetch_source_text(url)
        if not text:
            continue
        before = tuple(ipo.get(k) for k in
                       ("price_low","price_high","lot_size","issue_size","qib","nii","retail","total","gmp"))
        parse_source_facts(ipo, text)
        after = tuple(ipo.get(k) for k in
                      ("price_low","price_high","lot_size","issue_size","qib","nii","retail","total","gmp"))
        if after != before:
            print(f"  + Enriched from {final_url}")

        # Stop once all core issue/subscription fields are available.
        core = ("price_low","price_high","lot_size","issue_size","qib","nii","retail","total")
        if all(ipo.get(k) is not None for k in core):
            break

    # A second targeted search can still help when a source page was not
    # discoverable. It uses today's date rather than a hard-coded date.
    missing_core = [k for k in
                    ("price_low","price_high","lot_size","issue_size","qib","nii","retail","total")
                    if ipo.get(k) is None]
    if missing_core:
        s = google_search_text(
            f'"{ipo["name"]}" IPO {date_text} price band lot size subscription QIB NII retail total'
        )
        parse_source_facts(ipo, s)

    # GMP is separate and unofficial.
    if ipo.get("gmp") is None:
        s = google_search_text(f'"{ipo["name"]}" IPO GMP {date_text}')
        parse_source_facts(ipo, s)

    return ipo

def next_working_day(day):
    d = day + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def is_open_or_closing_soon(ipo, today):
    """Keep currently open IPOs plus issues closing today or next working day."""
    o = ipo["open_date"]
    c = ipo["close_date"]
    if o and c:
        next_day = next_working_day(today)
        return o <= today <= c or c in {today, next_day}
    return True


def extract_gmp_from_text(text, ipo_name=None):
    """
    Best-effort GMP parser. GMP is unofficial and may be stale.
    Require the IPO name to appear in the returned page text when possible.
    """
    if not text:
        return None

    if ipo_name:
        name_tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]{3,}", ipo_name)]
        hay = re.sub(r"\s+", " ", text.lower())
        # Require at least one meaningful company-name token; this reduces
        # accidental matches from generic search pages.
        if name_tokens and not any(tok in hay for tok in name_tokens[:3]):
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
    GMP is unofficial. Search results are treated only as a secondary signal.
    If the IPO name cannot be matched confidently, return N/A.
    """
    try:
        query = requests.utils.quote(f'"{ipo_name}" IPO GMP today')
        url = f"https://www.google.com/search?q={query}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.ok:
            gmp = extract_gmp_from_text(r.text, ipo_name=ipo_name)
            if gmp is not None:
                return {"gmp": gmp, "source": "External web search (unofficial GMP)"}
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
    Multi-factor score out of 100.
    Subscription/GMP are signals, not guarantees.
    Fundamental/valuation data is included only when verified; unavailable fields are not guessed.
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

    # Optional verified fundamentals: add modest weight, never fabricate.
    revenue_growth = ipo.get("revenue_growth")
    profit_growth = ipo.get("profit_growth")
    pe = ipo.get("pe")
    debt_equity = ipo.get("debt_equity")

    if revenue_growth is not None:
        if revenue_growth >= 20:
            score += 5
            reasons.append(f"Revenue growth {revenue_growth:.1f}% — strong")
        elif revenue_growth >= 10:
            score += 3
            reasons.append(f"Revenue growth {revenue_growth:.1f}% — healthy")

    if profit_growth is not None:
        if profit_growth >= 20:
            score += 5
            reasons.append(f"Profit growth {profit_growth:.1f}% — strong")
        elif profit_growth >= 10:
            score += 3
            reasons.append(f"Profit growth {profit_growth:.1f}% — healthy")

    if pe is not None:
        if pe <= 20:
            score += 5
            reasons.append(f"P/E {pe:.1f} — reasonable")
        elif pe <= 35:
            score += 2
            reasons.append(f"P/E {pe:.1f} — elevated")
        else:
            reasons.append(f"P/E {pe:.1f} — expensive")

    if debt_equity is not None:
        if debt_equity <= 0.5:
            score += 5
            reasons.append(f"Debt/Equity {debt_equity:.2f} — low")
        elif debt_equity <= 1.5:
            score += 2
            reasons.append(f"Debt/Equity {debt_equity:.2f} — moderate")
        else:
            reasons.append(f"Debt/Equity {debt_equity:.2f} — high")

    # V2 is conservative when key demand/GMP data is missing.
    known = sum(x is not None for x in (qib, nii, retail, total, gmp_pct))
    if known < 3:
        return None, reasons + ["Insufficient verified data — no recommendation"]

    score = min(score, 100)

    if score >= 70:
        action = "APPLY"
    elif score >= 50:
        action = "APPLY SELECTIVELY"
    else:
        action = "AVOID"

    return score, reasons + [f"Rule-based action: {action}"]


def action_from_score(score):
    if score is None:
        return "⚪ INSUFFICIENT DATA"
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

    # Analyze IPOs only when their official close date is TODAY.
    closing_today = [
        ipo for ipo in ipos
        if ipo.get("close_date") == today
    ]

    if not closing_today:
        print("No IPO closing today — sending status message only.")
        return []

    print(f"Found {len(closing_today)} IPO(s) closing today.")

    results = []
    for ipo in closing_today:
        ipo = enrich_from_web(ipo)

        if ipo.get("gmp") is None:
            gmp_info = fetch_gmp(ipo["name"])
            ipo["gmp"] = gmp_info["gmp"]
            ipo["gmp_source"] = gmp_info["source"]
        elif not ipo.get("gmp_source"):
            ipo["gmp_source"] = "External web search (unofficial GMP)"

        listing, gain_pct = estimate_listing(ipo)
        ipo["estimated_listing"] = listing
        ipo["gmp_pct"] = gain_pct

        score, reasons = score_ipo(ipo)
        ipo["score"] = score
        ipo["reasons"] = reasons
        ipo["action"] = action_from_score(score)

        results.append(ipo)
        time.sleep(0.5)

    results.sort(key=lambda x: -(x["score"] or 0))
    return results

def build_messages(results):
    now = datetime.now(IST)
    lines = [
        "🏦 IPO ANALYZER",
        f"Checked: {now:%d %b %Y, %I:%M %p} IST",
        "=" * 32,
        "Official issue/subscription facts: NSE/official data",
        "GMP: unofficial external grey-market signal",
        "Secondary IPO pages are used only to fill fields missing from NSE.",
        "",
    ]

    if not results:
        lines += [
            "ℹ️ NO IPO CLOSING TODAY",
            "",
            "No IPO is scheduled to close today.",
            "No IPO analysis is required today.",
            "",
            "Next check: Tomorrow at 2:00 PM IST.",
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
                f"Lot Size   : {int(ipo['lot_size']) if ipo.get('lot_size') is not None and float(ipo['lot_size']).is_integer() else ipo.get('lot_size') or 'N/A'}",
                f"QIB        : {format_sub(ipo['qib'])}",
                f"NII/HNI    : {format_sub(ipo['nii'])}",
                f"Retail     : {format_sub(ipo['retail'])}",
                f"Overall    : {format_sub(ipo['total'])}",
                f"GMP        : ₹{gmp_text.replace('₹','')} (unofficial)",
                f"Est Listing: {listing_text}",
                f"Est Gain   : {gain_text}",
                f"Revenue    : {ipo.get('revenue_growth'):.1f}%" if ipo.get('revenue_growth') is not None else "Revenue    : N/A",
                f"Profit     : {ipo.get('profit_growth'):.1f}%" if ipo.get('profit_growth') is not None else "Profit     : N/A",
                f"P/E        : {ipo.get('pe'):.1f}" if ipo.get('pe') is not None else "P/E        : N/A",
                f"Debt/Equity: {ipo.get('debt_equity'):.2f}" if ipo.get('debt_equity') is not None else "Debt/Equity: N/A",
                f"IPO Score  : {ipo['score']}/100" if ipo['score'] is not None else "IPO Score  : N/A",
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
    print(f"IPO Analyzer V4 — {datetime.now(IST):%d %b %Y %H:%M IST}")
    print("=" * 50)

    results = analyze()

    # Always send a Telegram status message.
    if not results:
        messages = build_messages([])
        send_telegram(messages)
        return

    write_report(results)
    messages = build_messages(results)
    send_telegram(messages)


if __name__ == "__main__":
    main()
