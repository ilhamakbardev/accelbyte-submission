# Webhook Delivery Service

Single-process FastAPI service that accepts events and reliably POSTs them to customer URLs with retries, per-customer ordering, and status tracking. SQLite persistence (WAL), in-process background worker.

## Run (one line)

```bash
./run.sh
```

Docs: http://localhost:8000/docs · Health: `GET /health`

## API

| Method | Path | Description |
|---|---|---|
| POST | `/subscriptions` | Register `{customer_id, url, secret?}` → `{id}` |
| GET | `/subscriptions` | List subscriptions |
| POST | `/events` | Ingest `{subscription_id, payload, event_id?}` → `202 {id, status}` (idempotent on `event_id`) |
| GET | `/events/{id}` | Status `pending / delivered / failed` + attempt history |
| GET | `/events?subscription_id=&status=&limit=` | List/filter events |
| POST | `/events/{id}/retry` | Re-drive a `failed` (or pending) event |

Delivery headers: `X-Event-Id`, `X-Subscription-Id`, `X-Attempt-Number`, `X-Signature: sha256=<hmac>` (when secret set).

## Demo with mock customer

```bash
# terminal 1: flaky mock (fails first 2 hits, then 200s). See mock_customer.py
.venv/bin/python mock_customer.py --port 9000 --fail-first 2
# terminal 2: service
PORT=8000 ./run.sh
# terminal 3:
curl -s -X POST localhost:8000/subscriptions -H 'Content-Type: application/json' \
  -d '{"customer_id":"acme","url":"http://localhost:9000/hook","secret":"s3cret"}'
# -> {"id":"sub_..."}
curl -s -X POST localhost:8000/events -H 'Content-Type: application/json' \
  -d '{"subscription_id":"sub_...","payload":{"order":1}}'
# -> {"id":"evt_...","status":"pending"}
curl -s localhost:8000/events/evt_... | head -c 500
```

## Config (env)

`DB_PATH` (default `webhooks.db`), `WORKER_POLL_INTERVAL_S=1.0`, `WORKER_BATCH_SIZE=20`,
`WORKER_MAX_INFLIGHT=10`, `DELIVERY_TIMEOUT_S=5.0`, `MAX_ATTEMPTS=8`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```
9 tests, mocked customer endpoints (no network): ingest + pending status, success, retry-then-success, terminal failure, per-customer FIFO, cross-customer isolation, idempotent ingest, HMAC, manual retry.
