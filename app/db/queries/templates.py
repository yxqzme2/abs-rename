"""
app/db/queries/templates.py
-----------------------------
DB helpers for UserTemplatePreference records.
"""

from __future__ import annotations

from datetime import datetime
from app.db.connection import get_db


async def list_templates() -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM user_template_preferences ORDER BY is_default DESC, id"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_default_template() -> dict | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM user_template_preferences WHERE is_default = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def save_template(name: str, template_string: str, set_default: bool = False) -> dict:
    async with get_db() as db:
        if set_default:
            await db.execute(
                "UPDATE user_template_preferences SET is_default = 0"
            )
        cursor = await db.execute(
            """
            INSERT INTO user_template_preferences
                (name, template_string, is_default, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, template_string, int(set_default), datetime.utcnow().isoformat()),
        )
        row_id = cursor.lastrowid

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM user_template_preferences WHERE id = ?", (row_id,)
        )
        row = await cursor.fetchone()
    return dict(row)


async def set_default_template(template_id: int) -> None:
    async with get_db() as db:
        await db.execute("UPDATE user_template_preferences SET is_default = 0")
        await db.execute(
            "UPDATE user_template_preferences SET is_default = 1 WHERE id = ?",
            (template_id,),
        )
