# COMP 440 schedule site

Public course schedule for COMP 440 (Collective Intelligence, Macalester), Fall 2026.
Live at **https://shilad.github.io/comp440-website/**. Instructor: Shilad Sen.

## Who owns what

- **This repo is owned by the schedule-site session.** Structure, `build.py`, `schedule.yml`,
  rendering, deploy. Other sessions: report issues, don't push.
- **Curriculum and course administration are owned by the course-admin session**
  (`comp440-main`). *What* happens on a given day is theirs. *How the schedule is modeled and
  rendered* is this repo's.
- **Course facts come from the instructor.** Classroom, deadlines, grading, speaker
  confirmations, LMS URLs. Leave them TBD rather than filling them in — a plausible invention is
  worse here than a visible gap, because students act on this page.

## Changing the schedule

Edit **`schedule.yml`**. Nothing else. Push to `main`; the site rebuilds and redeploys in about a
minute. `_site/` is build output — never edit it, never commit it.

**You never type a meeting date.** `calendar` declares the meeting pattern and the term bounds;
the build assigns dates to the `meetings` list in order.

- **Cancel a class** — replace its entry with `- break: Reason`. Later dates are unaffected.
- **Insert a session** — add an entry, push `last` out by one meeting.
- **Reorder topics** — move entries; dates follow position.
- **Move a deadline** — change `due:` on the assignment. Declared once, in one place.
- **Add a speaker** — `speaker: true` for an unnamed window, or `speaker: Their Name`.
  "Speaker questions due" is placed automatically per the `speaker_questions` policy.
  Never enter it by hand.

## Invariants — these are the design, not preferences

1. **No date on this site is typed and unchecked.** Every date is either derived from the meeting
   pattern or validated against it. Anything new that carries a date must be validated the same
   way, reusing `place()` / `by_date` rather than adding a second date path.
2. **Every fact is declared once.** The due time is one string. An assignment's due date lives on
   the assignment. If a fact appears in two places, one of them will go stale — that is what this
   site was built to escape.
3. **The build refuses to publish data that contradicts itself,** and runs on pull requests too.
   A wrong schedule that fails loudly beats a wrong schedule that publishes.
4. **Links are verified before they ship.** Check the file exists and is actually shared with
   students before linking it. Forms use their `/viewform` responder path — never `/edit`, which
   sends a student into the form editor. Uploaded files use Drive's `/view` URL, not a Docs editor
   path.
5. **Status stays honest.** `tbd: true` renders visibly as TBD. Don't dress a plan as done.

## Requesting a schedule change from another session

Send the **YAML fragment you want**, not a description of it. Prose relays lose details — a
request once described a doc as "Working with Claude (Fall 2026)" when the file was actually
titled "Working with AI (Fall 2026)", and the wrong title would have shipped to students.

Include the Drive file ID for anything to be linked, so it can be verified rather than guessed:

```yaml
# Thu Sep 10 — add a material
- { text: Community norms activity, tbd: true }

# link something (id, so sharing can be checked before it ships)
- { text: Working with AI (course AI policy),
    drive_id: 1Eb6qxeS2wy-9TBzL8eGk9j75iYu_yEV4VoLQaywRz8E }
```

What happens to such a request: anything inside this repo's ownership is applied directly. Any
asserted course fact is verified against the primary source first — Drive for a document's title
and sharing, the instructor for a policy. A brand-new student-facing surface (a whole new page)
goes back to the instructor before it is built.

## Build

```
pip install pyyaml
python3 build.py          # validates, then writes _site/index.html
```

It exits non-zero, and the deploy stops, if the number of `meetings` entries doesn't match the
derived dates, a deadline misses a class meeting or lands on a break, `speaker_questions.when` is
not `visit_day`/`prior_meeting`, or a speaker window has no prior meeting to carry its questions.
It also prints link coverage so remaining TBDs stay visible.

Design rationale: `shilad/comp440-main` → `docs/schedule-site-design.md`.
