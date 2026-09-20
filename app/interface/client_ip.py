"""
Client IP resolution, shared by every rate-limited route.

Lives here rather than in one route module because more than one route
now needs it (ask, ask/stream) and a rate limiter keyed off the wrong
IP is a security bug, not a formatting one — there should be exactly
one implementation of this rule.
"""
from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request, *, trust_proxy_headers: bool) -> str:
    # X-Forwarded-For is client-supplied unless a proxy we control sits
    # in front of the app, so it is only honoured when explicitly enabled
    # (TRUST_PROXY_HEADERS). Even then, only the RIGHTMOST entry is
    # trustworthy — that is the one our proxy appended; everything to
    # its left arrived in the request and can be forged to dodge the
    # per-IP rate limit.
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"
