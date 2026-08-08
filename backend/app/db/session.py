"""Async SQLAlchemy engine and session factory.

Postgres in production, SQLite for zero-setup local development. The only
behavioural difference we rely on is enabling SQLite's foreign key enforcement,
which is off by default and would otherwise let dev accept rows that Postgres
rejects.
"""

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

logger = logging.getLogger(__name__)

settings = get_settings()
_url = settings.sqlalchemy_url
_is_sqlite = _url.startswith("sqlite")

engine = create_async_engine(
    _url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    # SQLite's async driver does not support these pool options.
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}),
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep objects usable after commit for serialization
    autoflush=False,
)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):  # noqa: ANN001
        """SQLite ignores foreign keys unless asked. Without this, ON DELETE
        CASCADE silently does nothing and dev diverges from production."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that always closes.

    Rolls back on an exception so a failed request can never leave a partial
    write pending on a pooled connection handed to the next request.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create tables that don't exist yet.

    Fine for the first deploy and for local development. Once there is
    production data, schema changes must go through Alembic instead: this call
    only ever creates, it never alters an existing table.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database ready (%s)", "sqlite" if _is_sqlite else "postgres")


async def dispose_db() -> None:
    await engine.dispose()
