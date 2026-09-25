"""Background delivery worker.

Single-process thread that polls SQLite for due events and POSTs them.

Key properties:
- Per-subscription FIFO ordering: only the oldest pending event per
  subscription is eligible per poll cycle (prevents out-of-order delivery).
- Cross-customer isolation: deliveries run in a ThreadPoolExecutor, so one
  slow/down endpoint never blocks other subscriptions (no global head-of-line).
- At-least-once: events stay 'pending' until 2xx or terminal 'failed'.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, db, delivery

log = logging.getLogger(__name__)


def fetch_due_events(limit: int) -> list[dict]:
    """Due pending events, at most one (the oldest) per subscription."""
    conn = db.get_conn()
    now = db.utcnow_iso()
    with db._lock:
        rows = conn.execute(
            """
            SELECT e.id, e.subscription_id, e.payload, e.attempts,
                   e.created_at, s.url, s.secret
            FROM events e JOIN subscriptions s ON s.id = e.subscription_id
            WHERE e.status = 'pending' AND e.next_attempt_at <= ?
            ORDER BY e.created_at ASC
            LIMIT ?
            """,
            (now, limit * 5),
        ).fetchall()
    # Enforce per-subscription FIFO: keep only oldest row per subscription.
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        if r["subscription_id"] in seen:
            continue
        seen.add(r["subscription_id"])
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def deliver_one(event: dict) -> None:
    conn = db.get_conn()
    event_id: str = event["id"]
    attempt_no: int = int(event["attempts"]) + 1
    body: bytes = event["payload"].encode()
    ok, status_code, error = delivery.post_event(
        url=event["url"],
        body=body,
        event_id=event_id,
        subscription_id=event["subscription_id"],
        attempt_no=attempt_no,
        secret=event["secret"],
    )
    now = datetime.now(timezone.utc)
    with db._lock:
        conn.execute(
            """
            INSERT INTO attempts (event_id, attempt_no, status_code, success, error, attempted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, attempt_no, status_code, 1 if ok else 0, error, now.isoformat()),
        )
        if ok:
            conn.execute(
                "UPDATE events SET status='delivered', attempts=?, last_error=NULL,"
                " updated_at=? WHERE id=?",
                (attempt_no, now.isoformat(), event_id),
            )
            log.info("delivered event=%s attempt=%d status=%s", event_id, attempt_no, status_code)
        else:
            nxt = delivery.compute_next_attempt(attempt_no, now)
            if nxt is None:
                conn.execute(
                    "UPDATE events SET status='failed', attempts=?, last_error=?,"
                    " updated_at=? WHERE id=?",
                    (attempt_no, error, now.isoformat(), event_id),
                )
                log.warning("event=%s terminal failed after %d attempts: %s", event_id, attempt_no, error)
            else:
                conn.execute(
                    "UPDATE events SET status='pending', attempts=?, last_error=?,"
                    " next_attempt_at=?, updated_at=? WHERE id=?",
                    (attempt_no, error, nxt.isoformat(), now.isoformat(), event_id),
                )
                log.info("event=%s attempt=%d failed (%s), next=%s", event_id, attempt_no, error, nxt.isoformat())
        conn.commit()


def poll_once(limit: int | None = None, max_workers: int | None = None) -> int:
    """Run a single poll cycle. Returns number of events attempted. Test-friendly."""
    due = fetch_due_events(limit or config.WORKER_BATCH_SIZE)
    if not due:
        return 0
    with ThreadPoolExecutor(max_workers=max_workers or config.WORKER_MAX_INFLIGHT) as pool:
        list(pool.map(deliver_one, due))
    return len(due)


class Worker(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__(name="delivery-worker", daemon=True)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("delivery worker started")
        while not self._stop.is_set():
            try:
                n = poll_once()
                # Sleep always to avoid hot-spin; longer when idle is unnecessary
                # since interval is already 1s.
                time.sleep(config.WORKER_POLL_INTERVAL_S if n == 0 else 0.1)
            except Exception:  # never let the loop die
                log.exception("worker poll failed")
                time.sleep(config.WORKER_POLL_INTERVAL_S)
