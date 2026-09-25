"""Delivery primitives: signing + HTTP POST + backoff computation."""
from __future__ import annotations

import hashlib
import hmac
import random
from datetime import datetime, timedelta, timezone

import requests

from . import config


def sign_payload(secret: str | None, body: bytes) -> str | None:
    if not secret:
        return None
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_event(
    url: str,
    body: bytes,
    event_id: str,
    subscription_id: str,
    attempt_no: int,
    secret: str | None,
    timeout_s: float | None = None,
) -> tuple[bool, int | None, str | None]:
    """POST one event. Returns (success, status_code, error). 2xx == success."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "webhook-delivery/1.0",
        "X-Event-Id": event_id,
        "X-Subscription-Id": subscription_id,
        "X-Attempt-Number": str(attempt_no),
    }
    signature = sign_payload(secret, body)
    if signature:
        headers["X-Signature"] = f"sha256={signature}"
    try:
        resp = requests.post(
            url,
            data=body,
            headers=headers,
            timeout=timeout_s or config.DELIVERY_TIMEOUT_S,
        )
        if 200 <= resp.status_code < 300:
            return True, resp.status_code, None
        return False, resp.status_code, f"unexpected status {resp.status_code}"
    except requests.Timeout:
        return False, None, "timeout"
    except requests.ConnectionError as exc:
        return False, None, f"connection error: {exc.__class__.__name__}"
    except Exception as exc:  # defensive: never crash worker on weird errors
        return False, None, f"{exc.__class__.__name__}: {exc}"


def compute_next_attempt(failed_attempts: int, now: datetime | None = None) -> datetime | None:
    """Return next attempt time, or None if terminal (exceeded MAX_ATTEMPTS)."""
    if failed_attempts >= config.MAX_ATTEMPTS:
        return None
    idx = min(failed_attempts - 1, len(config.BACKOFF_SCHEDULE_S) - 1)
    base = config.BACKOFF_SCHEDULE_S[idx]
    jittered = base * random.uniform(0.8, 1.2)
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=jittered)
