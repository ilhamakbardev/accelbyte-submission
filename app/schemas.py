from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

EventStatus = Literal["pending", "delivered", "failed"]


class SubscriptionCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)
    url: HttpUrl
    secret: str | None = Field(default=None, max_length=512)


class SubscriptionOut(BaseModel):
    id: str
    customer_id: str
    url: str
    created_at: str


class EventCreate(BaseModel):
    subscription_id: str
    payload: dict[str, Any]
    event_id: str | None = Field(default=None, description="Optional client idempotency key")


class EventOut(BaseModel):
    id: str
    subscription_id: str
    status: EventStatus
    attempts: int
    next_attempt_at: str
    last_error: str | None = None
    created_at: str
    updated_at: str


class AttemptOut(BaseModel):
    attempt_no: int
    status_code: int | None
    success: bool
    error: str | None
    attempted_at: str


class EventDetail(EventOut):
    history: list[AttemptOut] = []
