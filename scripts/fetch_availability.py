#!/usr/bin/env python3
"""Snapshot Asia Miles award seat availability for every Cathay nonstop route.

Writes data/avail.json consumed by the static web app:

  {
    "generated": "...", "updateTime": "...", "hub": "HKG",
    "dates": ["20261102", ...],            # contiguous 180-day axis
    "cabins": {"eco": {"LHR": "----HHL..."}, ...},   # outbound HUB -> dest
    "return": {"eco": {"LHR": "-----..."}, ...},     # inbound dest -> HUB
    "miles":  {"LHR": {"eco": 27000, ...}, ...}      # standard award miles
  }

Per-day characters: "H" high availability, "L" low availability,
"-" no availability, "." not returned (route/dates not offered).

Usage: python3 scripts/fetch_availability.py [--concurrency 6] [--days 180]
"""
import argparse
import concurrent.futures
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def hk_now():
    """Current date in Hong Kong — GitHub runners are UTC, the calendar is HKT."""
    return datetime.datetime.now(HK_TZ).date()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Referer": "https://www.cathaypacific.com/cx/en_HK.html",
           "Origin": "https://www.cathaypacific.com"}
CAL = ("https://api.cathaypacific.com/afr/search/availability/en.{o}.{d}."
       "{cab}.CX.1.{s}.{e}.json")
MILES = ("https://api.cathaypacific.com/afr/searchpanel/searchoptions/en.{o}.{d}."
         "ow.std.CX.json")
CABINS = ["eco", "pey", "bus", "fir"]
CODE_CHAR = {"H": "H", "L": "L", "NA": "-"}
RETRIES = 3


def get_json(url, timeout=45):
    """Fetch JSON, backing off hard on Akamai 403/429 (= short IP block)."""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:                       # noqa: PERF203
            last = e
            if e.code in (403, 429, 503):
                time.sleep(30 * (attempt + 1))    # a burst gets the IP blocked
            else:
                time.sleep(1.5 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def availability(origin, dest, cabin, start, end):
    """Return {yyyymmdd: 'H'|'L'|'-'} or {} when the route is not offered."""
    data = get_json(CAL.format(o=origin, d=dest, cab=cabin, s=start, e=end))
    rows = data.get("availabilities", {}).get("std", [])
    return {r["date"]: CODE_CHAR.get(r["availability"], "-") for r in rows}


def miles(origin, dest):
    try:
        data = get_json(MILES.format(o=origin, d=dest), timeout=30)
        m = data.get("milesRequired") or {}
        return {k: m.get(k) for k in CABINS}
    except Exception:  # noqa: BLE001
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--out", default="data/avail.json")
    args = ap.parse_args()

    dests = json.load(open("data/destinations.json", encoding="utf-8"))
    hub = dests["hub"]
    codes = [d["code"] for d in dests["destinations"]]

    today = hk_now()
    start = today.strftime("%Y%m%d")
    axis = [(today + datetime.timedelta(days=i)).strftime("%Y%m%d")
            for i in range(args.days)]
    end = axis[-1]

    jobs = []
    for code in codes:
        for cab in CABINS:
            jobs.append(("out", code, cab))
            jobs.append(("in", code, cab))
    print(f"{len(codes)} destinations, {len(jobs)} availability requests "
          f"({start}..{end})")

    grid = {c: {} for c in CABINS}          # outbound: dest -> string
    rgrid = {c: {} for c in CABINS}         # inbound
    empty = {c: set() for c in CABINS}
    update_time = ""
    failures = 0

    def work(job):
        direction, code, cab = job
        o, d = (hub, code) if direction == "out" else (code, hub)
        try:
            rows = availability(o, d, cab, start, end)
        except Exception as e:  # noqa: BLE001
            return job, None, str(e)[:120]
        return job, rows, None

    padded = {c: {} for c in CABINS}
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as ex:
        for i, (job, rows, err) in enumerate(ex.map(work, jobs), 1):
            direction, code, cab = job
            if rows is None:
                failures += 1
                failures_note = (code, cab, direction, err)
                print("  ! fail", failures_note)
                rows = {}
            s = "".join(rows.get(dt, ".") for dt in axis)
            if rows and not update_time:
                pass
            (grid if direction == "out" else rgrid)[cab][code] = s
            if i % 60 == 0:
                print(f"  {i}/{len(jobs)}")

    # miles required (one call per OD, all cabins at once)
    print("fetching miles required...")
    miles_map = {}
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as ex:
        for code, m in zip(codes, ex.map(lambda c: miles(hub, c), codes)):
            if m:
                miles_map[code] = m

    # freshness stamp from one sample call
    try:
        sample = get_json(CAL.format(o=hub, d=codes[0], cab="bus", s=start, e=end))
        update_time = sample.get("availabilities", {}).get("updateTime", "")
    except Exception:  # noqa: BLE001
        pass

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updateTime": update_time,
        "hub": hub,
        "hubCity": "Hong Kong",
        "dates": axis,
        "destinations": [{"code": d["code"], "city": d["city"],
                          "cityZh": d.get("cityZh", d["city"]),
                          "country": d.get("country", ""),
                          "countryZh": d.get("countryZh", d.get("country", "")),
                          "continent": d.get("continent", "其他"),
                          "continentEn": d.get("continentEn", "Other")}
                         for d in dests["destinations"]],
        "cabins": grid,
        "return": rgrid,
        "miles": miles_map,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    n_avail = sum(1 for c in CABINS for s in grid[c].values() if "H" in s or "L" in s)
    print(f"wrote {args.out}: {len(codes)} dests, {n_avail} dest/cabin pairs with "
          f"availability, {failures} request failures, updateTime={update_time}")
    if failures > len(jobs) * 0.3:
        print("too many failures, exiting 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
