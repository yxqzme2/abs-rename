"""
tests/test_dry_run.py
----------------------
Tests that dry-run mode performs all validation but never touches
the filesystem (no files are created or copied).
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path

from app.models.rename_plan import RenamePlan
from app.services.copy_executor import execute_copies


# Helper to drain an async generator into a list
async def collect(gen):
    results = []
    async for item in gen:
        results.append(item)
    return results


@pytest.fixture
def tmp_src(tmp_path):
    """Create a small fake .m4b source file."""
    src = tmp_path / "test.m4b"
    src.write_bytes(b"\x00" * 128)
    return str(src)


@pytest.fixture
def tmp_dest(tmp_path):
    return str(tmp_path / "output" / "Author" / "Title" / "Title.m4b")


def _plan(src: str, dest: str, approved: bool = True) -> RenamePlan:
    return RenamePlan(
        id=1,
        local_audiobook_id=1,
        batch_run_id=1,
        template_used="{author}/{title}",
        destination_dir=str(Path(dest).parent),
        destination_filename=Path(dest).name,
        full_destination_path=dest,
        is_conflict=False,
        is_dry_run=True,
        user_approved=approved,
    )


class TestDryRun:
    def test_dry_run_no_files_created(self, tmp_src, tmp_dest):
        """Dry-run must not create the destination file."""
        plan = _plan(tmp_src, tmp_dest, approved=True)

        # Patch _get_source_path to return our tmp source
        import app.services.copy_executor as ce
        original = ce._get_source_path

        async def fake_get_source(local_audiobook_id):
            return tmp_src

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([plan], batch_run_id=1, is_dry_run=True))
            )
        finally:
            ce._get_source_path = original

        # Destination must NOT exist
        assert not Path(tmp_dest).exists()

        # Event status must be dry_run
        assert any(e["status"] == "dry_run" for e in events)

    def test_dry_run_event_has_correct_fields(self, tmp_src, tmp_dest):
        plan = _plan(tmp_src, tmp_dest, approved=True)

        import app.services.copy_executor as ce

        async def fake_get_source(local_audiobook_id):
            return tmp_src

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([plan], batch_run_id=1, is_dry_run=True))
            )
        finally:
            ce._get_source_path = ce._get_source_path

        assert len(events) > 0
        last = events[-1]
        assert last["done"] is True
        assert last["summary"] is not None
        assert "dry_run" in last["summary"]

    def test_unapproved_plan_not_executed(self, tmp_src, tmp_dest):
        """Plans with user_approved=False must be skipped entirely."""
        plan = _plan(tmp_src, tmp_dest, approved=False)

        import app.services.copy_executor as ce

        async def fake_get_source(local_audiobook_id):
            return tmp_src

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([plan], batch_run_id=1, is_dry_run=True))
            )
        finally:
            pass

        # No events for unapproved plans — only the empty-queue done event
        assert len(events) == 1
        assert events[0]["total"] == 0

    def test_empty_plan_list_returns_done_event(self):
        import app.services.copy_executor as ce
        events = asyncio.get_event_loop().run_until_complete(
            collect(ce.execute_copies([], batch_run_id=1, is_dry_run=True))
        )
        assert len(events) == 1
        assert events[0]["done"] is True
        assert events[0]["total"] == 0
