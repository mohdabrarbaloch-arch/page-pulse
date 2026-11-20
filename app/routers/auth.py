"""Authentication endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.security import (
    PasswordTooLongError,
    SlidingWindowLimiter,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_settings = get_settings()
_login_limiter = SlidingWindowLimiter(
    limit=_settings.auth_rate_limit_per_minute,
    enabled=_settings.rate_limit_enabled,
)


def _client_key(request: Request) -> str:
    """Rate-limit key. Prefers the forwarded address behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: DbSession) -> TokenResponse:
    """Create an account and return a token so the client can go straight in."""
    allowed, retry_after = _login_limiter.check(f"register:{_client_key(request)}")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many sign-up attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    email = payload.email.strip().lower()

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists. Sign in instead.",
        )

    try:
        password_hash = hash_password(payload.password)
    except PasswordTooLongError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    user = User(
        email=email,
        password_hash=password_hash,
        display_name=payload.display_name.strip() or email.split("@")[0],
        # The first account on a fresh install is the admin. This is a local-first
        # app; a public deployment should set the flag explicitly instead.
        is_admin=db.scalar(select(User.id).limit(1)) is None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        expires_in=_settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """Exchange credentials for an access token."""
    allowed, retry_after = _login_limiter.check(f"login:{_client_key(request)}")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many sign-in attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))

    # One message for both "no such account" and "wrong password", so the
    # endpoint cannot be used to enumerate which emails are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(
        access_token=token,
        expires_in=_settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """The signed-in account."""
    return UserOut.model_validate(user)
