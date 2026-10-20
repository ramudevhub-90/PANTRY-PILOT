"""Which meal it is for the user: breakfast at 8 am, evening snacks at 5 pm, and so on.

The server runs in AWS (UTC), so "now" must come from the user's device: the web app sends its time
zone in the X-Timezone header. Requests without one (Alexa+, scripts) use DEFAULT_TIMEZONE.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OCCASIONS = ("breakfast", "brunch", "lunch", "evening snacks", "dinner", "late-night snack")

# (start hour, start minute, occasion), in local time; the last one wraps past midnight.
_SLOTS = (
    (5, 0, "breakfast"),
    (10, 30, "brunch"),
    (12, 0, "lunch"),
    (15, 30, "evening snacks"),
    (18, 30, "dinner"),
    (22, 30, "late-night snack"),
)

user_timezone: ContextVar[str | None] = ContextVar("user_timezone", default=None)


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def local_now() -> datetime:
    zone = _zone(user_timezone.get()) or _zone(os.environ.get("DEFAULT_TIMEZONE")) or ZoneInfo("Asia/Kolkata")
    return datetime.now(zone)


def occasion_at(moment: datetime) -> str:
    minutes = moment.hour * 60 + moment.minute
    current = _SLOTS[-1][2]  # before 5 am it is still late night
    for hour, minute, occasion in _SLOTS:
        if minutes >= hour * 60 + minute:
            current = occasion
    return current


def current_occasion() -> str:
    return occasion_at(local_now())


class TimezoneMiddleware:
    """Reads the device's IANA time zone (e.g. Asia/Kolkata) from X-Timezone for this request."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        name = dict(scope["headers"]).get(b"x-timezone", b"").decode("latin-1")[:64]
        token = user_timezone.set(name if _zone(name) else None)
        try:
            await self.app(scope, receive, send)
        finally:
            user_timezone.reset(token)
