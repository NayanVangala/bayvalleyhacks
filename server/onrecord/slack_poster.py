"""Slack poster for OnRecord digests.

Slack is a sink, never a dependency. Every digest is queued in the
slack outbox first, so a failed or unconfirmed post never loses the
digest. Posting only happens after explicit human confirmation:
unconfirmed digests stay queued with reason awaiting_confirmation.

When confirmed, the digest goes to the configured incoming webhook.
If no webhook is configured, it falls back to a local file sink under
the data directory. Webhook failures leave the row queued and are
reported in the result dict; this module never raises for a failed
post attempt.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def _is_allowed_webhook(url) -> bool:
    """True only for a real Slack incoming webhook URL.

    The digest contains verbatim conversation quotes. Posting it to
    whatever URL happens to be in the environment would exfiltrate
    transcripts if SLACK_WEBHOOK_URL were ever pointed somewhere else,
    so the destination is pinned to https://hooks.slack.com.
    """
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.hostname == "hooks.slack.com"


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _outbox_id(enqueued):
    if enqueued is None:
        return None
    if isinstance(enqueued, dict):
        if "id" in enqueued:
            return enqueued.get("id")
        if "outbox_id" in enqueued:
            return enqueued.get("outbox_id")
        return None
    outbox_id = getattr(enqueued, "id", None)
    if outbox_id is not None:
        return outbox_id
    if isinstance(enqueued, (str, int)):
        return enqueued
    return None


def _mark_posted(store, outbox_id):
    if store is not None and outbox_id is not None:
        store.mark_outbox_posted(outbox_id)


def post_digest(store, session_id, digest_text, settings, confirmed=False) -> dict:
    """Queue a digest and, only when confirmed, deliver it.

    Returns a dict {posted: bool, reason: str, outbox_id}. The digest
    is always enqueued via store.enqueue_slack first, even when it is
    not confirmed yet.
    """
    enqueued = store.enqueue_slack(session_id, digest_text)
    outbox_id = _outbox_id(enqueued)

    if not confirmed:
        return {
            "posted": False,
            "reason": "awaiting_confirmation",
            "outbox_id": outbox_id,
        }

    webhook_url = _get(settings, "slack_webhook_url")

    if webhook_url and not _is_allowed_webhook(webhook_url):
        return {
            "posted": False,
            "reason": "webhook url rejected: not an https hooks.slack.com URL",
            "outbox_id": outbox_id,
        }

    if webhook_url:
        payload = json.dumps({"text": digest_text}).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
            try:
                status = getattr(response, "status", None)
                if not isinstance(status, int):
                    status = None
                    getcode = getattr(response, "getcode", None)
                    if callable(getcode):
                        code = getcode()
                        if isinstance(code, int):
                            status = code
                    if status is None:
                        code_attr = getattr(response, "code", None)
                        if isinstance(code_attr, int):
                            status = code_attr
                if status is None:
                    status = 200
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        except Exception as exc:
            return {
                "posted": False,
                "reason": "webhook failed: " + str(exc),
                "outbox_id": outbox_id,
            }
        if status == 200:
            _mark_posted(store, outbox_id)
            return {
                "posted": True,
                "reason": "webhook",
                "outbox_id": outbox_id,
            }
        return {
            "posted": False,
            "reason": "webhook failed: HTTP " + str(status),
            "outbox_id": outbox_id,
        }

    # No webhook configured: local file sink, still a real delivery
    # record, and the outbox row is marked posted.
    try:
        data_dir = _get(settings, "data_dir")
        outbox_dir = Path(data_dir) / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        path = outbox_dir / (str(session_id) + "-" + str(outbox_id) + ".txt")
        path.write_text(digest_text, encoding="utf-8")
    except Exception as exc:
        return {
            "posted": False,
            "reason": "file sink failed: " + str(exc),
            "outbox_id": outbox_id,
        }
    _mark_posted(store, outbox_id)
    return {
        "posted": True,
        "reason": "file_sink_no_webhook",
        "outbox_id": outbox_id,
    }
