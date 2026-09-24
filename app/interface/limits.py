"""
Per-caller share of the day's model budget.

The DailyQuotaGuard is a ceiling for everyone together: it stops the app
spending past the free tier, but it does nothing to stop the first
visitor of the day spending all of it before the second one arrives.
This is the other half — each caller's slice of that ceiling.

Checked before the hourly limiter on purpose. Both limiters increment as
they check, so whichever runs first consumes a slot even when the other
one is the reason the request is refused; running the daily one first
means a caller who is out of budget for the day does not also burn an
hourly slot they could never have used.
"""
from __future__ import annotations

from fastapi import HTTPException


def enforce_daily_budget(container, client_ip: str) -> None:
    """Raise 429 when this caller has used their share of today's budget."""
    allowed, _remaining = container.daily_ip_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "You have used your share of today's model budget. It resets at "
                "midnight UTC. Everything computed from already-ingested repositories "
                "— the diagram, code health, reading source files — still works."
            ),
        )
