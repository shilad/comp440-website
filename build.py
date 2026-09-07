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


def build() -> str:
    data = yaml.safe_load((HERE / "schedule.yml").read_text())
    course, cal = data["course"], data["calendar"]
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
                "materials": entry.get("materials", []) or [],
                "due": [],
            }
        )
    by_date = {r["date"]: r for r in rows}

    def place(date, label, kind, url=None):
        row = by_date.get(date)
        if row is None:
            fail(f"{label}: {date} is not a class meeting.")
        elif row["is_break"]:
            fail(f"{label}: {date} falls on {row['topic']}.")
        else:
            row["due"].append({"label": label, "kind": kind, "url": url})

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
            place(a["due"], f"{a['id'].upper()} due", "hw", a.get("url"))

    for m in data.get("milestones", []):
        place(m["date"], m["label"], "project", m.get("url"))
    for o in data.get("other_due", []):
        place(o["date"], o["label"], "other", o.get("url"))

    # Speaker questions are placed by policy, never entered by hand.
    sq = data.get("speaker_questions", {})
    when = sq.get("when", "visit_day")
    if when not in ("visit_day", "prior_meeting"):
        fail(f"speaker_questions.when: {when!r} (use visit_day or prior_meeting).")
        return ""
    teaching = [r for r in rows if not r["is_break"]]
    for i, row in enumerate(teaching):
        if not row["speaker"]:
            continue
        if when == "prior_meeting" and i == 0:
            fail(f"Speaker window on {row['date']} has no prior meeting.")
            continue
        target = row if when == "visit_day" else teaching[i - 1]
        target["due"].append(
            {"label": "Speaker questions due", "kind": "speaker",
             "time": sq.get("due_time")}
        )

    if errors:
        return ""
    when_txt = ("on the day of the visit" if when == "visit_day"
                else "the class meeting before the visit")
    t = sq.get("due_time", cal["due_time"])
    note = f"Speaker questions are due at <b>{html.escape(t)}</b> {when_txt}."
    return render(course, cal, rows, note)


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
            at = ""
            if d.get("time") and d["time"] != cal["due_time"]:
                at = f' <span class="at">{e(d["time"])}</span>'
            lbl = e(d["label"])
            if d.get("url"):
                lbl = f'<a href="{e(d["url"])}">{lbl}</a>'
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
        speaker = '<span class="spk">Guest speaker window</span>' if row["speaker"] else ""
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
.sub {{ color:var(--muted); margin:0 0 .35rem }}
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
tr.brk td {{ background:var(--brk); color:var(--muted) }}
tr.next {{ background:var(--now); box-shadow:inset 3px 0 var(--nowline) }}
.legend {{ margin-top:1.5rem; font-size:.85rem; color:var(--muted) }}
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
<p class="note">Everything below is due at <b>{due_time}</b> on the date shown.
{speaker_note}</p>
<table>
<thead><tr><th>Date</th><th>Topic</th><th>Class materials</th><th>Due</th></tr></thead>
<tbody>
{rows}
</tbody></table>
<p class="legend">Last updated {built}. Items marked TBD are not yet finalized.</p>
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
