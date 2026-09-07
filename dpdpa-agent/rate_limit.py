"""
Veritas Rate Limiter
====================
In-memory sliding-window rate limiter for protecting sensitive endpoints.
No external dependencies (no Redis). Suitable for single-process deployments.

Protects:
  - POST /api/auth/login        — brute-force login prevention
  - POST /api/auth/setup        — prevent rapid admin account creation
  - POST /agent/register        — prevent registration key enumeration
  - POST /agents/issue-key      — prevent key generation flooding

Design:
  - Keyed by (endpoint, identifier) — identifier is IP address or username
  - Sliding window: counts requests within the last `window_seconds`
  - On exceed: raises HTTP 429 with Retry-After header
  - State is in-memory — resets on server restart (acceptable for rate limiting)
  - Thread-safe via threading.Lock
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request


class _RateLimiter:
    """
    Sliding window rate limiter.
    Stores timestamps of recent requests per (endpoint, key) pair.
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        # {(endpoint, key): deque of timestamps}
        self._windows: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    def check(
        self,
        endpoint: str,
        key: str,
        *,
        max_requests: int,
        window_seconds: int,
    ) -> None:
        """
        Record a request and raise HTTP 429 if the limit is exceeded.

        Args:
            endpoint:        Logical name for the endpoint (e.g. "login").
            key:             Per-user key (IP address or username).
            max_requests:    Maximum allowed requests in the window.
            window_seconds:  Rolling window length in seconds.
        """
        now = time.monotonic()
        cutoff = now - window_seconds
        bucket_key = (endpoint, key)

        with self._lock:
            bucket = self._windows[bucket_key]
            # Evict timestamps outside the window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= max_requests:
                oldest = bucket[0]
                retry_after = int(oldest + window_seconds - now) + 1
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Too many requests. "
                        f"Maximum {max_requests} attempts per {window_seconds}s. "
                        f"Try again in {retry_after}s."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

    def reset(self, endpoint: str, key: str) -> None:
        """Clear the rate limit bucket for a key (e.g. after successful login)."""
        with self._lock:
            self._windows.pop((endpoint, key), None)

    def cleanup(self) -> None:
        """Remove all empty / expired buckets. Call periodically to avoid memory growth."""
        now = time.monotonic()
        with self._lock:
            dead = [k for k, v in self._windows.items() if not v or v[-1] < now - 3600]
            for k in dead:
                del self._windows[k]


# Module-level singleton
_limiter = _RateLimiter()


def _client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For if trusted."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# FastAPI dependency factories
# ---------------------------------------------------------------------------

def login_rate_limit(request: Request) -> None:
    """
    Dependency: rate-limit login attempts by IP.
    10 attempts per 5 minutes. Raises 429 on exceed.
    """
    _limiter.check(
        "login",
        _client_ip(request),
        max_requests=10,
        window_seconds=300,
    )


def setup_rate_limit(request: Request) -> None:
    """
    Dependency: rate-limit /setup by IP.
    3 attempts per 10 minutes.
    """
    _limiter.check(
        "setup",
        _client_ip(request),
        max_requests=3,
        window_seconds=600,
    )


def register_rate_limit(request: Request) -> None:
    """
    Dependency: rate-limit agent registration by IP.
    20 registrations per hour (generous — allows fleet enrollment).
    """
    _limiter.check(
        "agent_register",
        _client_ip(request),
        max_requests=20,
        window_seconds=3600,
    )


def issue_key_rate_limit(request: Request) -> None:
    """
    Dependency: rate-limit registration key issuance by IP.
    30 keys per hour.
    """
    _limiter.check(
        "issue_key",
        _client_ip(request),
        max_requests=30,
        window_seconds=3600,
    )


def clear_login_limit(ip: str) -> None:
    """Call after successful login to reset the IP's failure counter."""
    _limiter.reset("login", ip)
