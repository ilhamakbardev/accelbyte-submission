# Decisions

## 1. Delivery: at-least-once
An event stays `pending` until the customer endpoint answers 2xx, or it fails for good.
Each POST carries a stable `X-Event-Id`, so customers can ignore duplicates, plus an
`X-Signature` when they set a secret, so they can verify it came from us.
Trade-off: customers may receive an event twice and must handle that.
We chose this because the alternatives are worse. Exactly-once is impossible over
plain HTTP, and at-most-once would silently drop events during outages.

## 2. Retries: back off, 8 tries, then stop
Wait between tries: 30s, 60s, 5m, 15m, 30m, 1h, 2h, 4h (with a little randomness
so we don't hammer a recovering server). Only 2xx counts as delivered; each try
times out after 5s. After the 8th failure the event becomes `failed` with its full
history kept, and an operator can retry it by hand. This spacing survives about
6 to 7 hours of downtime without losing anything, then stops instead of retrying forever.

## 3. Long outages: one slow customer never blocks the rest
Each event carries its own "try me after" timestamp, and deliveries run on a pool
of workers. So when one endpoint is down for 6 hours, only its own events wait
(longer waits each time); everyone else delivers normally. When it recovers, its
queued events drain oldest-first. Everything lives in SQLite on disk, so a restart
loses nothing.

## 4. Order: first-in-first-out per customer only
For each customer we deliver the oldest waiting event first, so their events arrive
in order. We don't order across customers; that would pointlessly couple them.
Trade-off: if a customer's oldest event keeps failing, their newer ones wait behind
it. We accept that because out-of-order webhooks break most receivers.

## Left out on purpose
Auth, billing, UI, production deploy (per the brief). Also: multi-server workers,
a dead-letter queue, circuit breakers, payload filtering. Those are listed as next
steps in the README. Storage is a local SQLite file, which is a real datastore and
fits the single-process scope.
