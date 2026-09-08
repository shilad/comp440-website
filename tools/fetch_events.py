#!/usr/bin/env python3
"""Fetch the MSCS Events calendar and write events.yml.

Standard library only. Run from the repo root:

    python3 tools/fetch_events.py            # write events.yml, print a diff
    python3 tools/fetch_events.py --dry-run  # print the diff, write nothing

Three things in this feed are easy to get wrong, and each one is handled here
rather than left to the caller:

1. DTSTART comes in three forms -- UTC (`...Z`), `TZID=America/Chicago`, and
   `VALUE=DATE` all-day. Rendering a UTC stamp as if it were local puts a 4:40pm
   talk at 10:40pm. Everything is converted to America/Chicago; all-day events
   are kept without a time rather than given a fake one.
2. Recurring events (the weekly coffee breaks) exist only as an RRULE and must be
   expanded, honouring UNTIL, COUNT, INTERVAL, BYDAY, EXDATE, and RECURRENCE-ID
   overrides that move or cancel a single instance.
3. Events that are dropped are recorded under `skipped:` with a reason, not
   silently discarded, so a later pass does not have to re-derive the decision.

The term bounds come from schedule.yml, never typed here.
"""
import argparse
import datetime as dt
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent.parent
ICS_URL = (
    "https://calendar.google.com/calendar/ical/"
    "macalester.edu_k7f54d0aeqnpvqb3kjl5kvnuvg%40group.calendar.google.com/public/basic.ics"
)
LOCAL = ZoneInfo("America/Chicago")
# How far outside the term to keep recording skips. Beyond this the feed spans
# years and listing every event would bury the ones that matter.
NEAR_DAYS = 45

WEEKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def unfold(text: str) -> str:
    """RFC 5545 line folding: a leading space continues the previous line."""
    return text.replace("\r\n", "\n").replace("\n ", "").replace("\n\t", "")


def prop(block: str, name: str):
    """Return (params, value) for a property, or None."""
    m = re.search(rf"^{name}([^:\n]*):(.*)$", block, re.M)
    return (m.group(1), m.group(2).strip()) if m else None


def parse_dt(params: str, value: str):
    """-> (datetime|date, is_all_day). Aware datetimes are converted to LOCAL."""
    if "VALUE=DATE" in params:
        return dt.datetime.strptime(value, "%Y%m%d").date(), True
    if value.endswith("Z"):
        utc = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        return utc.astimezone(LOCAL), False
    tz = re.search(r"TZID=([^;:]+)", params)
    naive = dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=ZoneInfo(tz.group(1)) if tz else LOCAL).astimezone(LOCAL), False


def as_date(x):
    return x if isinstance(x, dt.date) and not isinstance(x, dt.datetime) else x.date()


def parse_rrule(value: str) -> dict:
    out = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def expand(start, rrule: dict, window_end: dt.date, limit: int = 400):
    """Yield occurrence start values. Handles FREQ=DAILY|WEEKLY, the only two in
    this feed; anything else yields the single start so nothing is lost silently."""
    freq = rrule.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY"):
        yield start
        return
    interval = int(rrule.get("INTERVAL", 1))
    count = int(rrule["COUNT"]) if "COUNT" in rrule else None
    until = None
    if "UNTIL" in rrule:
        u = rrule["UNTIL"]
        until = dt.datetime.strptime(u[:8], "%Y%m%d").date()

    days = [WEEKDAY[d] for d in rrule["BYDAY"].split(",")] if "BYDAY" in rrule else None
    cur, n = start, 0
    hard_stop = window_end + dt.timedelta(days=1)
    while n < limit:
        d = as_date(cur)
        if d > hard_stop or (until and d > until):
            return
        if days is None or d.weekday() in days:
            yield cur
            n += 1
            if count and n >= count:
                return
        step = interval if freq == "DAILY" else (1 if days else 7 * interval)
        cur = cur + dt.timedelta(days=step)
        # Weekly with BYDAY steps a day at a time within the week, then jumps.
        if freq == "WEEKLY" and days and as_date(cur).weekday() == start_weekday(start) and interval > 1:
            cur = cur + dt.timedelta(days=7 * (interval - 1))


def start_weekday(start):
    return as_date(start).weekday()


COFFEE_HOME = "Smail Gallery"
# The feed leaves coffee-break locations blank or set to the organiser's own
# placeholder. Both mean "the usual place", which is Smail Gallery (instructor,
# Sep 8). A real location in the feed always wins -- e.g. the Sep 17 outdoor
# edition, which is genuinely somewhere else.
PLACEHOLDER = re.compile(r"^\s*(confirm location|tbd|tba|location tbd)\s*$", re.I)


def resolve_location(title: str, location: str) -> str:
    # A cancellation has no venue: "No Coffee Break — Smail Gallery" reads as if
    # something is still happening there.
    if classify(title) == "cancelled":
        return ""
    if location and not PLACEHOLDER.match(location):
        return location
    if "coffee break" in title.lower():
        return COFFEE_HOME
    return ""


def classify(title: str) -> str:
    t = title.lower()
    if t.startswith("no ") or "cancel" in t:
        return "cancelled"
    # `talk` is the capstone-relevant class and the rail emphasises it, so keep it
    # to things a student could plausibly reflect on. A programming contest is not
    # a talk; it lands in career alongside the other participation events.
    if re.search(r"seminar|colloqui|capstone|speaker|\btalk\b|lecture|defense", t):
        return "talk"
    if re.search(r"intern|career|interview|info session|resume|job|preceptor|training|contest", t):
        return "career"
    if re.search(r"coffee|social|party|game night|ice cream|reception|break", t):
        return "social"
    return "other"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "comp440-website/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def term_bounds() -> tuple:
    """Read the term from schedule.yml rather than typing dates here."""
    text = (HERE / "schedule.yml").read_text()
    first = re.search(r"^\s*first:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    last = re.search(r"^\s*last:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    if not (first and last):
        sys.exit("Could not read calendar.first/last from schedule.yml")
    return (dt.date.fromisoformat(first.group(1)), dt.date.fromisoformat(last.group(1)))


def collect(raw: str, t0: dt.date, t1: dt.date):
    """-> (shown, skipped, far_count)."""
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfold(raw), re.S)
    masters, overrides = [], {}
    for b in blocks:
        ds = prop(b, "DTSTART")
        if not ds:
            continue
        start, all_day = parse_dt(*ds)
        uid = (prop(b, "UID") or ("", ""))[1]
        rec = prop(b, "RECURRENCE-ID")
        item = {
            "uid": uid,
            "start": start,
            "all_day": all_day,
            "summary": (prop(b, "SUMMARY") or ("", ""))[1],
            "location": (prop(b, "LOCATION") or ("", ""))[1],
            "rrule": parse_rrule((prop(b, "RRULE") or ("", ""))[1]) if prop(b, "RRULE") else None,
            "status": (prop(b, "STATUS") or ("", ""))[1],
            "exdates": set(),
        }
        for line in re.findall(r"^EXDATE([^:\n]*):(.*)$", b, re.M):
            for v in line[1].split(","):
                try:
                    item["exdates"].add(as_date(parse_dt(line[0], v.strip())[0]))
                except ValueError:
                    pass
        if rec:
            overrides.setdefault(uid, {})[as_date(parse_dt(*rec)[0])] = item
        else:
            masters.append(item)

    shown, skipped, far = [], [], 0
    near0, near1 = t0 - dt.timedelta(days=NEAR_DAYS), t1 + dt.timedelta(days=NEAR_DAYS)

    def record(start, item, origin):
        d = as_date(start)
        row = {
            "date": d,
            "time": None if item["all_day"] else start.strftime("%-I:%M%p").lower(),
            "title": item["summary"] or "(untitled)",
            "location": resolve_location(item["summary"], item["location"]),
            "kind": classify(item["summary"]),
            "origin": origin,
        }
        if item["status"].upper() == "CANCELLED":
            skipped.append({**row, "reason": "cancelled in the calendar"})
        elif t0 <= d <= t1:
            shown.append(row)
        elif near0 <= d <= near1:
            skipped.append({**row, "reason": "outside the term"})
        else:
            nonlocal far
            far += 1

    for m in masters:
        ov = overrides.get(m["uid"], {})
        if m["rrule"]:
            for occ in expand(m["start"], m["rrule"], t1):
                d = as_date(occ)
                if d in m["exdates"]:
                    if t0 <= d <= t1:
                        skipped.append({
                            "date": d, "time": None, "title": m["summary"], "location": "",
                            "kind": "cancelled", "origin": "recurring",
                            "reason": "EXDATE — this instance was cancelled",
                        })
                    continue
                if d in ov:
                    continue  # the override carries the real details
                record(occ, m, "recurring")
            for d, o in ov.items():
                record(o["start"], o, "override")
        else:
            record(m["start"], m, "single")

    shown.sort(key=lambda r: (r["date"], r["time"] or ""))
    skipped.sort(key=lambda r: (r["date"], r["title"]))
    return shown, skipped, far


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(shown, skipped, far, t0, t1) -> str:
    L = [
        "# GENERATED by tools/fetch_events.py — do not hand-edit; a refresh overwrites this.",
        "# Source: the public MSCS Events calendar. Times are America/Chicago, converted from",
        "# the feed's mix of UTC, TZID and all-day stamps. Recurring events are expanded here,",
        "# so each line below is one real occurrence.",
        f"source: {q(ICS_URL)}",
        f"fetched: {dt.date.today().isoformat()}",
        f"term: [{t0.isoformat()}, {t1.isoformat()}]",
        "",
        "# Shown on the site, in the MSCS events rail.",
        "events:",
    ]
    for r in shown:
        bits = [f"date: {r['date'].isoformat()}"]
        if r["time"]:
            bits.append(f"time: {q(r['time'])}")
        bits.append(f"title: {q(r['title'])}")
        if r["location"]:
            bits.append(f"location: {q(r['location'])}")
        bits.append(f"kind: {r['kind']}")
        L.append("  - { " + ", ".join(bits) + " }")
    if not shown:
        L.append("  []")
    L += [
        "",
        "# NOT shown. Recorded with a reason so a later pass does not have to work out",
        "# again why each one was dropped. Nothing here reaches the site.",
        "skipped:",
    ]
    for r in skipped:
        L.append(
            "  - { "
            + f"date: {r['date'].isoformat()}, title: {q(r['title'])}, "
            + f"reason: {q(r['reason'])}"
            + " }"
        )
    if not skipped:
        L.append("  []")
    L += [
        "",
        f"# {far} further events in the feed fall more than {NEAR_DAYS} days outside the term",
        "# and are not listed individually.",
        "",
    ]
    return "\n".join(L)


def diff(old: str, new: str) -> None:
    def rows(t):
        return {ln.strip() for ln in t.splitlines() if ln.strip().startswith("- {")}
    a, b = rows(old), rows(new)
    added, gone = sorted(b - a), sorted(a - b)
    if not added and not gone:
        print("  no change")
        return
    for r in added:
        print("  + " + r[:120])
    for r in gone:
        print("  - " + r[:120])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    args = ap.parse_args()

    t0, t1 = term_bounds()
    shown, skipped, far = collect(fetch(ICS_URL), t0, t1)
    out = render(shown, skipped, far, t0, t1)

    target = HERE / "events.yml"
    old = target.read_text() if target.exists() else ""
    print(f"MSCS events {t0}..{t1}: {len(shown)} shown, {len(skipped)} skipped, {far} far out of term")
    diff(old, out)
    if args.dry_run:
        print("  (dry run — events.yml not written)")
        return
    target.write_text(out)
    print(f"  wrote {target.relative_to(HERE)}")


if __name__ == "__main__":
    main()
