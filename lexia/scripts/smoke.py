#!/usr/bin/env python3
"""End to end smoke test for the Lexia Read Mode API.

Stdlib only. Starts the Lexia API as a subprocess on port 8100 with the
fake OCR and TTS engines and a fresh temp data dir, then exercises:

  GET  /health
  POST /read
  GET  /read/{session_id}
  POST /read/{session_id}/speak
  GET  {audio_url}          (must look like a WAV file)
  GET  /read/nope            (must be 404)

Prints one PASS line per step and a final SMOKE OK on success.
On any failure it prints what failed and exits 1.
The subprocess is always terminated in a finally block.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
PYTHON_BIN = REPO_ROOT / "lexia" / ".venv" / "bin" / "python"

BASE_URL = "http://127.0.0.1:8100"
HEALTH_TIMEOUT_SECONDS = 20.0
REQUEST_TIMEOUT_SECONDS = 10.0

TEST_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


class SmokeFailure(Exception):
    """Raised when a smoke step fails."""


def check(condition, message):
    if not condition:
        raise SmokeFailure(message)


def http_request(method, url, payload=None):
    """Perform an HTTP request with urllib.

    Returns a tuple of (status_code, body_bytes).
    HTTP error statuses are returned, not raised, so callers can
    assert on them (for example the expected 404 case).
    """
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc}") from exc


def http_json(method, url, payload=None):
    """Perform an HTTP request and parse the body as JSON.

    Returns a tuple of (status_code, parsed_json_or_None, raw_body_bytes).
    """
    status, body = http_request(method, url, payload=payload)
    parsed = None
    if body:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
    return status, parsed, body


def wait_for_health(proc):
    """Poll GET /health until it returns 200 or the timeout expires."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last_error = "no response yet"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SmokeFailure(
                f"API subprocess exited early with code {proc.returncode} "
                "before /health became ready"
            )
        try:
            status, _body = http_request("GET", f"{BASE_URL}/health")
            if status == 200:
                return
            last_error = f"GET /health returned status {status}"
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise SmokeFailure(
        f"GET /health did not return 200 within "
        f"{HEALTH_TIMEOUT_SECONDS:.0f}s (last error: {last_error})"
    )


def run_smoke(proc):
    # Step 1: health.
    wait_for_health(proc)
    print("PASS health: GET /health returned 200")

    # Step 2: POST /read creates a session with chunks.
    status, data, body = http_json(
        "POST", f"{BASE_URL}/read", payload={"image_base64": TEST_IMAGE_BASE64}
    )
    check(
        status == 200,
        f"POST /read expected status 200, got {status}: {body!r}",
    )
    check(isinstance(data, dict), f"POST /read did not return a JSON object: {body!r}")
    session_id = data.get("session_id")
    check(bool(session_id), f"POST /read response missing session_id: {data!r}")
    chunks = data.get("chunks")
    check(
        isinstance(chunks, list) and len(chunks) > 0,
        f"POST /read response chunks missing or empty: {data!r}",
    )
    print(f"PASS create: POST /read returned session_id={session_id} with {len(chunks)} chunk(s)")

    # Step 3: GET /read/{id} returns the same session.
    status, data, body = http_json("GET", f"{BASE_URL}/read/{session_id}")
    check(
        status == 200,
        f"GET /read/{{id}} expected status 200, got {status}: {body!r}",
    )
    check(isinstance(data, dict), f"GET /read/{{id}} did not return a JSON object: {body!r}")
    check(
        data.get("session_id") == session_id,
        f"GET /read/{{id}} session_id mismatch: expected {session_id!r}, got {data.get('session_id')!r}",
    )
    print(f"PASS fetch: GET /read/{session_id} returned the same session_id")

    # Step 4: POST /read/{id}/speak returns audio info.
    status, data, body = http_json(
        "POST", f"{BASE_URL}/read/{session_id}/speak", payload={}
    )
    check(
        status == 200,
        f"POST /read/{{id}}/speak expected status 200, got {status}: {body!r}",
    )
    check(
        isinstance(data, dict),
        f"POST /read/{{id}}/speak did not return a JSON object: {body!r}",
    )
    audio_url = data.get("audio_url")
    check(
        isinstance(audio_url, str) and audio_url.startswith("/audio/"),
        f"speak response audio_url must start with /audio/, got {audio_url!r}",
    )
    duration = data.get("duration_seconds")
    check(
        isinstance(duration, (int, float)) and duration > 0,
        f"speak response duration_seconds must be > 0, got {duration!r}",
    )
    print(f"PASS speak: POST /read/{session_id}/speak returned audio_url={audio_url} duration_seconds={duration}")

    # Step 5: GET the audio URL returns WAV bytes.
    audio_full_url = audio_url if audio_url.startswith("http") else f"{BASE_URL}{audio_url}"
    status, audio_body = http_request("GET", audio_full_url)
    check(
        status == 200,
        f"GET audio expected status 200, got {status}",
    )
    check(
        len(audio_body) >= 4 and audio_body[:4] == b"RIFF",
        f"GET audio body does not look like a WAV file (first bytes: {audio_body[:16]!r})",
    )
    print(f"PASS audio: GET {audio_url} returned 200 with WAV data ({len(audio_body)} bytes)")

    # Step 6: unknown session id is a 404.
    status, _body = http_request("GET", f"{BASE_URL}/read/nope")
    check(
        status == 404,
        f"GET /read/nope expected status 404, got {status}",
    )
    print("PASS not_found: GET /read/nope returned 404")


def main():
    if not PYTHON_BIN.is_file():
        print(f"FAIL startup: venv python not found at {PYTHON_BIN}")
        return 1

    data_dir = tempfile.mkdtemp(prefix="lexia-smoke-")
    env = dict(os.environ)
    env["LEXIA_OCR_ENGINE"] = "fake"
    env["LEXIA_TTS_ENGINE"] = "fake"
    env["LEXIA_DATA_DIR"] = data_dir

    log_path = Path(data_dir) / "uvicorn.log"
    log_file = open(log_path, "wb")
    proc = None
    try:
        proc = subprocess.Popen(
            [
                str(PYTHON_BIN),
                "-m",
                "uvicorn",
                "lexia.api.app:app",
                "--port",
                "8100",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            run_smoke(proc)
        except SmokeFailure as exc:
            print(f"FAIL: {exc}")
            try:
                log_tail = log_path.read_text(errors="replace").strip().splitlines()
                if log_tail:
                    print("Server log tail:")
                    for line in log_tail[-20:]:
                        print(f"  {line}")
            except OSError:
                pass
            return 1
        print("SMOKE OK")
        return 0
    except Exception as exc:  # noqa: BLE001, report anything unexpected as a failure
        print(f"FAIL: unexpected error: {exc}")
        return 1
    finally:
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
        try:
            log_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
