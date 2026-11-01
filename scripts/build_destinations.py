#!/usr/bin/env python3
"""Build data/destinations.json = Cathay Pacific nonstop destinations from a hub.

Source: Wikipedia "List of Cathay Pacific destinations" (wikitext, cached in
data/_wiki_cx.json). Airport codes are resolved against Cathay's own OD list
(api.cathaypacific.com/ibe-od/v3.0) by airport name first, then city name.

Each candidate is then validated against the award calendar: if the route has
no calendar data at all (terminated / not sold as an award), it is dropped.

Usage: python3 scripts/build_destinations.py [--hub HKG] [--no-validate]
"""
import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CX_HEADERS = {"User-Agent": UA,
              "Referer": "https://www.cathaypacific.com/cx/en_HK.html"}
OD_URL = "https://api.cathaypacific.com/ibe-od/v3.0/{loc}/{hub}"
CAL_URL = ("https://api.cathaypacific.com/afr/search/availability/en.{o}.{d}."
           "{cab}.CX.1.{s}.{e}.json")


def norm(s):
    s = s.lower().replace("international", " ").replace("airport", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_json(url, timeout=40):
    req = urllib.request.Request(url, headers=CX_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- wikitext
def parse_wikitext(text):
    body = text.split("==List==")[1].split("\n|}")[0]

    def link(s):
        m = re.match(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", s.strip())
        return (m.group(2) or m.group(1)).strip() if m else s.strip()

    def clean(s):
        s = re.sub(r"<ref[^>]*/>", "", s)
        s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
        s = re.sub(r"\{\{[^}]*\}\}", "", s)
        s = re.sub(r"align=center\|?", "", s)
        s = re.sub(r'style="[^"]*"\|', "", s)
        return s.replace("''", "").replace("&nbsp;", " ").strip().strip("|").strip()

    rows = []
    for raw in re.split(r"\n\|-\s*\n?", body):
        raw = raw.strip()
        if "||" not in raw:
            continue
        if raw.startswith("|"):
            raw = raw[1:]
        if raw.startswith("!"):
            continue
        cells = []
        for c in raw.split("||"):
            c = clean(c)
            m = re.match(r'rowspan="(\d+)"\|(.*)$', c)
            cells.append((int(m.group(1)), m.group(2)) if m else (1, c))
        rows.append(cells)

    # expand rowspan into a uniform 4-column table (country|city|airport|notes)
    fills = {}
    out = []
    for cells in rows:
        ptr = 0
        cols = []
        for col in range(4):
            if fills.get(col, (None, 0))[1] > 0:
                cols.append(fills[col][0])
                fills[col] = (fills[col][0], fills[col][1] - 1)
                continue
            while ptr < len(cells) and cells[ptr][1] == "":
                ptr += 1
            if ptr >= len(cells):
                cols.append("")
                continue
            span, text = cells[ptr]
            ptr += 1
            cols.append(text)
            if span > 1:
                fills[col] = (text, span - 1)
        country, city, airport, notes = cols
        if not city or not airport or "airport" not in airport.lower():
            continue
        out.append({"country": link(country), "city": link(city),
                    "airport": link(airport), "notes": notes})
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="HKG")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    wiki = json.load(open("data/_wiki_cx.json", encoding="utf-8"))
    rows = parse_wikitext(wiki["parse"]["wikitext"])
    print(f"wiki rows: {len(rows)}")

    od = fetch_json(OD_URL.format(loc="en_HK", hub=args.hub))["airports"]
    ports = [{"code": a["airportCode"],
              "city": a["airportDetails"]["city"]["defaultName"],
              "country": a["airportDetails"]["country"]["defaultName"],
              "region": a.get("regionName", ""),
              "full": a["airportDetails"]["airportFullName"]} for a in od]

    # Chinese names + region labels come from Cathay's own zh_HK locale
    zhm = {}
    try:
        for a in fetch_json(OD_URL.format(loc="zh_HK", hub=args.hub))["airports"]:
            det = a["airportDetails"]
            zhm[a["airportCode"]] = {
                "cityZh": det["city"]["name"],
                "countryZh": det["country"]["name"],
                "fullZh": det["airportFullName"],
                "regionZh": a.get("regionName", ""),
            }
    except Exception as e:  # noqa: BLE001
        print("zh_HK locale fetch failed:", e)

    CONTINENTS = {
        "asia": ("亞洲", "Asia"), "middle east": ("亞洲", "Asia"),
        "europe": ("歐洲", "Europe"), "africa": ("非洲", "Africa"),
        "americas": ("美洲", "Americas"),
        "australasia": ("大洋洲", "Oceania"), "oceania": ("大洋洲", "Oceania"),
    }

    def continent(region):
        r = (region or "").strip().lower().replace("_", " ").replace("-", " ")
        r = " ".join(r.split())
        if r.startswith("china") or r.startswith("middle east"):
            return ("亞洲", "Asia")
        return CONTINENTS.get(r, (region or "其他", region or "Other"))

    by_airport = {}
    by_city = {}
    for p in ports:
        by_airport.setdefault(norm(p["full"]), []).append(p)
        by_city.setdefault(norm(p["city"]), []).append(p)

    def tokens(s):
        return set(norm(s).split())

    def score(port, airport, city):
        at, ft, ct = tokens(airport), tokens(port["full"]), tokens(port["city"])
        if norm(airport) == norm(port["full"]):
            return 100
        if at and at <= ft:
            return 60 + len(at)
        if ft and ft <= at:
            return 50 + len(ft)
        if at & ft:
            return 30 + len(at & ft)
        if norm(city) == norm(port["city"]):
            return 20
        return 0

    dests, unresolved = [], []
    for r in rows:
        if "cargo" in r["notes"].lower() and "passenger" not in r["notes"].lower():
            continue  # cargo-only station
        if norm(r["city"]) == norm("Hong Kong"):
            continue  # hub itself
        scored = [(score(p, r["airport"], r["city"]), p) for p in ports]
        best = max((s for s in scored), key=lambda t: t[0], default=(0, None))
        if best[0] <= 0:
            unresolved.append(r)
            continue
        p = best[1]
        code = p["code"].rstrip("+")  # BKK+ / SHA+ style port codes
        z = zhm.get(p["code"], zhm.get(code, {}))
        cont, cont_en = continent(p.get("region") or z.get("regionZh", ""))
        dests.append({
            "code": code,
            "city": p["city"], "cityZh": z.get("cityZh", p["city"]),
            "country": p.get("country") or r["country"],
            "countryZh": z.get("countryZh", r["country"]),
            "continent": cont, "continentEn": cont_en,
            "airport": p["full"], "airportZh": z.get("fullZh", p["full"]),
            "notes": r["notes"],
        })

    # de-dupe by code
    seen, uniq = set(), []
    for d in dests:
        if d["code"] in seen:
            continue
        seen.add(d["code"])
        uniq.append(d)
    dests = uniq
    print(f"resolved: {len(dests)}  unresolved: {len(unresolved)}")
    for u in unresolved:
        print("   unresolved:", u)

    if not args.no_validate:
        today = time.strftime("%Y%m%d")
        end = time.strftime("%Y%m%d", time.localtime(time.time() + 180 * 86400))

        def has_data(d):
            url = CAL_URL.format(o=args.hub, d=d["code"], cab="bus",
                                 s=today, e=end)
            try:
                data = fetch_json(url)
                rows_ = data.get("availabilities", {}).get("std", [])
                return d, len(rows_) > 0
            except Exception as e:  # noqa: BLE001
                return d, None

        keep, unknown = [], []
        with concurrent.futures.ThreadPoolExecutor(6) as ex:
            for d, ok in ex.map(has_data, dests):
                if ok:
                    keep.append(d)
                elif ok is None:
                    unknown.append(d)
                time.sleep(0.05)
        print(f"validated live: {len(keep)} kept, "
              f"{len(dests) - len(keep) - len(unknown)} dropped (no calendar data), "
              f"{len(unknown)} unknown")
        for d in dests:
            if d not in keep and d not in unknown:
                print("   dropped:", d["code"], d["city"])
        dests = keep + unknown

    dests.sort(key=lambda d: (d["continentEn"], d["country"], d["city"]))
    payload = {"hub": args.hub, "count": len(dests), "destinations": dests}
    with open("data/destinations.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("wrote data/destinations.json:", len(dests), "destinations")
    print(", ".join(d["code"] for d in dests))


if __name__ == "__main__":
    sys.exit(main())
