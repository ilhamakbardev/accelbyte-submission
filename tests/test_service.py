"""Tests with mocked customer endpoints (no real network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, delivery, worker


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.reset_for_tests(db_path)
    # Don't auto-start background worker during tests; drive poll_once manually.
    from app.main import app

    with TestClient(app) as c:
        yield c


def _make_sub(client: TestClient, url: str = "http://customer.example/hook") -> str:
    r = client.post("/subscriptions", json={"customer_id": "cust_1", "url": url})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ingest_and_status_pending(client: TestClient):
    sub = _make_sub(client)
    r = client.post("/events", json={"subscription_id": sub, "payload": {"order": 1}})
    assert r.status_code == 202
    eid = r.json()["id"]
    s = client.get(f"/events/{eid}").json()
    assert s["status"] == "pending"
    assert s["attempts"] == 0


def test_successful_delivery(client: TestClient, monkeypatch):
    sub = _make_sub(client)
    def fake_post(url, body, event_id, subscription_id, attempt_no, secret, timeout_s=None):
        return True, 200, None

    monkeypatch.setattr(delivery, "post_event", fake_post)
    eid = client.post("/events", json={"subscription_id": sub, "payload": {"a": 1}}).json()["id"]
    assert worker.poll_once() == 1
    s = client.get(f"/events/{eid}").json()
    assert s["status"] == "delivered"
    assert s["attempts"] == 1
    assert len(s["history"]) == 1
    assert s["history"][0]["success"] is True


def test_retry_then_success(client: TestClient, monkeypatch):
    sub = _make_sub(client)
    calls = {"n": 0}

    def flaky(url, body, event_id, subscription_id, attempt_no, secret, timeout_s=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, 500, "unexpected status 500"
        return True, 200, None

    monkeypatch.setattr(delivery, "post_event", flaky)
    # Shorten backoff for test by patching schedule indirectly: force next_attempt due now.
    eid = client.post("/events", json={"subscription_id": sub, "payload": {}}).json()["id"]
    assert worker.poll_once() == 1
    assert client.get(f"/events/{eid}").json()["status"] == "pending"
    conn = db.get_conn()
    with db._lock:
        conn.execute("UPDATE events SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=?", (eid,))
        conn.commit()
    assert worker.poll_once() == 1
    assert client.get(f"/events/{eid}").json()["status"] == "delivered"


def test_terminal_failure_after_max_attempts(client: TestClient, monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(delivery, "post_event", lambda *a, **k: (False, 500, "unexpected status 500"))
    sub = _make_sub(client)
    eid = client.post("/events", json={"subscription_id": sub, "payload": {}}).json()["id"]
    assert worker.poll_once() == 1
    conn = db.get_conn()
    with db._lock:
        conn.execute("UPDATE events SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=?", (eid,))
        conn.commit()
    assert worker.poll_once() == 1
    s = client.get(f"/events/{eid}").json()
    assert s["status"] == "failed"
    assert s["attempts"] == 2


def test_per_subscription_ordering(client: TestClient, monkeypatch):
    """Only the oldest pending event per subscription is attempted per cycle."""
    monkeypatch.setattr(delivery, "post_event", lambda *a, **k: (True, 200, None))
    sub = _make_sub(client)
    e1 = client.post("/events", json={"subscription_id": sub, "payload": {"n": 1}}).json()["id"]
    e2 = client.post("/events", json={"subscription_id": sub, "payload": {"n": 2}}).json()["id"]
    n = worker.poll_once(limit=10)
    assert n == 1  # second event waits for first cycle (FIFO)
    n2 = worker.poll_once(limit=10)
    assert n2 == 1
    assert client.get(f"/events/{e1}").json()["status"] == "delivered"
    assert client.get(f"/events/{e2}").json()["status"] == "delivered"


def test_cross_customer_isolation(client: TestClient, monkeypatch):
    """One down endpoint must not block another customer's delivery."""
    sub_ok = _make_sub(client, "http://good.example/hook")
    sub_bad = _make_sub(client, "http://bad.example/hook")

    def route(url, body, event_id, subscription_id, attempt_no, secret, timeout_s=None):
        if "bad.example" in url:
            return False, 500, "unexpected status 500"
        return True, 200, None

    monkeypatch.setattr(delivery, "post_event", route)
    e_ok = client.post("/events", json={"subscription_id": sub_ok, "payload": {}}).json()["id"]
    e_bad = client.post("/events", json={"subscription_id": sub_bad, "payload": {}}).json()["id"]
    assert worker.poll_once(limit=10) == 2
    assert client.get(f"/events/{e_ok}").json()["status"] == "delivered"
    assert client.get(f"/events/{e_bad}").json()["status"] == "pending"


def test_idempotent_ingest(client: TestClient):
    sub = _make_sub(client)
    r1 = client.post("/events", json={"subscription_id": sub, "payload": {"x": 1}, "event_id": "evt_dup"})
    r2 = client.post("/events", json={"subscription_id": sub, "payload": {"x": 1}, "event_id": "evt_dup"})
    assert r1.json()["id"] == "evt_dup"
    assert r2.json()["duplicate"] is True


def test_hmac_signature():
    sig = delivery.sign_payload("s3cret", b'{"a":1}')
    assert sig is not None and len(sig) == 64
    assert delivery.sign_payload(None, b"x") is None


def test_manual_retry(client: TestClient, monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(delivery, "post_event", lambda *a, **k: (False, 500, "boom"))
    sub = _make_sub(client)
    eid = client.post("/events", json={"subscription_id": sub, "payload": {}}).json()["id"]
    worker.poll_once()
    assert client.get(f"/events/{eid}").json()["status"] == "failed"
    r = client.post(f"/events/{eid}/retry").json()
    assert r["retried"] is True
    assert client.get(f"/events/{eid}").json()["status"] == "pending"
