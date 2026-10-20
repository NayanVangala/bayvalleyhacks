#!/usr/bin/env python3
"""Seed rehearsal fixture data for OnRecord.

What it does:
    Loads a small, fictional rehearsal conversation into OnRecord so the
    demo can be practiced end to end without using real captures. It tries
    the HTTP rehearsal ingest API first and falls back to writing a JSON
    fixture file that the server seed tooling can load later.

Quarantine rule:
    Rehearsal fixtures are quarantined from real data. This script only
    runs when the rehearsal flag is set (env ONRECORD_REHEARSAL=1, matching
    .env.example, or the --rehearsal flag). Every record it creates is
    tagged source="rehearsal". Seeded data must be visually labeled as
    rehearsal in any UI.

How to run:
    ONRECORD_REHEARSAL=1 python3 scripts/seed_demo.py
    ONRECORD_REHEARSAL=1 python3 scripts/seed_demo.py --file
    ONRECORD_REHEARSAL=1 python3 scripts/seed_demo.py --file --out data/rehearsal_fixture.json
    ONRECORD_REHEARSAL=1 python3 scripts/seed_demo.py --wipe

Warning:
    Never load this fixture during judging. Never present fixture output
    as a live capture. This data is fictional and is only for rehearsal.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures: plain data only. All people are fictional. Every record carries
# source="rehearsal" so it stays quarantined from real data.
# ---------------------------------------------------------------------------

PEOPLE = [
    {
        "name": "Maya Chen",
        "role": "Organizer",
        "notes": "Leads the community garden planning and tracks the budget.",
        "source": "rehearsal",
    },
    {
        "name": "Liam Ortiz",
        "role": "Volunteer, tools and logistics",
        "notes": "Brings tools and helps with soil delivery and setup.",
        "source": "rehearsal",
    },
    {
        "name": "Priya Nair",
        "role": "Volunteer, school liaison",
        "notes": "Checks with the school office about access and scheduling.",
        "source": "rehearsal",
    },
]

CONVERSATION = {
    "title": "Community garden planning",
    "source": "rehearsal",
}

# Exactly one segment is low confidence and flagged REVIEW to exercise
# the confidence gate. Exactly one segment has no speaker (Unknown) to
# exercise the Unknown speaker path. Do not add a second of either.
SEGMENTS = [
    {
        "speaker": "Maya Chen",
        "offset_seconds": 0,
        "text": "Thanks for meeting to plan the community garden. Let us lock the planting plan today.",
        "confidence": 0.97,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Liam Ortiz",
        "offset_seconds": 12,
        "text": "The nursery on Main Street is donating tomato seedlings for all the beds.",
        "confidence": 0.96,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Priya Nair",
        "offset_seconds": 28,
        "text": "That is great. I counted the beds and we have room for those seedlings plus herbs.",
        "confidence": 0.95,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Maya Chen",
        "offset_seconds": 45,
        "text": "We still need $300 for soil before we can fill the raised beds.",
        "confidence": 0.98,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Liam Ortiz",
        "offset_seconds": 62,
        "text": "I can bring tools on planting day, shovels, gloves, and two wheelbarrows.",
        "confidence": 0.94,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Priya Nair",
        "offset_seconds": 80,
        "text": "I will check with the school office about water access and weekend gate hours.",
        "confidence": 0.93,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Maya Chen",
        "offset_seconds": 98,
        "text": "Planting is Saturday November 7th, starting at nine in the morning.",
        "confidence": 0.97,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": None,
        "offset_seconds": 115,
        "text": "Someone near the gate mentions bringing extra watering cans.",
        "confidence": 0.88,
        "flag": None,
        "source": "rehearsal",
    },
    {
        "speaker": "Liam Ortiz",
        "offset_seconds": 132,
        "text": "If the soil money comes through, we can order delivery on Friday.",
        "confidence": 0.41,
        "flag": "REVIEW",
        "source": "rehearsal",
    },
    {
        "speaker": "Priya Nair",
        "offset_seconds": 150,
        "text": "Let us confirm the volunteer list next week and send a reminder to everyone.",
        "confidence": 0.96,
        "flag": None,
        "source": "rehearsal",
    },
]

DEFAULT_OUT = "data/rehearsal_fixture.json"
DEFAULT_BASE = "http://localhost:8000"


def build_fixture():
    """Return the full fixture as one dict for file mode."""
    return {
        "source": "rehearsal",
        "conversation": dict(CONVERSATION),
        "people": list(PEOPLE),
        "segments": list(SEGMENTS),
    }


def get_base_url():
    return os.environ.get("ONRECORD_API_URL", DEFAULT_BASE).rstrip("/")


def http_request(method, url, payload=None):
    """Make a small HTTP request with urllib. Return (status, body_text).

    HTTPError is returned as a status instead of raising, so callers can
    handle 404 cleanly. Other network errors raise URLError.
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def api_is_healthy(base):
    try:
        status, _body = http_request("GET", base + "/health")
    except (urllib.error.URLError, OSError):
        return False
    return 200 <= status < 300


def post_via_api(base):
    """POST people and segments via the rehearsal API.

    Return "api" on success, or None if the server does not expose the
    rehearsal ingest endpoints yet (404), so the caller can fall back
    to file mode. Raise RuntimeError on other failures.
    """
    for person in PEOPLE:
        status, body = http_request("POST", base + "/v1/rehearsal/people", person)
        if status == 404:
            print("Server does not expose rehearsal ingest yet (404 on /v1/rehearsal/people). Falling back to file mode.")
            return None
        if not (200 <= status < 300):
            raise RuntimeError("POST /v1/rehearsal/people failed with status %s: %s" % (status, body))
    for segment in SEGMENTS:
        payload = dict(segment)
        payload["conversation_title"] = CONVERSATION["title"]
        status, body = http_request("POST", base + "/v1/rehearsal/segments", payload)
        if status == 404:
            print("Server does not expose rehearsal ingest yet (404 on /v1/rehearsal/segments). Falling back to file mode.")
            return None
        if not (200 <= status < 300):
            raise RuntimeError("POST /v1/rehearsal/segments failed with status %s: %s" % (status, body))
    return "api"


def write_fixture_file(out_path):
    """Write the fixture JSON to out_path, creating parent directories.

    Only writes to the --out path (default inside data/). Never write
    fixture JSON anywhere outside data/ or the explicit --out path.
    """
    path = Path(out_path)
    if path.parent and str(path.parent) != "":
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(build_fixture(), fh, indent=2)
        fh.write("\n")
    return "file"


def wipe_data(args, base):
    # Wipe removes ONLY rehearsal-tagged data. It must never touch
    # non-rehearsal data, in either API mode or file mode.
    if not args.file and api_is_healthy(base):
        try:
            status, body = http_request("DELETE", base + "/v1/rehearsal")
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError("DELETE /v1/rehearsal failed: %s" % exc)
        if status == 404:
            print("Server does not expose rehearsal wipe yet (404 on DELETE /v1/rehearsal). Falling back to file mode.")
        elif 200 <= status < 300:
            print("Wiped rehearsal data via API.")
            return "api"
        else:
            raise RuntimeError("DELETE /v1/rehearsal failed with status %s: %s" % (status, body))
    path = Path(args.out)
    if path.exists():
        path.unlink()
        print("Deleted rehearsal fixture file: %s" % path)
    else:
        print("No rehearsal fixture file to delete: %s" % path)
    return "file"


def load_data(args, base):
    """Load fixtures via API if possible, otherwise via file mode."""
    if args.file:
        write_fixture_file(args.out)
        print("Wrote rehearsal fixture file: %s" % args.out)
        return "file"
    if api_is_healthy(base):
        mode = post_via_api(base)
        if mode is not None:
            print("Loaded rehearsal fixtures via API at %s" % base)
            return mode
        # 404 on rehearsal endpoints: fall through to file mode.
    else:
        print("API not healthy at %s. Falling back to file mode." % base)
    write_fixture_file(args.out)
    print("Wrote rehearsal fixture file: %s" % args.out)
    return "file"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Seed OnRecord rehearsal fixture data (quarantined, rehearsal only).")
    parser.add_argument("--rehearsal", action="store_true", help="Confirm rehearsal mode. Required unless ONRECORD_REHEARSAL=1 is set.")
    parser.add_argument("--file", action="store_true", help="Force file mode, writing the fixture JSON instead of using the API.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Fixture JSON output path for file mode (default: data/rehearsal_fixture.json).")
    parser.add_argument("--wipe", action="store_true", help="Remove only rehearsal-tagged data and exit.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Hard guard: refuse unless rehearsal is explicitly enabled.
    if os.environ.get("ONRECORD_REHEARSAL") != "1" and not args.rehearsal:
        print("Refusing to run: rehearsal mode is not enabled. Set ONRECORD_REHEARSAL=1 or pass --rehearsal. Fixture data is quarantined and must never load outside rehearsal.", file=sys.stderr)
        return 2

    base = get_base_url()
    try:
        if args.wipe:
            mode = wipe_data(args, base)
            action = "wipe"
        else:
            mode = load_data(args, base)
            action = "load"
    except Exception as exc:
        print("Failed: %s" % exc, file=sys.stderr)
        return 1

    print("Summary: action=%s people=%d segments=%d mode=%s source=rehearsal" % (action, len(PEOPLE), len(SEGMENTS), mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
