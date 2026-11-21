"""Database engine, session factory and declarative base."""
from __future__ import annotations
import platform
from collections.abc import Iterator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from app.config import get_settings

_settings = get_settings()


def _resolve_database_url(url: str) -> str:
    """Ensure the SQLite file lives in a writable location.

    - On Vercel (Linux): filesystem is read-only except /tmp, so redirect
      any SQLite path that isn't already in /tmp.
    - Locally (Windows/macOS): keep the original path so the dev DB persists.
    - If DATABASE_URL points to PostgreSQL, return it unchanged.
    """
    if not url.startswith("sqlite"):
        return url
    if platform.system() == "Linux":
        path_part = url[len("sqlite:///"):]
        if not path_part.startswith("/tmp"):
            return "sqlite:////tmp/pagepulse.db"
    return url


_database_url = _resolve_database_url(_settings.database_url)

# check_same_thread=False is required for SQLite behind a thread pool; the app
# still serialises writes through short-lived sessions.
_connect_args = {"check_same_thread": False} if _database_url.startswith("sqlite") else {}

engine: Engine = create_engine(
    _database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if _database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        """Enable foreign keys and WAL on SQLite.

        SQLite ignores foreign keys unless asked, which would let orphaned rows
        accumulate silently. WAL keeps reads from blocking the single writer.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
