"""
app/db/connection.py
--------------------
SQLite connection management using aiosqlite (async).

Usage:
    from app.db.connection import get_db, init_db

    # In FastAPI lifespan / startup:
    await init_db()

    # In a route or service:
    async with get_db() as db:
        await db.execute("SELECT ...")
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from app.config import DATABASE_PATH
from app.db.schema import SCHEMA_STATEMENTS

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """
    Bootstrap the database schema on startup.
    Safe to call every time — all statements use IF NOT EXISTS / OR IGNORE.
    """
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Initializing database at %s", db_path)

    async with aiosqlite.connect(str(db_path)) as db:
        # Enable WAL mode for better concurrent read performance
        await db.execute("PRAGMA journal_mode=WAL")
        # Enforce foreign key constraints
        await db.execute("PRAGMA foreign_keys=ON")

        for statement in SCHEMA_STATEMENTS:
            try:
                await db.execute(statement)
            except aiosqlite.OperationalError as exc:
                logger.error("Schema error: %s\nStatement: %s", exc, statement[:80])
                raise

        await db.commit()

    logger.info("Database ready.")


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Async context manager that yields a database connection.
    Foreign keys are enabled and the connection is committed/closed on exit.

    Example:
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM batch_runs")
            rows = await cursor.fetchall()
    """
    db_path = Path(DATABASE_PATH)
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row  # rows accessible by column name
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
