"""AlphaBuddy final IPO closing-day analyzer.

Official issue dates/subscription are taken from NSE's current IPO endpoint.
Secondary public IPO pages are used only to fill missing issue facts and GMP.
GMP is always labelled unofficial.
"""
import os, re, html, time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo
import requests

IST=ZoneInfo("Asia/Kolkata")
TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT=os.environ.get("TELEGRAM_CHAT_ID")
TIMEOUT=20
NSE_HOME="https://www.nseindia.com"
NSE_PAGE="https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
NSE_API="https://www.nseindia.com/api/ipo-current-issue"
HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36","Accept":"application/json,text/plain,*/*","Accept-Language":"en-IN,en;q=0.9","Referer":NSE_PAGE}
s=requests.Session(); s.headers.update(HEADERS)


def num(x):
    if x is None: return None
    m=re.search(r"-?\d+(?:\.\d+)?",str(x).replace(",",""))
    return float(m.group()) if m else None


def date(x):
    if not x: return None
    for fmt in ("%d-%b-%Y","%d-%b-%y","%d/%m/%Y","%Y-%m-%d","%d-%m-%Y"):
        try: return datetime.strptime(str(x).strip(),fmt).date()
        except ValueError: pass
    return None


def parse_band(x):
    if not x: return None,None
    vals=re.findall(r"\d+(?:\.\d+)?",str(x).replace(",",""))
    if len(vals)>=2: return float(vals[0]),float(vals[1])
    if len(vals)==1: return float(vals[0]),float(vals[0])
    return None,None


def nse_rows():
    try:
        s.get(NSE_HOME,timeout=TIMEOUT)
        s.get(NSE_PAGE,timeout=TIMEOUT)
        r=s.get(NSE_API,timeout=TIMEOUT); r.raise_for_status(); data=r.json()
        return data if isinstance(data,list) else data.get("data",[])
    except Exception as e:
        print("NSE API failed:",e); return []


def normalize_nse(rows):
    grouped={}
    for r in rows:
        if not isinstance(r,dict): continue
        sym=str(r.get("symbol") or r.get("issueSymbol") or r.get("companyName") or "").strip()
        if not sym: continue
        g=grouped.setdefault(sym,{"name":r.get("companyName") or sym,"symbol":sym,"open_date":None,"close_date":None,"listing_date":None,"price_low":None,"price_high":None,"qib":None,"nii":None,"retail":None,"total":None,"raw":[]})
        g["raw"].append(r)
        g["name"]=r.get("companyName") or g["name"]
        g["open_date"]=g["open_date"] or date(r.get("issueStartDate"))
        g["close_date"]=g["close_date"] or date(r.get("issueEndDate"))
        g["listing_date"]=g["listing_date"] or date(r.get("listingDate"))
        lo,hi=parse_band(r.get("issuePrice") or r.get("priceBand"))
        if lo is not None: g["price_low"]=lo
        if hi is not None: g["price_high"]=hi
        cat=str(r.get("category") or r.get("categoryName") or "").lower().replace(" ","")
        sub=num(r.get("noOfTime") or r.get("subscription"))
        if sub is not None:
            if "qib" in cat or "institution" in cat: g["qib"]=sub
            elif "nii" in cat or "noninstitution" in cat or "hni" in cat: g["nii"]=sub
            elif "retail" in cat or "rii" in cat: g["retail"]=sub
            elif "total" in cat or "overall" in cat: g["total"]=sub
    return list(grouped.values())


def clean_search_page(text):
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.unescape(text or ""))).strip()


def discover_urls(name):
    q=quote(f'"{name}" IPO price band lot size subscription GMP')
    try:
        r=s.get("https://www.google.com/search?q="+q,timeout=TIMEOUT)
        if not r.ok: return []
        page=r.text
    except Exception: return []
    urls=[]
    # Google may use /url?q=... or direct hrefs.
    for raw in re.findall(r'href=["\']([^"\']+)["\']',page,re.I):
        u=raw
        if u.startswith("/url?q="): u=unquote(u[7:].split("&")[0])
        elif u.startswith("/url?"): 
            m=re.search(r"[?&]q=([^&]+)",u); u=unquote(m.group(1)) if m else ""
        if not u.startswith("http"): continue
        if any(d in u for d in ("zerodha.com/ipo/","indmoney.com/ipo/","kotakneo.com/ipo/","moneycontrol.com/ipo/","livemint.com/market/ipo/","ipowatch.in/ipo/","ipomarkets.com/ipo/")):
            if u not in urls: urls.append(u)
    return urls[:10]


def enrich(ipo):
    urls=discover_urls(ipo["name"])
    for u in urls:
        try:
            r=s.get(u,timeout=TIMEOUT); text=clean_search_page(r.text)
        except Exception: continue
        if not text: continue
        if ipo["price_low"] is None or ipo["price_high"] is None:
            m=re.search(r"(?:Price Band|Price Range)[^₹0-9]{0,80}₹?\s*([0-9,]+)\s*[–-]\s*₹?\s*([0-9,]+)",text,re.I)
            if m: ipo["price_low"]=float(m.group(1).replace(",","")); ipo["price_high"]=float(m.group(2).replace(",",""))
        if ipo.get("lot_size") is None:
            m=re.search(r"(?:Lot Size|lot size)[^0-9]{0,40}([0-9]{1,5})\s*shares?",text,re.I)
            if m: ipo["lot_size"]=int(m.group(1))
        if ipo.get("issue_size") is None:
            m=re.search(r"(?:Issue Size|issue size)[^₹0-9]{0,30}(?:₹|Rs\.?|INR)?\s*([0-9,]+(?:\.[0-9]+)?)\s*(?:Cr|crore)",text,re.I)
            if m: ipo["issue_size"]=float(m.group(1).replace(",",""))
        if ipo.get("listing_date") is None:
            m=re.search(r"(?:Listing Date|listing on)[^0-9]{0,30}([0-9]{1,2}\s+[A-Za-z]{3}\s+['’]?[0-9]{2,4})",text,re.I)
            if m: ipo["listing_date"]=date(m.group(1).replace("’",""))
        # Secondary subscription figures only fill missing NSE fields.
        pats={"qib":r"(?:QIB|Qualified Institutional Buyers?)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)x","nii":r"(?:NII|Non[- ]Institutional|HNI)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)x","retail":r"(?:Retail Individual|RII|Retail)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)x","total":r"(?:Total|Overall)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)x"}
        for k,p in pats.items():
            if ipo.get(k) is None:
                m=re.search(p,text,re.I)
                if m: ipo[k]=float(m.group(1))
        if ipo.get("gmp") is None:
            m=re.search(r"(?:GMP|Grey Market Premium)[^₹0-9]{0,50}(?:₹|Rs\.?|INR)?\s*([0-9]+(?:\.[0-9]+)?)",text,re.I)
            if m: ipo["gmp"]=float(m.group(1)); ipo["gmp_source"]="Secondary IPO page (unofficial)"
        if all(ipo.get(k) is not None for k in ("price_low","price_high","lot_size","issue_size","qib","nii","retail","total")) and ipo.get("gmp") is not None: break
    return ipo


def score(ipo):
    score=0; reasons=[]
    q,n,r,t,g=ipo.get("qib"),ipo.get("nii"),ipo.get("retail"),ipo.get("total"),ipo.get("gmp")
    if q is not None: score += 25 if q>=5 else 18 if q>=2 else 10 if q>=1 else 0; reasons.append(f"QIB {q:.2f}x")
    else: reasons.append("QIB data unavailable")
    if n is not None: score += 15 if n>=5 else 10 if n>=1 else 0; reasons.append(f"NII/HNI {n:.2f}x")
    else: reasons.append("NII/HNI data unavailable")
    if r is not None: score += 10 if r>=3 else 6 if r>=1 else 0; reasons.append(f"Retail {r:.2f}x")
    else: reasons.append("Retail data unavailable")
    if t is not None: score += 10 if t>=10 else 7 if t>=3 else 4 if t>=1 else 0; reasons.append(f"Overall {t:.2f}x")
    else: reasons.append("Overall subscription unavailable")
    issue=ipo.get("price_high") or ipo.get("price_low")
    gpct=(g/issue*100) if g is not None and issue else None
    ipo["gmp_pct"]=gpct
    if gpct is not None:
        score += 25 if gpct>=20 else 18 if gpct>=10 else 10 if gpct>=5 else 5 if gpct>0 else 0
        reasons.append(f"GMP implies ~{gpct:.1f}% listing premium")
    else: reasons.append("GMP not verified")
    known=sum(x is not None for x in (q,n,r,t,g))
    if known<3: return None,reasons+["Insufficient verified demand/GMP data — no recommendation"]
    action="APPLY" if score>=70 else "APPLY SELECTIVELY" if score>=50 else "AVOID"
    return min(score,100),reasons+[f"Rule-based action: {action}"]


def telegram(text):
    if not TOKEN or not CHAT: return
    if isinstance(text,list): text="\n".join(text)
    try: s.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT,"text":text[:4000]},timeout=15)
    except Exception as e: print("Telegram error:",e)


def main():
    now=datetime.now(IST); today=now.date()
    print(f"IPO Analyzer FINAL — {now:%d %b %Y %H:%M IST}")
    rows=nse_rows(); ipos=normalize_nse(rows)
    closing=[x for x in ipos if x.get("close_date")==today]
    if not closing:
        telegram(["🏦 IPO ANALYZER",f"Checked: {now:%d %b %Y, %I:%M %p} IST","================================","ℹ️ NO IPO CLOSING TODAY","","No IPO is scheduled to close today.","No IPO analysis is required today.","","Next check: Tomorrow at 2:00 PM IST.","","⚠️ GMP is unofficial and can change rapidly."])
        print("No IPO closing today")
        return
    out=["🏦 IPO ANALYZER",f"Checked: {now:%d %b %Y, %I:%M %p} IST","================================","Official issue/subscription facts: NSE", "Secondary pages only fill missing fields. GMP is unofficial.",""]
    for i,ipo in enumerate(closing,1):
        ipo=enrich(ipo); sc,reasons=score(ipo); ipo["score"]=sc
        issue=ipo.get("price_high") or ipo.get("price_low")
        listing=(issue+ipo["gmp"]) if issue and ipo.get("gmp") is not None else None
        action="⚪ INSUFFICIENT DATA" if sc is None else "🟢 APPLY" if sc>=70 else "🟡 APPLY SELECTIVELY" if sc>=50 else "🔴 AVOID"
        out += [f"{action} #{i} — {ipo['name']}","──────────────────────────────",f"Close      : {ipo.get('close_date') or 'N/A'}",f"Price Band : ₹{ipo['price_low']:.0f}–₹{ipo['price_high']:.0f}" if ipo.get('price_low') is not None and ipo.get('price_high') is not None else "Price Band : N/A",f"Lot Size   : {ipo.get('lot_size') or 'N/A'}",f"Issue Size : ₹{ipo['issue_size']:.2f} Cr" if ipo.get('issue_size') is not None else "Issue Size : N/A",f"QIB        : {ipo['qib']:.2f}x" if ipo.get('qib') is not None else "QIB        : N/A",f"NII/HNI    : {ipo['nii']:.2f}x" if ipo.get('nii') is not None else "NII/HNI    : N/A",f"Retail     : {ipo['retail']:.2f}x" if ipo.get('retail') is not None else "Retail     : N/A",f"Overall    : {ipo['total']:.2f}x" if ipo.get('total') is not None else "Overall    : N/A",f"GMP        : ₹{ipo['gmp']:.0f} (unofficial)" if ipo.get('gmp') is not None else "GMP        : N/A (unofficial)",f"Est Listing: ₹{listing:.0f}" if listing is not None else "Est Listing: N/A",f"Est Gain   : {ipo['gmp_pct']:.1f}%" if ipo.get('gmp_pct') is not None else "Est Gain   : N/A",f"IPO Score  : {sc}/100" if sc is not None else "IPO Score  : N/A","Why:"] + [f"  * {x}" for x in reasons[:6]] + [""]
    out += ["⚠️ GMP is unofficial and can change rapidly.","Listing-gain estimates are not guaranteed.","This is algorithmic screening, not SEBI-registered investment advice."]
    telegram(out); Path(f"ipo_report_{today:%Y-%m-%d}.md").write_text("\n".join(out),encoding="utf-8")

if __name__=="__main__": main()
