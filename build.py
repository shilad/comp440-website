#!/usr/bin/env python3
"""Build the COMP 440 schedule page from schedule.yml.

Derives every meeting date from the calendar block, joins deadlines onto the
grid, and refuses to build if the data has drifted. Writes _site/index.html.

Usage: python3 build.py
"""
import datetime as dt
import html
import sys
from pathlib import Path

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


def join_labels(items: list[dict]) -> str:
    """Speaker-deadline labels as a sentence subject: 'A and b'."""
    names = [html.escape(n[: -len(" due")] if n.endswith(" due") else n)
             for n in (i["label"] for i in items)]
    if not names:
        return "Nothing"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1][0].lower() + names[-1][1:]


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

    def place(date, label, kind, url=None, time=None, links=None):
        row = by_date.get(date)
        if row is None:
            fail(f"{label}: {date} is not a class meeting.")
        elif row["is_break"]:
            fail(f"{label}: {date} falls on {row['topic']}.")
        else:
            row["due"].append({"label": label, "kind": kind, "url": url,
                               "time": time, "links": links})

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

    # A reading creates its own reflection deadline. Declared once here with the
    # citation and the paper's URL; the form URL and the time come from the
    # `reflections` policy block, so they are never repeated per reading.
    refl = data.get("reflections", {})
    for r in data.get("readings", []):
        links = [{"text": "paper", "url": r["url"]}] if r.get("url") else []
        if refl.get("form_url"):
            links.append({"text": "reflection", "url": refl["form_url"]})
        elif r.get("url"):
            fail("readings are set but reflections.form_url is missing.")
        place(r["date"], f'Read {r["cite"]}', "reading",
              time=refl.get("due_time", cal["due_time"]), links=links)

    for m in data.get("milestones", []):
        place(m["date"], m["label"], "project", m.get("url"), time=cal["due_time"])
    for o in data.get("other_due", []):
        place(o["date"], o["label"], "other", o.get("url"), time=cal["due_time"])

    # Speaker deadlines are placed by policy, never entered by hand. A speaker day
    # owes more than the questions, so `items` is a list; the row entries and the
    # footer note are built from that one list and cannot drift apart.
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
        for item in items:
            target["due"].append(
                {"label": item["label"], "kind": "speaker",
                 "time": sq.get("due_time"), "url": item.get("url")}
            )

    if errors:
        return ""
    when_txt = ("on the day of the visit" if when == "visit_day"
                else "the class meeting before the visit")
    t = sq.get("due_time", cal["due_time"])
    same = t == cal["due_time"]
    note = (f"{join_labels(items)} are due {when_txt}." if same else
            f"{join_labels(items)} are due at <b>{html.escape(t)}</b> {when_txt}.")
    note += (" A reading's reflection is due on the day of the class that"
             " discusses it.")
    return render(course, cal, rows, note)


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
        out.append(
            f'<li class="ev-{e(ev.get("kind", "other"))}">'
            f'<span class="when">{e(when)}</span>'
            f'<span class="what">{e(ev["title"])}</span>'
            + (f'<span class="where">{e(loc)}</span>' if loc else "")
            + "</li>"
        )
    if not out:
        return ""

    # Named honestly: the capstone requirement is seminars, and the calendar has
    # none this term. Drops away by itself once a seminar is posted.
    seminars = [x for x in evs if "seminar" in x["title"].lower()]
    gap = "" if seminars else (
        '<p class="gap"><b>No seminar dates are posted yet.</b> The capstone requirement is two '
        "MSCS seminars with a reflection for each; the talks below are the nearest thing on the "
        "department calendar so far. Check back.</p>"
    )
    return (
        '<aside class="rail"><h2>MSCS events</h2>'
        + gap
        + f'<ul class="evs">{"".join(out)}</ul>'
        + f'<p class="asof">From the MSCS Events calendar, as of {e(str(fetched))}.</p>'
        "</aside>"
    )


def course_links(links: list) -> str:
    """The course-level links row: things needed all semester, not tied to a meeting."""
    if not links:
        return ""
    e = html.escape
    items = "".join(
        f'<li><a href="{e(l["url"])}">{e(l["text"])}</a></li>' for l in links
    )
    return f'<ul class="links">{items}</ul>'


def render(course, cal, rows, speaker_note) -> str:
    e = html.escape

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
        return f'<ul class="mat">{"".join(out)}</ul>'

    def due(row):
        if not row["due"]:
            return ""
        items = ""
        for d in row["due"]:
            # The column header states the common time; annotate only exceptions.
            at = ""
            if d.get("time") and d["time"] != cal.get("column_time", cal["due_time"]):
                at = f' <span class="at">{e(d["time"])}</span>'
            lbl = e(d["label"])
            if d.get("url"):
                lbl = f'<a href="{e(d["url"])}">{lbl}</a>'
            # A reading names its own links inline: Read X [paper | reflection]
            if d.get("links"):
                inner = " | ".join(
                    f'<a href="{e(l["url"])}">{e(l["text"])}</a>' for l in d["links"]
                )
                lbl += f' <span class="lnks">[{inner}]</span>'
            items += f'<li class="d-{d["kind"]}">{lbl}{at}</li>'
        return f'<ul class="due">{items}</ul>'

    body, module = [], None
    for row in rows:
        if row["module"] != module:
            module = row["module"]
            body.append(
                f'<tr class="modrow"><th colspan="4" scope="rowgroup">{e(module or "")}</th></tr>'
            )
        d = row["date"]
        anchor = d.strftime("%b-%d").lower()
        cls = "brk" if row["is_break"] else ""
        speaker = ""
        if row["speaker"]:
            who = (f'Guest speaker: {e(row["speaker_name"])}' if row["speaker_name"]
                   else "Guest speaker window")
            speaker = f'<span class="spk">{who}</span>'
        body.append(
            f'<tr id="{anchor}" class="{cls}" data-date="{d.isoformat()}">'
            f'<td class="dt"><a href="#{anchor}">{d.strftime("%a, %b %-d")}</a></td>'
            f'<td class="tp">{e(row["topic"])}{speaker}</td>'
            f'<td>{materials(row)}</td>'
            f'<td>{due(row)}</td>'
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
        speaker_note=speaker_note,
        rows="\n".join(body),
        built=dt.date.today().isoformat(),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {term} Schedule</title>
<style>
:root {{
  --bg:#fff; --fg:#1a1a1a; --muted:#6b6b6b; --line:#e3e3e3;
  --accent:#7c2d12; --now:#fffbeb; --nowline:#f59e0b; --brk:#f7f7f7;
}}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme=light]) {{
  --bg:#16181c; --fg:#e8e8e8; --muted:#9aa0a6; --line:#2c3038;
  --accent:#fca5a5; --now:#2a2410; --nowline:#d97706; --brk:#1c1f24;
}} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:2rem 1.25rem 4rem }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem }}
.sub {{ color:var(--muted); margin:0 0 .5rem }}
ul.links {{ list-style:none; margin:0 0 .9rem; padding:0; display:flex; flex-wrap:wrap; gap:.4rem .9rem }}
ul.links a {{ font-size:.9rem; font-weight:600; color:inherit; text-decoration:none;
  border:1px solid var(--line); border-radius:999px; padding:.2rem .7rem; display:inline-block }}
ul.links a:hover {{ border-color:currentColor }}
.note {{ color:var(--muted); font-size:.9rem; margin:0 0 1.5rem }}
.note b {{ color:var(--fg) }}
table {{ border-collapse:collapse; width:100%; }}
th,td {{ text-align:left; vertical-align:top; padding:.7rem .75rem; border-bottom:1px solid var(--line) }}
thead th {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); border-bottom:2px solid var(--line) }}
.modrow th {{ background:var(--brk); font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); padding:.5rem .75rem; border-bottom:1px solid var(--line) }}
.dt {{ white-space:nowrap; width:8.5rem }}
.dt a {{ color:inherit; text-decoration:none }}
.dt a:hover {{ text-decoration:underline }}
.tp {{ width:30% }}
.spk {{ display:block; font-size:.78rem; color:var(--accent); margin-top:.2rem }}
ul.mat, ul.due {{ margin:0; padding:0; list-style:none; font-size:.9rem }}
ul.mat li, ul.due li {{ margin:0 0 .25rem }}
.tbd {{ color:var(--muted) }}
ul.mat a, ul.due a {{ color:inherit; text-decoration:underline; text-decoration-color:var(--line);
  text-underline-offset:2px }}
ul.mat a:hover, ul.due a:hover {{ text-decoration-color:currentColor }}
.at {{ font-size:.78rem; color:var(--muted); white-space:nowrap }}
.tag {{ font-size:.65rem; border:1px solid var(--line); border-radius:3px; padding:0 .25rem; vertical-align:1px }}
ul.due li::before {{ content:"● "; color:var(--muted) }}
.d-hw::before {{ color:#dc2626 !important }}
.d-project::before {{ color:#2563eb !important }}
.d-speaker::before {{ color:#7c3aed !important }}
.d-reading::before {{ color:#0f766e !important }}
.lnks {{ font-size:.78rem; color:var(--muted); white-space:nowrap }}
tr.brk td {{ background:var(--brk); color:var(--muted) }}
tr.next {{ background:var(--now); box-shadow:inset 3px 0 var(--nowline) }}
.legend {{ margin-top:1.5rem; font-size:.85rem; color:var(--muted) }}
.cols {{ display:flex; gap:2rem; align-items:flex-start }}
.cols main {{ flex:1 1 auto; min-width:0 }}
.rail {{ flex:0 0 15rem; font-size:.85rem; border-left:1px solid var(--line); padding-left:1rem }}
.rail h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:.15rem 0 .6rem; font-weight:600 }}
.rail .gap {{ margin:0 0 .8rem; color:var(--muted); line-height:1.45 }}
.rail .gap b {{ color:var(--fg) }}
ul.evs {{ list-style:none; margin:0; padding:0 }}
ul.evs li {{ margin:0 0 .7rem; padding-left:.6rem; border-left:2px solid var(--line) }}
ul.evs .when {{ display:block; color:var(--muted); font-size:.78rem }}
ul.evs .what {{ display:block }}
ul.evs .where {{ display:block; color:var(--muted); font-size:.78rem }}
li.ev-talk {{ border-left-color:#0f766e }}
li.ev-talk .what {{ font-weight:600 }}
li.ev-cancelled {{ opacity:.55 }}
li.ev-cancelled .what {{ text-decoration:line-through }}
.rail .asof {{ margin:1rem 0 0; color:var(--muted); font-size:.78rem }}
@media (max-width:900px) {{
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
  table,tbody,tr,td {{ display:block; width:auto }}
  tr:not(.modrow) {{ border-bottom:1px solid var(--line); padding:.6rem 0 }}
  td {{ border:0; padding:.15rem .5rem }}
  .dt {{ font-weight:600 }}
  .tp {{ width:auto }}
}}
</style></head><body><div class="wrap">
<h1>{title}</h1>
<p class="sub">{term} · {meets}</p>
{links}
<p class="note">Everything is due at <b>{due_time}</b> on the date shown. {speaker_note}</p>
<div class="cols">
<main>
<table>
<thead><tr><th>Date</th><th>Topic</th><th>Class materials</th><th>Due ({due_col})</th></tr></thead>
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
