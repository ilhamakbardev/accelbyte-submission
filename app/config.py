"""Central configuration (env-overridable)."""
from __future__ import annotations

import os

DB_PATH: str = os.getenv("DB_PATH", "webhooks.db")
WORKER_POLL_INTERVAL_S: float = float(os.getenv("WORKER_POLL_INTERVAL_S", "1.0"))
WORKER_BATCH_SIZE: int = int(os.getenv("WORKER_BATCH_SIZE", "20"))
WORKER_MAX_INFLIGHT: int = int(os.getenv("WORKER_MAX_INFLIGHT", "10"))
DELIVERY_TIMEOUT_S: float = float(os.getenv("DELIVERY_TIMEOUT_S", "5.0"))
MAX_ATTEMPTS: int = int(os.getenv("MAX_ATTEMPTS", "8"))

# Backoff seconds per failed attempt; total ~= 7h so a 6h outage stays pending.
BACKOFF_SCHEDULE_S: list[int] = [30, 60, 300, 900, 1800, 3600, 7200, 14400]
