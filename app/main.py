"""
app/main.py
-----------
FastAPI application entry point.

Startup:
  - Initializes the SQLite database
  - Mounts static files and Jinja2 templates
  - Registers all API routers under /api
  - Serves the UI pages for all non-API routes

Run locally:
  cd H:/ABS_Rename
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import HOST, PORT
from app.db.connection import init_db
from app.utils.logging import setup_logging

# Import all route modules
from app.api.routes import scan, match, copy, templates, history

# --- Logging setup (before anything else) ---
setup_logging()

import logging
logger = logging.getLogger(__name__)

# --- Base paths ---
BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup."""
    logger.info("Starting ABS Rename...")
    await init_db()
    logger.info("ABS Rename ready at http://%s:%d", HOST, PORT)
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="ABS Rename",
    description="Local audiobook metadata lookup and copy/organize tool for Audiobookshelf.",
    version="1.0.0",
    lifespan=lifespan,
)

# --- Static files ---
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

# --- Jinja2 templates ---
jinja = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- API routes (all under /api prefix) ---
app.include_router(scan.router,      prefix="/api", tags=["scan"])
app.include_router(match.router,     prefix="/api", tags=["match"])
app.include_router(copy.router,      prefix="/api", tags=["copy"])
app.include_router(templates.router, prefix="/api", tags=["templates"])
app.include_router(history.router,   prefix="/api", tags=["history"])


# ---------------------------------------------------------------------------
# UI page routes — serve HTML templates
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def page_scan(request: Request):
    """Main scan page."""
    return jinja.TemplateResponse("scan.html", {"request": request})


@app.get("/results/{batch_run_id}", response_class=HTMLResponse)
async def page_results(request: Request, batch_run_id: int):
    """Results / review page for a specific batch run."""
    return jinja.TemplateResponse(
        "results.html",
        {"request": request, "batch_run_id": batch_run_id},
    )


@app.get("/execute/{batch_run_id}", response_class=HTMLResponse)
async def page_execute(request: Request, batch_run_id: int):
    """Execute page — shows approved plans and runs the copy."""
    return jinja.TemplateResponse(
        "execute.html",
        {"request": request, "batch_run_id": batch_run_id},
    )


@app.get("/history", response_class=HTMLResponse)
async def page_history(request: Request):
    """Batch history page."""
    return jinja.TemplateResponse("history.html", {"request": request})


@app.get("/history/{batch_run_id}", response_class=HTMLResponse)
async def page_history_detail(request: Request, batch_run_id: int):
    """Detail view for a past batch run."""
    return jinja.TemplateResponse(
        "history_detail.html",
        {"request": request, "batch_run_id": batch_run_id},
    )


@app.get("/templates-settings", response_class=HTMLResponse)
async def page_templates(request: Request):
    """Template settings page."""
    return jinja.TemplateResponse("template_settings.html", {"request": request})
