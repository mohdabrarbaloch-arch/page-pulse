"""Password hashing, JWT issuance and request-level rate limiting."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

_settings = get_settings()

ALGORITHM = "HS256"

# bcrypt silently truncates anything past 72 bytes. Reject instead of truncating,
# because two different long passwords could otherwise authenticate each other.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


class PasswordTooLongError(ValueError):
    """Raised when a password exceeds bcrypt's 72-byte limit."""


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes when UTF-8 encoded."
        )
    salt = bcrypt.gensalt(rounds=_settings.bcrypt_rounds)
    return bcrypt.hashpw(encoded, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time password verification. Returns False on malformed hashes."""
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Issue a signed access token for the given subject (user id)."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_settings.access_token_ttl_minutes)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _settings.resolved_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a token. Returns None when invalid or expired."""
    try:
        return jwt.decode(token, _settings.resolved_secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


class SlidingWindowLimiter:
    """In-process sliding-window rate limiter.

    Deliberately simple: a deque of timestamps per key. It is per-process, so N
    workers give N times the budget. That limitation is documented rather than
    hidden; Redis is the production answer.
    """

    def __init__(self, limit: int, window_seconds: int = 60, enabled: bool = True) -> None:
        self.limit = limit
        self.window = window_seconds
        self.enabled = enabled
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        if not self.enabled:
            return True, 0

        now = time.monotonic()
        cutoff = now - self.window
        bucket = self._hits[key]

        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = max(1, int(bucket[0] + self.window - now) + 1)
            return False, retry_after

        bucket.append(now)
        return True, 0

    def reset(self) -> None:
        """Clear all buckets. Used by tests."""
        self._hits.clear()
