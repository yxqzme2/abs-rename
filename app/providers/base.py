"""
app/providers/base.py
---------------------
Abstract base class for metadata providers.

Any future provider (e.g. Google Books, OpenLibrary) must implement
this interface. The rest of the app only speaks to this interface —
never directly to AudNexus or any other provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from app.models.candidate import AudibleCandidate


class BaseMetadataProvider(ABC):
    """Interface that all metadata providers must implement."""

    @abstractmethod
    async def search_books(
        self,
        title: str,
        author: str | None = None,
    ) -> list[AudibleCandidate]:
        """
        Search for audiobooks matching the given title (and optionally author).

        Args:
            title:  title string to search for
            author: optional author name to narrow results

        Returns:
            List of candidates, ordered by relevance (best first).
            Empty list if nothing found or provider is unavailable.
        """
        ...

    @abstractmethod
    async def get_book_by_asin(self, asin: str) -> AudibleCandidate | None:
        """
        Fetch full details for a specific book by its Audible ASIN.

        Returns:
            AudibleCandidate if found, None otherwise.
        """
        ...
