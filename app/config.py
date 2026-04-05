"""
app/config.py
-------------
Central configuration loaded from environment variables / .env file.
All other modules import settings from here — never read os.environ directly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level above this file)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    return _get(key, str(default)).lower() in ("true", "1", "yes")


# --- Database ---
DATABASE_PATH: str = _get("DATABASE_PATH") or str(
    Path(__file__).parent.parent / "abs_rename.db"
)

# --- Output ---
DEFAULT_OUTPUT_FOLDER: str = _get("DEFAULT_OUTPUT_FOLDER")

# --- AudNexus provider ---
AUDNEXUS_BASE_URL: str = _get("AUDNEXUS_BASE_URL", "https://api.audnexus.app")
AUDNEXUS_REGION: str = _get("AUDNEXUS_REGION", "us")
AUDNEXUS_REQUEST_DELAY_MS: int = _get_int("AUDNEXUS_REQUEST_DELAY_MS", 400)

# --- Confidence thresholds ---
CONFIDENCE_AUTO_APPROVE: float = float(_get("CONFIDENCE_AUTO_APPROVE", "90"))
CONFIDENCE_REVIEW_REQUIRED: float = float(_get("CONFIDENCE_REVIEW_REQUIRED", "75"))

# --- Server ---
HOST: str = _get("HOST", "127.0.0.1")
PORT: int = _get_int("PORT", 8000)

# --- Logging ---
DEBUG: bool = _get_bool("DEBUG", False)

# --- Scoring weights (adjustable here without touching scorer logic) ---
SCORE_WEIGHTS: dict[str, float] = {
    "title": 0.45,
    "author": 0.25,
    "narrator": 0.10,
    "series": 0.15,
    "runtime": 0.05,
}

# Runtime tolerance bands (fraction of total duration)
RUNTIME_TOLERANCE_FULL: float = 0.05   # within ±5%  -> full runtime score
RUNTIME_TOLERANCE_PARTIAL: float = 0.15  # within ±15% -> partial score (0.5)
# beyond ±15% -> 0
