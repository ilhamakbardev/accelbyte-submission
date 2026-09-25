"""FastAPI service: ingest events, manage subscriptions, expose status."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from . import db, schemas, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_worker: worker.Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    db.get_conn()  # ensure schema exists
    _worker = worker.Worker()
    _worker.start()
    yield
    if _worker:
        _worker.stop()


app = FastAPI(title="Webhook Delivery Service", version="1.0.0", lifespan=lifespan)


def _row_to_sub(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "customer_id": r["customer_id"], "url": r["url"], "created_at": r["created_at"]}


def _row_to_event(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "subscription_id": r["subscription_id"],
        "status": r["status"],
        "attempts": r["attempts"],
        "next_attempt_at": r["next_attempt_at"],
        "last_error": r["last_error"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/subscriptions", status_code=201, response_model=schemas.SubscriptionOut)
def create_subscription(body: schemas.SubscriptionCreate) -> dict:
    sub_id = f"sub_{uuid.uuid4().hex[:12]}"
    now = db.utcnow_iso()
    conn = db.get_conn()
    with db._lock:
        conn.execute(
            "INSERT INTO subscriptions (id, customer_id, url, secret, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (sub_id, body.customer_id, str(body.url), body.secret, now),
        )
        conn.commit()
    return {"id": sub_id, "customer_id": body.customer_id, "url": str(body.url), "created_at": now}


@app.get("/subscriptions")
def list_subscriptions() -> dict:
    conn = db.get_conn()
    with db._lock:
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY created_at DESC LIMIT 200").fetchall()
    return {"subscriptions": [_row_to_sub(r) for r in rows]}


@app.post("/events", status_code=202)
def ingest_event(body: schemas.EventCreate) -> dict:
    conn = db.get_conn()
    with db._lock:
        sub = conn.execute("SELECT id FROM subscriptions WHERE id=?", (body.subscription_id,)).fetchone()
        if not sub:
            raise HTTPException(404, f"subscription {body.subscription_id} not found")
        event_id = body.event_id or f"evt_{uuid.uuid4().hex[:12]}"
        existing = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if existing:
            # Idempotent ingest: duplicate event_id returns the original.
            row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return {"id": event_id, "status": row["status"], "duplicate": True}
        now = db.utcnow_iso()
        try:
            payload_text = json.dumps(body.payload, separators=(",", ":"))
        except (TypeError, ValueError):
            raise HTTPException(422, "payload must be JSON-serializable")
        conn.execute(
            "INSERT INTO events (id, subscription_id, payload, status, attempts,"
            " next_attempt_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)",
            (event_id, body.subscription_id, payload_text, now, now, now),
        )
        conn.commit()
    return {"id": event_id, "status": "pending", "duplicate": False}


@app.get("/events/{event_id}", response_model=schemas.EventDetail)
def get_event(event_id: str) -> dict:
    conn = db.get_conn()
    with db._lock:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"event {event_id} not found")
        hist = conn.execute(
            "SELECT attempt_no, status_code, success, error, attempted_at FROM attempts"
            " WHERE event_id=? ORDER BY attempt_no ASC",
            (event_id,),
        ).fetchall()
    out = _row_to_event(row)
    out["history"] = [
        {
            "attempt_no": h["attempt_no"],
            "status_code": h["status_code"],
            "success": bool(h["success"]),
            "error": h["error"],
            "attempted_at": h["attempted_at"],
        }
        for h in hist
    ]
    return out


@app.get("/events")
def list_events(
    subscription_id: str | None = None,
    status: schemas.EventStatus | None = None,
    limit: int = Query(default=50, le=200),
) -> dict:
    query = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if subscription_id:
        query += " AND subscription_id=?"
        params.append(subscription_id)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    with db._lock:
        rows = conn.execute(query, params).fetchall()
    return {"events": [_row_to_event(r) for r in rows]}


@app.post("/events/{event_id}/retry")
def retry_event(event_id: str) -> dict:
    """Operator re-drive for failed events. Resets to pending for immediate retry."""
    conn = db.get_conn()
    now = db.utcnow_iso()
    with db._lock:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"event {event_id} not found")
        if row["status"] == "delivered":
            return {"id": event_id, "status": "delivered", "retried": False}
        conn.execute(
            "UPDATE events SET status='pending', attempts=0, last_error=NULL,"
            " next_attempt_at=?, updated_at=? WHERE id=?",
            (now, now, event_id),
        )
        conn.commit()
    return {"id": event_id, "status": "pending", "retried": True}
