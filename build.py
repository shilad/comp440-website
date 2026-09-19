#!/usr/bin/env python3
"""Build the COMP 440 schedule page from schedule.yml.

Derives every meeting date from the calendar block, joins deadlines onto the
grid, and refuses to build if the data has drifted. Writes _site/index.html.

Usage: python3 build.py
"""
import datetime as dt
import html
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import yaml

HERE = Path(__file__).parent
DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def derive_dates(cal: dict) -> list[dt.date]:
    """Walk the term, keeping only the configured meeting days."""
    want = {DAYS[d] for d in cal["days"]}
    out, day = [], cal["first"]
    while day <= cal["last"]:
        if day.weekday() in want:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def build() -> str:
    data = yaml.safe_load((HERE / "schedule.yml").read_text())
    course, cal = data["course"], data["calendar"]
    course["links"] = data.get("links") or []
    grid = derive_dates(cal)
    entries = data["meetings"]

    if len(entries) != len(grid):
        fail(
            f"{len(entries)} entries in `meetings` but the calendar derives "
            f"{len(grid)} slots ({cal['first']}..{cal['last']}, "
            f"{'/'.join(cal['days'])}). Add or remove an entry."
        )
        return ""

    # Zip entries onto the derived grid; carry module names down.
    rows, module = [], None
    for date, entry in zip(grid, entries):
        module = entry.get("module", module)
        rows.append(
            {
                "date": date,
                "module": module,
                "topic": entry.get("break") or entry.get("topic", ""),
                "is_break": "break" in entry,
                "speaker": entry.get("speaker", False),
                "speaker_name": (entry["speaker"]
                                 if isinstance(entry.get("speaker"), str) else None),
                "materials": entry.get("materials", []) or [],
                "due": [],
            }
        )
    by_date = {r["date"]: r for r in rows}

    def place(date, label, kind, url=None, time=None, links=None, note=None):
        row = by_date.get(date)
        if row is None:
            fail(f"{label}: {date} is not a class meeting.")
        elif row["is_break"]:
            fail(f"{label}: {date} falls on {row['topic']}.")
        else:
            row["due"].append({"label": label, "kind": kind, "url": url,
                               "time": time, "links": links, "note": note})

    for a in data.get("assignments", []):
        if a.get("launch"):
            row = by_date.get(a["launch"])
            if row is None or row["is_break"]:
                fail(f"{a['id']} launch: {a['launch']} is not a class meeting.")
            else:
                row["materials"] = list(row["materials"]) + [
                    {"text": f"Launch: {a['title']}", "url": a.get("url")}
                ]
        if a.get("due"):
            place(a["due"], f"{a['id'].upper()} due", "hw", a.get("url"),
                  time=cal["due_time"])

    # One form takes every kind of submission and branches on its first question.
    # Prefilling that question, and the one that follows it, saves two picks and
    # removes the chance of filing a reflection against the wrong reading.
    #
    # Every part is declared once in `form_prefill` and composed here; a row never
    # writes a prefilled URL. If the block is absent, or a value is missing, this
    # returns None and the caller falls back to the plain form link -- so the
    # feature can be removed by deleting the block, and a stale option string
    # costs a prefill, never a wrong answer.
    pf = data.get("form_prefill") or {}

    def prefill(kind_value, field, value):
        if not (pf.get("responder") and pf.get("kind_field") and field
                and kind_value and value):
            return None
        q = urlencode({"usp": "pp_url", pf["kind_field"]: kind_value,
                       field: value})
        return f'{pf["responder"]}?{q}'

    # A reading creates its own reflection deadline. Declared once here with the
    # citation and the paper's URL; the form URL and the time come from the
    # `reflections` policy block, so they are never repeated per reading.
    refl = data.get("reflections", {})
    for r in data.get("readings", []):
        # `label` names the link when "paper" would be wrong — a news article, a
        # blog post, a video. Optional; academic papers just leave it off.
        #
        # `sources` is for a reading assembled from several pieces — a speaker's
        # site plus a filing plus a news story, say. It stays ONE reading with one
        # reflection and one 8:00am; four separate `readings` entries would render
        # four reflection links and imply four submissions. Each entry is
        # {text, url}. `url`/`label` still work alone for the ordinary one-paper
        # case, and the two can be combined.
        links = []
        if r.get("url"):
            links.append({"text": r.get("label", "paper"), "url": r["url"]})
        for s in r.get("sources", []):
            if not (s.get("text") and s.get("url")):
                fail(f'{r["date"]}: every reading source needs text and url.')
            links.append({"text": s["text"], "url": s["url"]})
        # The submit link is the action, the rest are things to open. `act` marks it
        # so the renderer can style it apart; on a speaker day two obligations point
        # at this same form and both should look like the same kind of thing.
        if refl.get("form_url"):
            links.append({"text": "Submit reflection",
                          "url": prefill(pf.get("reading_kind"),
                                         pf.get("reading_field"),
                                         r.get("form_option")) or refl["form_url"],
                          "act": True})
        elif links:
            fail("readings are set but reflections.form_url is missing.")
        # `note` is an instruction that belongs to this reading and nowhere else —
        # how to approach a hard source, an exception to a standing rule. Policy
        # that applies to every reading belongs in `reflections`, not here.
        place(r["date"], f'Read {r["cite"]}', "reading",
              time=refl.get("due_time", cal["due_time"]), links=links,
              note=r.get("note"))

    for m in data.get("milestones", []):
        place(m["date"], m["label"], "project", m.get("url"), time=cal["due_time"])
    for o in data.get("other_due", []):
        place(o["date"], o["label"], "other", o.get("url"), time=cal["due_time"])

    # Speaker deadlines are placed by policy, never entered by hand. A speaker day
    # owes more than the questions, so `items` is a list.
    sq = data.get("speaker_due") or data.get("speaker_questions") or {}
    when = sq.get("when", "visit_day")
    if when not in ("visit_day", "prior_meeting"):
        fail(f"speaker_due.when: {when!r} (use visit_day or prior_meeting).")
        return ""
    items = sq.get("items", [{"label": "Speaker questions due"}])
    if not isinstance(items, list) or any(
        not isinstance(i, dict) or not i.get("label") for i in items
    ):
        fail("speaker_due.items must be a list of {label, url?} mappings.")
        return ""
    teaching = [r for r in rows if not r["is_break"]]
    for i, row in enumerate(teaching):
        if not row["speaker"]:
            continue
        if when == "prior_meeting" and i == 0:
            fail(f"Speaker window on {row['date']} has no prior meeting.")
            continue
        target = row if when == "visit_day" else teaching[i - 1]
        # "(tentative)" qualifies the date for readers of this page; the form's
        # speaker list holds the bare name, so it is stripped for the prefill only.
        who = re.sub(r"\s*\(tentative\)\s*$", "", row["speaker_name"] or "")
        for item in items:
            target["due"].append(
                {"label": item["label"], "kind": "speaker",
                 "time": sq.get("due_time"),
                 "url": (prefill(pf.get("speaker_kind"), pf.get("speaker_field"), who)
                         or item.get("url"))}
            )

    if errors:
        return ""
    # The note paragraph that used to sit above the table is gone (instructor,
    # Sep 14). Everything it said is still enforced and still visible where it
    # is acted on: the due time is in the Due column heading, a speaker row
    # carries its own "Speaker questions due" entry, and a reading's reflection
    # is placed on the class that discusses it. The paragraph restated all three
    # before anyone had a row in front of them, which is the wrong moment.
    return render(course, cal, rows)


def events_rail(t0: dt.date, t1: dt.date) -> str:
    """The MSCS events rail. Generated data from events.yml, never hand-edited.

    Rail dates are DISPLAYED, not validated against the meeting grid: these are
    department events on Wed/Sat/Sun, not course deadlines, and the grid check
    exists to protect deadlines.
    """
    path = HERE / "events.yml"
    if not path.exists():
        return ""
    data = yaml.safe_load(path.read_text()) or {}
    evs = data.get("events") or []
    fetched = data.get("fetched")
    if not fetched:
        fail("events.yml has no `fetched:` date — regenerate with tools/fetch_events.py.")
        return ""
    age = (dt.date.today() - fetched).days
    if age > 30:
        fail(f"events.yml is {age} days old. Refresh it (tools/fetch_events.py) before publishing.")
        return ""

    credit = yaml.safe_load((HERE / "events_overrides.yml").read_text()) or {}

    # Hand-added events. The other three override keys all MODIFY an event the
    # feed produced; this one adds an event the feed does not carry at all,
    # because not everything students should see is on the MSCS calendar. They
    # are merged in here, before the matching below, so a hand-added event can
    # carry a poster or seminar credit exactly like a generated one.
    #
    # Two guards, both because a hand-written event is the one kind the feed
    # cannot correct later: an entry that duplicates something already on the
    # calendar is a build failure (say so with `hidden:` or an override instead
    # of two rows for one event), and the rail footer stops claiming everything
    # came from the MSCS calendar once anything here is in it.
    extras = credit.get("extra") or []
    for x in extras:
        for field in ("date", "title", "kind"):
            if not x.get(field):
                fail(f"events_overrides extra: an entry is missing `{field}:`.")
        if x.get("url") and x.get("poster"):
            fail(f"events_overrides extra: {x.get('title')!r} has both `url:` and a "
                 "poster. The title links to one thing; pick which.")
        dupes = [e_ for e_ in evs
                 if e_["date"] == x["date"]
                 and (x["title"].lower() in e_["title"].lower()
                      or e_["title"].lower() in x["title"].lower())]
        if dupes:
            fail(f"events_overrides extra: {x['date']} {x['title']!r} looks like "
                 f"{dupes[0]['title']!r}, which the calendar already carries. Hand-adding "
                 "it would put the same event on the rail twice — use `hidden:` or an "
                 "override on the generated one instead.")
    evs = sorted(evs + [dict(x) for x in extras],
                 key=lambda e_: (e_["date"], _minutes(e_.get("time"))))

    # Seminar credit is curated by hand in events_overrides.yml and matched on here,
    # because events.yml is regenerated and would lose the flag. A miss is a build
    # failure: an event that moved or was retitled must not silently stop counting.
    for want in credit.get("counts") or []:
        hits = [e for e in evs
                if e["date"] == want["date"]
                and want["title"].lower() in e["title"].lower()]
        if len(hits) != 1:
            fail(f"events_overrides counts: {want['date']} {want['title']!r} matched "
                 f"{len(hits)} events, expected exactly 1. The event may have moved, "
                 "been retitled, or dropped off the calendar.")
        for h in hits:
            h["counts"] = True

    # Event posters, curated the same way and matched the same way. The poster
    # itself lives in the MSCS department's shared drive, so what is recorded here
    # is the Drive id -- the /view URL is built once, below, rather than pasted in
    # a form that could be an editor path.
    #
    # This build cannot check that a poster is still shared: it never touches the
    # network, by design. Sharing is confirmed by hand before an entry is added and
    # the date of that check is recorded on the entry, because these are other
    # people's uploads in a department folder and a re-upload silently drops a
    # per-file sharing exception.
    for want in credit.get("posters") or []:
        hits = [e for e in evs
                if e["date"] == want["date"]
                and want["title"].lower() in e["title"].lower()]
        if len(hits) != 1:
            fail(f"events_overrides posters: {want['date']} {want['title']!r} matched "
                 f"{len(hits)} events, expected exactly 1. The event may have moved, "
                 "been retitled, or dropped off the calendar.")
        if not want.get("checked"):
            fail(f"events_overrides posters: {want['date']} {want['title']!r} has no "
                 "`checked:` date. Confirm the file is shared to anyone with the link "
                 "before linking it from a public page, then record when you looked.")
        for h in hits:
            h["poster"] = f"https://drive.google.com/file/d/{want['drive_id']}/view"

    # A date must not carry both an event and its own cancellation notice.
    by_date = {}
    for e in evs:
        by_date.setdefault(e["date"], []).append(e)
    for d, group in by_date.items():
        if any(g.get("kind") == "cancelled" for g in group) and len(group) > 1:
            titles = [g["title"] for g in group]
            if any(t.lower().lstrip().startswith("no ") for t in titles) and len(titles) > 1:
                base = [t for t in titles if not t.lower().lstrip().startswith("no ")]
                if any("coffee" in t.lower() for t in base) and any(
                    "coffee" in t.lower() for t in titles if t.lower().lstrip().startswith("no ")
                ):
                    fail(f"{d}: both a coffee break and a 'No Coffee Break' notice. "
                         "The feed's override was not applied — check fetch_events.py.")

    e = html.escape
    out = []
    for ev in evs:
        d = ev["date"]
        when = d.strftime("%a %b ") + str(d.day)
        if ev.get("time"):
            when += f" · {e(ev['time'])}"
        loc = ev.get("location") or ""
        if len(loc) > 40:
            loc = loc[:38].rstrip() + "…"
        cls = f'ev-{e(ev.get("kind", "other"))}' + (" counts" if ev.get("counts") else "")
        badge = ('<span class="cred" title="Counts toward the seminar requirement"'
                 ' aria-label="Counts toward the seminar requirement">✓</span> '
                 if ev.get("counts") else "")
        # A poster links the title itself rather than adding a fourth element to
        # the row. The rail was trimmed twice to stay minimal, and the one thing
        # colour is allowed to mean here is seminar credit -- so the link inherits
        # whatever colour its row already has and is marked by an underline alone.
        # The ✓ stays outside the link: it is a status, not part of the name.
        what = e(ev["title"])
        link = ev.get("poster") or ev.get("url")
        if link:
            title = "Event poster" if ev.get("poster") else "Event details"
            what = f'<a href="{e(link)}" title="{title}">{what}</a>' 
        out.append(
            f'<li class="{cls}" data-date="{d.isoformat()}">'
            f'<span class="when">{e(when)}</span>'
            + f'<span class="what">{badge}{what}</span>'
            + (f'<span class="where">{e(loc)}</span>' if loc else "")
            + "</li>"
        )
    if not out:
        return ""

    # Students will not assume an event counts (instructor, Sep 9), so credit is
    # stated positively on the events that have it and never left to inference.
    # Two things a student would otherwise get wrong: that any talk counts, and
    # that COMP 440's own guest speakers do.
    gap = (
        '<p class="gap">You are required to attend and write a reflection for 2 events below '
        "marked ✓. More will be added as they are scheduled.</p>"
    )
    return (
        '<aside class="rail"><h2>MSCS events</h2>'
        + gap
        + f'<ul class="evs">{"".join(out)}</ul>'
        + f'<p class="asof">From the MSCS Events calendar, as of {e(str(fetched))}'
        + (", plus events added by hand." if extras else ".")
        + "</p></aside>"
    )


def _minutes(t) -> int:
    """Sort key for a display time like "4:40pm". All-day events sort first."""
    if not t:
        return -1
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])m", str(t).strip(), re.I)
    if not m:
        return -1
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    h = h % 12 + (12 if ap == "p" else 0)
    return h * 60 + mi


def course_links(links: list) -> str:
    """The course-level links row: things needed all semester, not tied to a meeting."""
    if not links:
        return ""
    e = html.escape
    items = "".join(
        f'<li><a href="{e(l["url"])}">{e(l["text"])}</a></li>' for l in links
    )
    return f'<ul class="links">{items}</ul>'


def render(course, cal, rows) -> str:
    e = html.escape

    # Materials sit inside the Class column, under the topic, rather than in a
    # column of their own: the split students kept misreading was two adjacent
    # lists of links. "What happens in class" against "what you owe before it"
    # is a difference of place, which needs no explaining.
    def materials(row):
        if not row["materials"]:
            return ""
        out = []
        for m in row["materials"]:
            text = e(str(m.get("text", "")))
            if m.get("tbd"):
                out.append(f'<li class="tbd">{text} <span class="tag">TBD</span></li>')
            elif m.get("url"):
                out.append(f'<li><a href="{e(m["url"])}">{text}</a></li>')
            else:
                out.append(f"<li>{text}</li>")
        caption = '<span class="sec">Materials</span>'
        return f'{caption}<ul class="mat">{"".join(out)}</ul>'

    # Every obligation says what kind it is, in words. The bullets this replaces
    # were coloured per kind with nothing on the page saying what a colour meant.
    # `other` is named too, so it is not the one unlabelled item in the column and
    # does not read as optional.
    KIND = {"reading": "Reading", "hw": "Homework", "project": "Project",
            "speaker": "Speaker", "other": "Form"}

    def due(row):
        if not row["due"]:
            return ""
        items = ""
        for d in row["due"]:
            # The column header states the common time; annotate only exceptions.
            at = ""
            if d.get("time") and d["time"] != cal.get("column_time", cal["due_time"]):
                at = f' <span class="at">{e(d["time"])}</span>'
            kind = d["kind"]
            lbl = e(d["label"])
            if d.get("url"):
                lbl = f'<a href="{e(d["url"])}">{lbl}</a>'
            out = f'<span class="kind">{e(KIND.get(kind, kind))}</span>' \
                  f'<span class="what">{lbl}{at}</span>'
            # A reading's sources are things to open; the submit link is the thing
            # to do. Both are chips so neither hides inside a run of prose, and the
            # submit one carries the accent. They wrap: the old bracketed list was
            # `white-space:nowrap`, and one five-link row set the minimum width of
            # this whole column.
            chips = list(d.get("links") or [])
            # A speaker day's questions go to the same form as the reflection, as a
            # separate submission. Same shape, so they read as two of one thing.
            if kind == "speaker" and d.get("url"):
                out = f'<span class="kind">{e(KIND[kind])}</span>' \
                      f'<span class="what">{e(d["label"])}{at}</span>'
                chips = [{"text": "Submit questions", "url": d["url"], "act": True}]
            if chips:
                inner = "".join(
                    f'<a class="chip{" act" if l.get("act") else ""}" '
                    f'href="{e(l["url"])}">{e(l["text"])}</a>' for l in chips
                )
                out += f'<span class="srcs">{inner}</span>'
            # An instruction attached to one reading renders under it, so it is
            # read with the thing it applies to rather than as general policy.
            if d.get("note"):
                out += f'<span class="note">{e(d["note"])}</span>'
            items += f'<li class="d-{kind}">{out}</li>'
        return f'<ul class="due">{items}</ul>'

    body, module = [], None
    for row in rows:
        if row["module"] != module:
            module = row["module"]
            body.append(
                f'<tr class="modrow"><th colspan="3" scope="rowgroup">{e(module or "")}</th></tr>'
            )
        d = row["date"]
        anchor = d.strftime("%b-%d").lower()
        date_cell = (f'<td class="dt"><a href="#{anchor}">'
                     f'{d.strftime("%a, %b %-d")}</a></td>')
        # A break owes nothing and holds nothing, so it spans rather than carrying
        # an empty Due cell that reads as a deadline someone forgot to fill in.
        if row["is_break"]:
            body.append(
                f'<tr id="{anchor}" class="brk" data-date="{d.isoformat()}">'
                f'{date_cell}<td class="tp" colspan="2">{e(row["topic"])}</td></tr>'
            )
            continue
        speaker = ""
        if row["speaker"]:
            who = (f'Guest speaker: {e(row["speaker_name"])}' if row["speaker_name"]
                   else "Guest speaker window")
            speaker = f'<span class="spk">{who}</span>'
        body.append(
            f'<tr id="{anchor}" class="" data-date="{d.isoformat()}">'
            f'{date_cell}'
            f'<td class="tp">{e(row["topic"])}{speaker}{materials(row)}</td>'
            f'<td class="du" data-label="Due {e(cal.get("column_time", cal["due_time"]))}">'
            f'{due(row)}</td>'
            f"</tr>"
        )

    return TEMPLATE.format(
        title=e(f'{course["code"]}: {course["title"]}'),
        term=e(course["term"]),
        meets=e(course["meets"]),
        due_time=e(cal["due_time"]),
        due_col=e(cal.get("column_time", cal["due_time"])),
        links=course_links(course.get("links") or []),
        events=events_rail(cal["first"], cal["last"]),
        rows="\n".join(body),
        built=dt.date.today().isoformat(),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {term} Schedule</title>
<style>
/* `--line` is a hairline between rows and is deliberately faint (1.28:1 on the
   page background). It must NOT carry a link underline or a chip's border: those
   identify a control, which WCAG 1.4.11 puts at 3:1. `--ctl` is that second
   token, measured at 3.03:1 light and 3.07:1 dark. */
:root {{
  color-scheme:light dark;
  --bg:#fff; --fg:#1a1a1a; --muted:#6b6b6b; --line:#e3e3e3; --ctl:#8f8f8f;
  --accent:#7c2d12; --now:#fffbeb; --nowline:#b45309; --brk:#f7f7f7; --talk:#0f766e; --counts:#2563eb;
}}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme=light]) {{
  --bg:#16181c; --fg:#e8e8e8; --muted:#9aa0a6; --line:#2c3038; --ctl:#676e7a;
  --accent:#fca5a5; --now:#2a2410; --nowline:#d97706; --brk:#1c1f24; --talk:#5eead4; --counts:#93c5fd;
}} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:2rem 1.25rem 4rem }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem }}
.sub {{ color:var(--muted); margin:0 0 .5rem }}
ul.links {{ list-style:none; margin:0 0 .9rem; padding:0; display:flex; flex-wrap:wrap; gap:.4rem .9rem }}
ul.links a {{ font-size:.9rem; color:inherit; text-decoration:none;
  border:1px solid var(--line); border-radius:999px; padding:.2rem .7rem; display:inline-block }}
ul.links a:hover {{ border-color:currentColor }}
/* Fixed layout, so the Due column's width is a decision rather than a result. In
   auto layout the widest unbreakable run in any cell sets the column, which is how
   one five-link reading came to squeeze every other column on the page. */
table {{ border-collapse:collapse; width:100%; table-layout:fixed }}
th,td {{ text-align:left; vertical-align:top; padding:.7rem .75rem; border-bottom:1px solid var(--line) }}
thead th {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
  position:sticky; top:0; z-index:4; background:var(--bg); padding-top:.5rem; padding-bottom:.4rem;
  box-shadow:inset 0 -2px 0 var(--line) }}
.modrow th {{ background:var(--brk); font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); padding:.5rem .75rem; border-bottom:1px solid var(--line) }}
.dt {{ white-space:nowrap; width:8.5rem }}
.dt a {{ color:inherit; text-decoration:none }}
.dt a:hover {{ text-decoration:underline }}
.tp {{ width:36% }}
.spk {{ display:block; font-size:.78rem; color:var(--accent); margin-top:.2rem }}
/* Nothing on this page is smaller than 12px. The sizes are 16 / 14.4 / 13.6 / 12,
   and every caption, tag and kind label shares the bottom one. */
.sec {{ display:block; font-size:.75rem; text-transform:uppercase;
  letter-spacing:.06em; color:var(--muted); margin:.6rem 0 .15rem }}
ul.mat, ul.due {{ margin:0; padding:0; list-style:none }}
ul.mat {{ font-size:.85rem }}
ul.mat li {{ margin:0 0 .2rem }}
ul.due {{ font-size:.9rem }}
ul.due li {{ margin:0 0 .55rem }}
ul.due li:last-child {{ margin-bottom:0 }}
.tbd {{ color:var(--muted) }}
ul.mat a, ul.due a {{ color:inherit; text-decoration:underline; text-decoration-color:var(--ctl);
  text-underline-offset:2px }}
ul.mat a:hover, ul.due a:hover {{ text-decoration-color:currentColor }}
.at {{ font-size:.78rem; color:var(--muted); white-space:nowrap }}
.tag {{ font-size:.75rem; border:1px solid var(--ctl); border-radius:3px; padding:0 .25rem; vertical-align:1px }}
/* The kind is said in words, so colour is not carrying it. One muted weight for
   all four: four hues would add colour to a page already called cluttered, and
   two of them collided with the blue the events rail uses for seminar credit. */
.kind {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin-right:.4rem; vertical-align:1px }}
.what {{ display:inline }}
.srcs {{ display:flex; flex-wrap:wrap; gap:.3rem .35rem; margin:.3rem 0 0 }}
ul.due a.chip {{ font-size:.8rem; line-height:1.3; text-decoration:none; color:inherit;
  border:1px solid var(--ctl); border-radius:999px; padding:.15rem .6rem }}
ul.due a.chip:hover {{ border-color:currentColor }}
ul.due a.chip.act {{ border-color:var(--accent); color:var(--accent) }}
/* This carries rule exceptions -- the Sep 29 reading suspends the no-AI rule for
   one document -- so it is not allowed to be the least legible text in its row. */
.note {{ display:block; font-size:.85rem; line-height:1.45; color:var(--muted);
  margin:.3rem 0 0; max-width:42em }}
tr.brk td {{ background:var(--brk); color:var(--muted) }}
tr.next {{ background:var(--now); box-shadow:inset 3px 0 var(--nowline) }}
.legend {{ margin-top:1.5rem; font-size:.85rem; color:var(--muted) }}
.cols {{ display:flex; gap:2rem; align-items:flex-start }}
.cols main {{ flex:1 1 auto; min-width:0 }}
.rail {{ flex:0 0 15rem; font-size:.85rem; border-left:1px solid var(--line); padding-left:1rem }}
/* Pinned alongside the table's column headers, so the top of both columns stays
   put as one band rather than the schedule keeping its headings and the rail
   losing its own. The bottom margin becomes padding: sticky pins the border box,
   and a margin below it is a gap the background does not paint, which events
   would scroll through. Same 2px inset rule as `thead th`, for the same reason --
   without it the pinned heading and the events under it run together. */
.rail h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:.15rem 0 0; font-weight:600;
  position:sticky; top:0; z-index:4; background:var(--bg);
  padding:.5rem 0 .6rem; box-shadow:inset 0 -2px 0 var(--line) }}
.rail .gap {{ margin:0 0 .8rem; color:var(--muted); line-height:1.45 }}
.rail .gap b {{ color:var(--fg) }}
ul.evs {{ list-style:none; margin:0; padding:0 }}
ul.evs li {{ margin:0 0 .7rem }}
/* When JS has measured the table, events are lifted out of flow and parked beside
   the meeting they fall near. Without JS this class is never added and the list
   above renders as an ordinary stack. */
ul.evs.aligned {{ position:relative }}
ul.evs.aligned li {{ position:absolute; left:0; right:0; margin:0;
  transition:top .15s ease-out }}
@media (prefers-reduced-motion:reduce) {{ ul.evs.aligned li {{ transition:none }} }}
ul.evs .when {{ display:block; color:var(--muted); font-size:.78rem }}
ul.evs .what {{ display:block }}
ul.evs .where {{ display:block; color:var(--muted); font-size:.78rem }}
/* Talks are the capstone-relevant kind, so they carry the emphasis now that the
   per-event rule is gone. */
/* Colour means exactly one thing in this rail: this event counts toward the
   seminar requirement. Talks that do NOT count are styled like anything else —
   emphasising them too would make the blue ambiguous. */
ul.evs li.counts .what {{ font-weight:600; color:var(--counts) }}
.cred {{ color:var(--counts); font-weight:700 }}
/* A poster link inherits its row's colour rather than taking the link accent,
   so it cannot be mistaken for the blue that means seminar credit. The dotted
   underline is the whole affordance. */
ul.evs .what a {{ color:inherit; text-decoration:underline dotted;
                  text-underline-offset:2px }}
ul.evs .what a:hover, ul.evs .what a:focus {{ text-decoration:underline solid }}
li.ev-cancelled {{ opacity:.55 }}
li.ev-cancelled .what {{ text-decoration:line-through }}
.rail .asof {{ margin:1rem 0 0; color:var(--muted); font-size:.78rem }}
/* 1024, not 900: the rail costs the table 272px, and on a laptop that came out of
   the Due column, which is the one that has to hold a reading's chips and note. */
@media (max-width:1024px) {{
  .cols {{ display:block }}
  .rail {{ border-left:0; border-top:1px solid var(--line); padding:1rem 0 0; margin-top:1.5rem }}
}}
#today {{
  position:fixed; right:1.1rem; bottom:1.1rem; z-index:9;
  display:none; align-items:center; gap:.35rem;
  padding:.45rem .8rem; border-radius:999px;
  background:var(--bg); color:var(--muted);
  border:1px solid var(--line); font-size:.82rem; cursor:pointer;
  opacity:.55; transition:opacity .15s, color .15s, border-color .15s;
  box-shadow:0 1px 4px rgba(0,0,0,.07);
}}
#today.on {{ display:inline-flex }}
#today:hover, #today:focus-visible {{ opacity:1; color:var(--fg); border-color:var(--nowline) }}
#today .arrow {{ color:var(--nowline) }}
@media print {{ #today {{ display:none !important }} }}
@media (max-width:720px) {{
  thead {{ display:none }}
  table,tbody,tr,td,th {{ display:block; width:auto }}
  tr:not(.modrow) {{ border-bottom:1px solid var(--line); padding:.6rem 0 }}
  td {{ border:0; padding:.15rem .5rem }}
  .dt {{ font-weight:600; width:auto }}
  .tp {{ width:auto }}
  /* The column headings are gone at this width, so the Due block names itself.
     Materials already carry their own caption. */
  td.du::before {{ content:attr(data-label); display:block; font-size:.75rem;
    text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:.6rem 0 .15rem }}
  td.du:empty {{ display:none }}
  /* Reading on a phone, not squinting: nothing shrinks just because the screen
     did, and the chips grow to a 26px touch target. */
  ul.mat {{ font-size:.9rem }}
  ul.due a.chip {{ font-size:.85rem; padding:.2rem .7rem }}
}}
</style></head><body><div class="wrap">
<h1>{title}</h1>
<p class="sub">{term} · {meets}</p>
{links}
<div class="cols">
<main>
<table>
<colgroup><col style="width:8.5rem"><col style="width:36%"><col></colgroup>
<thead><tr><th scope="col">Date</th><th scope="col">Class</th><th scope="col">Due ({due_col})</th></tr></thead>
<tbody>
{rows}
</tbody></table>
<p class="legend">Last updated {built}. Items marked TBD are not yet finalized.</p>
</main>
{events}
</div>
</div>
<button id="today" type="button" hidden><span class="arrow">&#8595;</span> Today</button>
<script>
// Find the current/next meeting client-side, so the page stays correct without a rebuild.
(function () {{
  var today = new Date(); today.setHours(0, 0, 0, 0);
  var rows = document.querySelectorAll("tr[data-date]");
  var target = null;
  for (var i = 0; i < rows.length; i++) {{
    var p = rows[i].dataset.date.split("-");
    if (new Date(+p[0], +p[1] - 1, +p[2]) >= today) {{ target = rows[i]; break; }}
  }}
  if (!target) return;                       // term is over: no highlight, no button
  target.classList.add("next");

  var btn = document.getElementById("today");
  var label = target.querySelector(".dt a");
  var when = label ? label.textContent.trim() : "today";
  btn.title = "Jump to " + when;
  btn.setAttribute("aria-label", "Jump to " + when);
  btn.hidden = false;

  // Open on the current/next meeting rather than at the top of the term
  // (instructor, Sep 14). By October the interesting row is several screens
  // down and every visit started with the same scroll.
  //
  // Three things it must not fight, in order of how annoying each would be:
  // a deep link (#sep-22 means someone asked for that row), the browser's own
  // scroll restoration on reload or Back, and a target already on screen --
  // scrolling a visible row to centre is movement for nothing.
  var restored = false;
  try {{
    var nav = performance.getEntriesByType("navigation")[0];
    restored = nav && nav.type === "back_forward";
  }} catch (e) {{}}
  if (!location.hash && !restored && window.scrollY === 0 && !inView(target)) {{
    // Instant, not smooth: this happens before the reader has looked at
    // anything, so animating it only delays the page they asked for.
    target.scrollIntoView({{ block: "center" }});
  }}

  function inView(el) {{
    var r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= (window.innerHeight || 0);
  }}
  function sync() {{ btn.classList.toggle("on", !inView(target)); }}
  btn.addEventListener("click", function () {{
    target.scrollIntoView({{ behavior: "smooth", block: "center" }});
    history.replaceState(null, "", "#" + target.id);
  }});
  addEventListener("scroll", sync, {{ passive: true }});
  addEventListener("resize", sync);
  sync();
}})();

// Park each MSCS event beside the class meeting it falls near, so the rail reads as a
// timeline parallel to the schedule rather than a list beside it. Row heights are not
// knowable at build time, so this measures them. If it cannot (no JS, narrow screen),
// the rail stays a plain stacked list and nothing is lost.
(function () {{
  var list = document.querySelector("ul.evs");
  if (!list) return;
  var items = [].slice.call(list.children);
  var rows = [].slice.call(document.querySelectorAll("tr[data-date]"));
  if (!items.length || rows.length < 2) return;

  function day(s) {{ var p = s.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }}
  var GAP = 10;

  function place() {{
    // Below the breakpoint the rail sits under the table; leave it alone.
    if (window.matchMedia("(max-width: 1024px)").matches) {{
      list.classList.remove("aligned");
      list.style.height = "";
      items.forEach(function (li) {{ li.style.top = ""; }});
      return;
    }}
    var base = list.getBoundingClientRect().top + window.scrollY;
    var marks = rows.map(function (r) {{
      return {{ t: day(r.dataset.date),
               y: r.getBoundingClientRect().top + window.scrollY - base }};
    }});

    // Ideal position: interpolate between the two meetings that bracket the event,
    // so a Wednesday event lands between Tuesday's row and Thursday's.
    var want = items.map(function (li) {{
      var t = day(li.dataset.date), i;
      if (t <= marks[0].t) return {{ li: li, y: marks[0].y }};
      for (i = 0; i < marks.length - 1; i++) {{
        if (t <= marks[i + 1].t) {{
          var span = marks[i + 1].t - marks[i].t || 1;
          var f = (t - marks[i].t) / span;
          return {{ li: li, y: marks[i].y + (marks[i + 1].y - marks[i].y) * f }};
        }}
      }}
      return {{ li: li, y: marks[marks.length - 1].y }};
    }});

    // Events cluster (four on Sep 24), so push overlaps down rather than stacking them
    // on top of each other. Order is already chronological.
    list.classList.add("aligned");
    var bottom = 0;
    want.forEach(function (w) {{
      var y = Math.max(w.y, bottom ? bottom + GAP : 0);
      w.li.style.top = y + "px";
      bottom = y + w.li.offsetHeight;
    }});
    list.style.height = bottom + "px";
  }}

  var pending;
  function relayout() {{ clearTimeout(pending); pending = setTimeout(place, 80); }}
  place();
  addEventListener("resize", relayout);
  // Fonts landing late change row heights, so measure again once they have.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(place);
}})();
</script>
</body></html>
"""

if __name__ == "__main__":
    out = build()
    if errors:
        print("Schedule did not build:\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    out_dir = HERE / "_site"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "index.html").write_text(out)
    print(f"Built {out_dir / 'index.html'}")

    # Not a failure -- just a running count of how much is still unlinked, so
    # filling in materials over the term is visible work rather than a guess.
    linked = tbd = plain = 0
    for m in yaml.safe_load((HERE / "schedule.yml").read_text())["meetings"]:
        for mat in m.get("materials") or []:
            if mat.get("url"):
                linked += 1
            elif mat.get("tbd"):
                tbd += 1
            else:
                plain += 1
    print(f"  materials: {linked} linked, {tbd} TBD, {plain} unlinked")
