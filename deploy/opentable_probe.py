#!/usr/bin/env python3
"""OpenTable diagnostic — run ON THE VM (where the watcher runs), not locally.

    ~/sevenrooms-notifier/.venv/bin/python deploy/opentable_probe.py <slug> [YYYY-MM-DD] [party]
    e.g. ...python deploy/opentable_probe.py roast-reservations-london 2026-07-11 2

Answers the two questions that decide whether OpenTable can work at all:
  1. Is the VM's IP being bot-blocked? (status code + block-page markers)
  2. Is availability actually in the HTML the scraper reads, or loaded later by
     JavaScript? (raw-HTML marker search + what the current scraper would find)
"""
import sys, re
from curl_cffi import requests as c

slug  = sys.argv[1] if len(sys.argv) > 1 else "roast-reservations-london"
date  = sys.argv[2] if len(sys.argv) > 2 else "2026-07-11"
party = sys.argv[3] if len(sys.argv) > 3 else "2"
url = f"https://www.opentable.co.uk/r/{slug}?covers={party}&dateTime={date}T19:00:00"

print("GET", url)
try:
    r = c.get(url, impersonate="chrome120", timeout=30,
              headers={"Accept-Language": "en-GB,en;q=0.9"})
except Exception as e:
    print("REQUEST FAILED:", type(e).__name__, str(e)[:200]); sys.exit(1)

html, low = r.text, r.text.lower()
print(f"HTTP {r.status_code}   {len(html)} bytes")
print("final URL       :", getattr(r, "url", "?"))
print("content-type    :", r.headers.get("content-type"))
print("server          :", r.headers.get("server"))
_redir = re.search(r'http-equiv=["\']refresh|window\.location|location\.href|location\.replace', html, re.I)
if _redir:
    print("client redirect :", _redir.group(0), "(page bounces via JS/meta)")

blockers = [b for b in ["access denied", "pardon our interruption", "captcha", "datadome",
            "perimeterx", "px-captcha", "unusual traffic", "are you a human",
            "request unsuccessful", "cf-chl", "verify you are human"] if b in low]
print("bot-block markers :", blockers or "none  (good — not blocked)")
print("raw HTML has 'isAvailable' :", "isavailable" in low)
print("raw HTML has '\"time\"'      :", '"time"' in low)

blocks, times = re.findall(r'\{[^{}]*\}', html), []
for b in blocks:
    bc = b.replace(" ", "").replace('\\"', '"')
    if '"isAvailable":true' in bc and '"time":' in bc:
        m = re.search(r'"time"\s*:\s*"([^"]+)"', b)
        if m: times.append(m.group(1))
print("times the scraper finds :", sorted(set(times)) or "NONE")

apis = sorted(set(re.findall(r'/dapi/[A-Za-z0-9/_-]+', html)))[:12]
print("OpenTable API paths seen:", apis or "none in HTML (availability is fetched client-side)")

if len(html) < 8000:
    print("\n----- RAW BODY (response was small — showing it in full) -----")
    print(html)
    print("----- END RAW BODY -----")

print("\nVerdict:")
if blockers:
    print("  -> IP appears BOT-BLOCKED. Scraping won't work from this host.")
elif not times:
    print("  -> Not blocked, but availability is NOT in the HTML (JS-rendered).")
    print("     The regex scraper can't see it; needs a real browser or the API.")
else:
    print("  -> Works: availability is in the HTML and the scraper reads it.")
