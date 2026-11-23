# UA Class Tracker (24/7 on GitHub Actions)

Checks UAccess public Class Search every ~5 minutes for Jeri Heileman's UNIV 301
sections (Spring 2027: 68309, 68310, 55576) and pushes an alert to your phone the
moment one changes from **Closed**. Runs on GitHub's servers, so your laptop can be off.

## Setup (about 5 minutes)

1. **Phone:** install the free **ntfy** app and subscribe to a private topic name,
   a random name that does NOT appear anywhere in this public repo (anyone who knows the name can read it).
2. **GitHub:** create a new **public** repo (public = unlimited free Actions minutes;
   a private repo would run out of the 2,000 free minutes in about 10 days at this frequency).
   Upload `ua_class_tracker.py` and this README. Then add the workflow with
   **Add file → Create new file**, name it `.github/workflows/check.yml`, and paste in
   the contents of `check.yml` (macOS hides dot-folders, so drag-and-drop usually skips it).
3. **Secret:** repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `NTFY_TOPIC`  Value: the topic name from step 1
4. **Test:** Actions tab → "UA class tracker" → **Run workflow**. The first run should go
   green and its log shows each section's status. Scheduled runs start on their own after that.

## Notes
- If a run fails (e.g. UA changes the page), GitHub emails you.
- GitHub pauses scheduled workflows after 60 days with no commits to the repo; that's
  past spring registration, but a trivial commit restarts the 60-day clock.
- To track a different class: edit the CONFIG block at the top of `ua_class_tracker.py`
  (TERM_CODE, SUBJECT, CATALOG_NBR, INSTRUCTOR, EXTRA_CLASS_NUMBERS).
- Stop it: Actions → "UA class tracker" → ⋯ → Disable workflow.
