"""
tests/test_conflict_detection.py
----------------------------------
Tests that conflict detection correctly identifies when a destination
file already exists and handles it by skipping (not overwriting).
"""

import pytest
import asyncio
from pathlib import Path

from app.models.rename_plan import RenamePlan
from app.services.copy_executor import execute_copies


async def collect(gen):
    results = []
    async for item in gen:
        results.append(item)
    return results


def _plan(src: str, dest: str, is_conflict: bool = False) -> RenamePlan:
    return RenamePlan(
        id=2,
        local_audiobook_id=2,
        batch_run_id=1,
        full_destination_path=dest,
        destination_dir=str(Path(dest).parent),
        destination_filename=Path(dest).name,
        is_conflict=is_conflict,
        is_dry_run=False,
        user_approved=True,
    )


class TestConflictDetection:
    def test_existing_destination_is_skipped(self, tmp_path):
        """If destination already exists, copy must be skipped."""
        src  = tmp_path / "source.m4b"
        dest = tmp_path / "output" / "Title.m4b"

        src.write_bytes(b"\x00" * 256)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"\xff" * 256)  # destination pre-exists

        original_content = dest.read_bytes()

        import app.services.copy_executor as ce

        async def fake_get_source(_):
            return str(src)

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([_plan(str(src), str(dest))], 1, is_dry_run=False))
            )
        finally:
            pass

        # Destination content must be unchanged (not overwritten)
        assert dest.read_bytes() == original_content

        # Event must report skipped_conflict
        assert any(e["status"] == "skipped_conflict" for e in events)

    def test_no_conflict_when_dest_absent(self, tmp_path):
        """If destination does not exist, copy should succeed."""
        src  = tmp_path / "source.m4b"
        dest = tmp_path / "output" / "Title.m4b"
        src.write_bytes(b"\x00" * 256)

        import app.services.copy_executor as ce

        async def fake_get_source(_):
            return str(src)

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([_plan(str(src), str(dest))], 1, is_dry_run=False))
            )
        finally:
            pass

        assert dest.exists()
        assert any(e["status"] == "success" for e in events)

    def test_summary_counts_conflict_correctly(self, tmp_path):
        src  = tmp_path / "source.m4b"
        dest = tmp_path / "output" / "Title.m4b"
        src.write_bytes(b"\x00" * 128)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"\xff" * 128)

        import app.services.copy_executor as ce

        async def fake_get_source(_):
            return str(src)

        ce._get_source_path = fake_get_source
        try:
            events = asyncio.get_event_loop().run_until_complete(
                collect(ce.execute_copies([_plan(str(src), str(dest))], 1, is_dry_run=False))
            )
        finally:
            pass

        final = events[-1]
        assert final["done"] is True
        assert final["summary"]["skipped_conflicts"] == 1
        assert final["summary"]["copied"] == 0
