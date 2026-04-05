"""
tests/conftest.py
-----------------
Shared pytest configuration and fixtures.

Sets the DATABASE_PATH to an in-memory / temp path so tests never
touch the real database file.
"""

import os
import pytest


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """
    Redirect the database to a temp file for each test that uses the DB.
    Tests that don't import from app.db won't be affected.
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    # Also patch the config module directly since it reads env at import time
    import app.config as cfg
    monkeypatch.setattr(cfg, "DATABASE_PATH", db_file)
