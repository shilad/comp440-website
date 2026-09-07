# COMP 440 — Collective Intelligence

Source for the public course schedule: **https://shilad.github.io/comp440-website/**

## Changing the schedule

Edit **`schedule.yml`**. That is the only file you need to touch. Push to `main` and the site
rebuilds and redeploys in about a minute.

You never type a meeting date. `calendar` declares the meeting pattern and the term bounds, and
the build assigns dates to the `meetings` list in order:

```yaml
calendar:
  days: [Tue, Thu]
  first: 2026-09-08
  last:  2026-12-10
  due_time: 10:00am Central
```

- **Cancel a class** — replace its entry with `- break: Reason`. Everything after keeps its date.
- **Insert a session** — add an entry and push `last` out by one meeting.
- **Reorder topics** — move entries around; dates follow their position automatically.
- **Move a deadline** — change `due:` on the assignment. It's declared once, in one place.
- **Add a speaker window** — put `speaker: true` on the meeting. "Speaker questions due" is
  placed on the *previous* meeting for you; never enter it by hand.

## The build refuses to publish bad data

`python3 build.py` (and every push and pull request) fails if:

- the number of `meetings` entries doesn't match the number of dates the calendar derives
- a deadline doesn't land on a class meeting, or lands on a break
- a speaker window has no prior meeting to carry its questions

```
$ python3 build.py
Schedule did not build:
  - HW2 due: 2026-10-15 falls on Fall Break.
```

That's the check a spreadsheet can't do, and the reason the schedule and the syllabus can't
quietly drift apart.

## Local preview

```
pip install pyyaml
python3 build.py && open _site/index.html
```

Design notes: `shilad/comp440-main` → `docs/schedule-site-design.md`.
