"""
app/api/routes/history.py
--------------------------
Batch run history endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from app.db.queries.batch_runs import list_batch_runs, get_batch_run_detail

router = APIRouter()


@router.get("/history")
async def get_history():
    """List past batch runs, newest first."""
    runs = await list_batch_runs(limit=100)
    return {"runs": runs}


@router.get("/history/{batch_run_id}")
async def get_run_detail(batch_run_id: int):
    """Get full detail for one batch run including all copy operations."""
    run = await get_batch_run_detail(batch_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    return run
