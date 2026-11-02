#!/usr/bin/env python3
"""
Build data/partners.json — which Asia Miles partner airlines can be redeemed
on each HKG nonstop destination, plus the miles each one charges.

The award-calendar endpoint (/afr/search/availability) is CX-only: passing a
partner code there returns an empty calendar even on routes the partner flies.
But /afr/searchpanel/searchoptions accepts a partner airlineCode and answers
with that carrier's Asia Miles award chart — and it errors (ERR_AFR_001) for
carriers that do not operate the OD. That makes it a cheap "who serves this
route, and what does it cost" probe.

Pace this gently: the API sits behind Akamai and a burst of concurrent calls
gets the whole IP 403'd for a minute or two.

Usage: python3 scripts/build_partners.py [--delay 1.1] [--out data/partners.json]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = ("https://api.cathaypacific.com/afr/searchpanel/searchoptions/"
       "en.HKG.{dest}.ow.std.{carrier}.json")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "Referer": "https://www.cathaypacific.com/cx/en_HK.html",
    "Accept": "application/json",
}

# Asia Miles airline partners that could plausibly serve HKG nonstop.
# Anything the API rejects simply drops out of the output.
CANDIDATES = ["CX", "BA", "QF", "JL", "AY", "QR", "MH", "AA",
              "CA", "ZH", "NZ", "LA", "GA", "PG", "AS"]

CARRIER_NAMES = {
    "CX": ("Cathay Pacific", "國泰航空"),
    "BA": ("British Airways", "英國航空"),
    "QF": ("Qantas", "澳洲航空"),
    "JL": ("Japan Airlines", "日本航空"),
    "AY": ("Finnair", "芬蘭航空"),
    "QR": ("Qatar Airways", "卡塔爾航空"),
    "MH": ("Malaysia Airlines", "馬來西亞航空"),
    "AA": ("American Airlines", "美國航空"),
    "AS": ("Alaska Airlines", "阿拉斯加航空"),
    "RJ": ("Royal Jordanian", "皇家約旦航空"),
    "UL": ("SriLankan Airlines", "斯里蘭卡航空"),
    "AT": ("Royal Air Maroc", "摩洛哥皇家航空"),
    "IB": ("Iberia", "伊比利亞航空"),
    "WY": ("Oman Air", "阿曼航空"),
    "CA": ("Air China", "中國國際航空"),
    "ZH": ("Shenzhen Airlines", "深圳航空"),
    "LA": ("LATAM Airlines", "智利航空"),
    "NZ": ("Air New Zealand", "新西蘭航空"),
    "PG": ("Bangkok Airways", "曼谷航空"),
    "GA": ("Garuda Indonesia", "嘉魯達印尼航空"),
}


def fetch(dest, carrier, timeout=25, retries=2):
    url = API.format(dest=dest, carrier=carrier)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            miles = data.get("milesRequired")
            return miles, None
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                time.sleep(20 + attempt * 20)
                continue
            # "this carrier sells no award on this OD" arrives as HTTP 500 + ERR_AFR_001,
            # which is an answer, not a fault — do not report it as a failure
            try:
                body = json.loads(e.read().decode("utf-8", "replace"))
                codes = [x.get("code") for x in body.get("errors", [])]
            except Exception:                                     # noqa: BLE001
                codes = []
            if "ERR_AFR_001" in codes:
                return None, "nochart"
            return None, f"HTTP{e.code}"
        except Exception as e:                                    # noqa: BLE001
            if attempt == retries:
                return None, type(e).__name__
            time.sleep(2)
    return None, "blocked"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.1, help="seconds between calls")
    ap.add_argument("--out", default="data/partners.json")
    ap.add_argument("--destinations", default="data/destinations.json")
    ap.add_argument("--cache", default="data/_partners_cache.json",
                    help="resumable per-destination results")
    args = ap.parse_args()

    with open(args.destinations, encoding="utf-8") as f:
        dests = [d["code"] for d in json.load(f)["destinations"]]

    # resume support: a run that trips the Akamai block must not restart from zero
    cache = {}
    if os.path.exists(args.cache):
        with open(args.cache, encoding="utf-8") as f:
            cache = json.load(f)
    by_dest = {k: v for k, v in cache.items() if v}
    nochart = 0
    failures = 0
    total = len(dests) * len(CANDIDATES)
    n = 0
    consecutive_blocked = 0
    for dest in dests:
        if dest in cache:                       # already probed in an earlier run
            n += len(CANDIDATES)
            continue
        row = {}
        for carrier in CANDIDATES:
            n += 1
            miles, err = fetch(dest, carrier)
            if miles:
                consecutive_blocked = 0
                if any(v for v in miles.values()):
                    row[carrier] = miles
            elif err == "nochart":
                nochart += 1
            elif err:
                failures += 1
                consecutive_blocked = consecutive_blocked + 1 if err == "blocked" else 0
            if consecutive_blocked >= 20:
                print("  blocked by the API — checkpointing and stopping", flush=True)
                cache[dest] = row
                with open(args.cache, "w", encoding="utf-8") as f:
                    json.dump(cache, f)
                return 2
            if n % 40 == 0:
                print(f"  {n}/{total}  dests={len(by_dest)} failures={failures}", flush=True)
            time.sleep(args.delay)
        cache[dest] = row
        if row:
            by_dest[dest] = row
        with open(args.cache, "w", encoding="utf-8") as f:
            json.dump(cache, f)

    used = sorted({c for v in by_dest.values() for c in v})
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hub": "HKG",
        "candidates": CANDIDATES,
        "carriers": {c: {"name": CARRIER_NAMES.get(c, (c, c))[0],
                         "nameZh": CARRIER_NAMES.get(c, (c, c))[1],
                         "destinations": sum(1 for v in by_dest.values() if c in v)}
                     for c in used},
        "byDest": by_dest,
        "failures": failures,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    out["noChart"] = nochart
    print(f"wrote {args.out}: {len(by_dest)} destinations, carriers={used}, "
          f"no-chart answers={nochart}, real failures={failures}")
    for c in used:
        print(f"  {c}: {out['carriers'][c]['destinations']} dests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
