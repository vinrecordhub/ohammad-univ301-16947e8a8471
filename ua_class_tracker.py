#!/usr/bin/env python3
"""
UA Class Tracker — watches UAccess public Class Search and alerts you the moment
a section you care about stops being "Closed".

Default target: UNIV 301 sections taught by Jeri Heileman, Spring 2027
  68309 (301-159, 7wk first), 68310 (301-160, 7wk first), 55576 (301-312, 7wk second)

No NetID / Duo login needed: it uses the public search at
https://studentcenter.arizona.edu/app/ui/public/search

Setup (once):
    python3 -m pip install playwright
    python3 -m playwright install chromium

Run:
    python3 ua_class_tracker.py              # loop forever, check every ~3 min
    python3 ua_class_tracker.py --once       # single check, then exit
    python3 ua_class_tracker.py --show       # same, with a visible browser (debug)
    python3 ua_class_tracker.py --test-alert # fire a test notification
    python3 ua_class_tracker.py --install    # macOS: run in background every 3 min (launchd)
    python3 ua_class_tracker.py --uninstall  # remove the background job

Alerts: macOS notification + sound, and a phone push via ntfy (install the free
"ntfy" app on your phone and subscribe to the topic below).
"""

import argparse
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ─────────────────────────── CONFIG ───────────────────────────
TERM_CODE = "2271"          # Spring 2027 (Fall 2026 = 2264)
SUBJECT = "UNIV"
CATALOG_NBR = "301"
INSTRUCTOR = "Heileman"     # match any section with this instructor (case-insensitive)
EXTRA_CLASS_NUMBERS = {"68309", "68310", "55576"}  # always tracked, even if instructor changes

CHECK_EVERY_SEC = 180       # loop mode interval (a little random jitter is added)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")   # phone push topic (GitHub secret / env var)
OPEN_UACCESS_ON_ALERT = True               # open UAccess in your browser when a seat opens
UACCESS_URL = "https://uaccess.arizona.edu"

STATE_FILE = Path(os.environ.get("STATE_FILE", Path.home() / ".ua_class_tracker_state.json"))
LOG_FILE = Path(os.environ.get("LOG_FILE", Path.home() / "ua_class_tracker.log"))
LAUNCHD_LABEL = "edu.arizona.ua-class-tracker"
# ──────────────────────────────────────────────────────────────

SEARCH_START = "https://studentcenter.arizona.edu/app/ui/public/select-campus"


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ─────────────────────────── SCRAPE ───────────────────────────
def fetch_sections(headless=True):
    """Run the public class search and return a list of section dicts."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_default_timeout(45_000)
        try:
            page.goto(SEARCH_START, wait_until="domcontentloaded")
            page.get_by_text("Main Campus (Including iCourses", exact=False).first.click()

            subj = page.locator('select[id="SSR_CLSRCH_WRK_SUBJECT_SRCH$0"]:visible').first
            subj.wait_for()
            page.locator('select[id="CLASS_SRCH_WRK2_STRM$35$"]:visible').first.select_option(TERM_CODE)
            page.wait_for_timeout(1500)  # term change triggers a server round-trip
            subj.select_option(SUBJECT)
            page.locator('input[id="SSR_CLSRCH_WRK_CATALOG_NBR$2"]:visible').first.fill(CATALOG_NBR)

            # Turn OFF "Show Open Classes Only" so closed sections are listed too
            page.evaluate(
                """() => { const c = document.getElementById('SSR_CLSRCH_WRK_SSR_OPEN_ONLY$8');
                           if (c && c.checked) c.click(); }"""
            )
            page.wait_for_timeout(500)
            page.locator('button[id="CLASS_SRCH_WRK2_SSR_PB_CLASS_SRCH"]:visible').first.click()

            page.wait_for_url(re.compile(r"search-results"), timeout=90_000)
            page.get_by_text(re.compile(r"class section\(s\) found|class sections found")).first.wait_for(timeout=90_000)
            page.wait_for_timeout(1500)
            text = page.evaluate("() => (document.querySelector('main, article') || document.body).innerText")
        finally:
            browser.close()
    return parse_sections(text)


def parse_sections(text):
    sections = []
    for chunk in re.split(r"\nSection ", text)[1:]:
        chunk = chunk.split("CLASS DETAILS")[0]
        lines = [l.strip() for l in chunk.split("\n") if l.strip()]
        head = re.match(r"(\S+)\s+(\w+)\s+\((\d{5})\)", lines[0]) if lines else None
        if not head:
            continue
        sec, component, class_nbr = head.groups()
        status = lines[1] if len(lines) > 1 else "?"
        instructors = [lines[i + 1] for i, l in enumerate(lines[:-1]) if l == "Instructor"]
        # "Meeting TimeTu 9:00Instructor" style lines glue text together; catch those too
        instructors += re.findall(r"Instructor\n?([A-Z][^\n]+)", chunk)
        dates = next((lines[i + 1] for i, l in enumerate(lines[:-1]) if l == "Start and End Date"), "")
        session = next((lines[i + 1] for i, l in enumerate(lines[:-1]) if l == "Session"), "")
        sections.append({
            "class_nbr": class_nbr,
            "section": f"{SUBJECT} {CATALOG_NBR}-{sec}",
            "status": status,
            "instructors": sorted({i for i in instructors if i != "To be Announced"}),
            "session": session,
            "dates": dates,
        })
    return sections


def is_tracked(s):
    return s["class_nbr"] in EXTRA_CLASS_NUMBERS or any(
        INSTRUCTOR.lower() in i.lower() for i in s["instructors"]
    )


# ─────────────────────────── ALERTS ───────────────────────────
def notify(title, body, urgent=True):
    log(f"ALERT: {title} — {body}")
    if platform.system() == "Darwin":
        safe = lambda s: s.replace("\\", "").replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe(body)}" with title "{safe(title)}" sound name "Glass"'],
            check=False,
        )
        if urgent:
            subprocess.run(["say", "U-Access class open"], check=False)
    if NTFY_TOPIC:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=body.encode(),
                headers={"Title": title.encode("ascii", "ignore").decode().strip(), "Priority": "urgent" if urgent else "default",
                         "Tags": "rotating_light" if urgent else "information_source",
                         "Click": UACCESS_URL},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log(f"ntfy push failed: {e}")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─────────────────────────── MAIN CHECK ───────────────────────────
def check(headless=True):
    sections = fetch_sections(headless=headless)
    if not sections:
        raise RuntimeError("Search returned no sections (page layout may have changed)")
    tracked = [s for s in sections if is_tracked(s)]
    old = load_state()
    new = {s["class_nbr"]: s["status"] for s in tracked}

    summary = ", ".join(f'{s["section"]} ({s["class_nbr"]}): {s["status"]}' for s in tracked)
    log(f"Checked {len(sections)} sections; tracking {len(tracked)} → {summary or 'none found!'}")

    opened = []
    for s in tracked:
        prev = old.get(s["class_nbr"])
        if s["status"] != "Closed" and prev != s["status"]:
            opened.append(s)
        elif prev and prev != s["status"]:
            notify(f'{s["section"]} is {s["status"]} again', f'Class {s["class_nbr"]} changed: {prev} → {s["status"]}', urgent=False)
        elif prev is None and old:
            notify("New Heileman section listed",
                   f'{s["section"]} ({s["class_nbr"]}) {s["session"]} — {s["status"]}', urgent=False)

    for s in opened:
        note = " (open with requirements — check Enrollment Options)" if "Req" in s["status"] else ""
        notify(f'🚨 {s["section"]} is {s["status"].upper()}!',
               f'Class #{s["class_nbr"]} · {", ".join(s["instructors"])} · {s["session"]} {s["dates"]}{note}. Enroll now in UAccess.')
    if opened and OPEN_UACCESS_ON_ALERT and platform.system() == "Darwin":
        subprocess.run(["open", UACCESS_URL], check=False)

    save_state(new)
    return tracked


# ─────────────────────────── LAUNCHD ───────────────────────────
def plist_path():
    return Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install():
    if platform.system() != "Darwin":
        sys.exit("--install is for macOS. On Windows use Task Scheduler with: python ua_class_tracker.py --once")
    script = Path(__file__).resolve()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string><string>{script}</string><string>--once</string>
  </array>
  <key>StartInterval</key><integer>{CHECK_EVERY_SEC}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{Path.home()}/ua_class_tracker.launchd.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/ua_class_tracker.launchd.log</string>
</dict></plist>"""
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(p)], check=False, capture_output=True)
    p.write_text(plist)
    subprocess.run(["launchctl", "load", str(p)], check=True)
    print(f"Installed. Checks every {CHECK_EVERY_SEC}s while your Mac is awake.\nLog: {LOG_FILE}")


def uninstall():
    p = plist_path()
    subprocess.run(["launchctl", "unload", str(p)], check=False)
    if p.exists():
        p.unlink()
    print("Background job removed.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--show", action="store_true", help="visible browser (debug)")
    ap.add_argument("--test-alert", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()

    if a.test_alert:
        notify("🚨 TEST: UNIV 301-159 is OPEN!", "This is a test alert from ua_class_tracker.py")
        return
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.once or a.show:
        check(headless=not a.show)
        return

    log("Tracker started. Ctrl+C to stop.")
    fails = 0
    while True:
        try:
            check()
            fails = 0
        except Exception as e:
            fails += 1
            log(f"Check failed ({fails}): {e}")
            if fails == 5:
                notify("Class tracker is failing", f"5 checks in a row failed: {e}", urgent=False)
        time.sleep(CHECK_EVERY_SEC + random.randint(0, 45))


if __name__ == "__main__":
    main()
