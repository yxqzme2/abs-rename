"""
app/providers/audnexus.py
--------------------------
Metadata provider: two-step approach.

SEARCH  → Audible's unofficial catalog API (api.audible.com)
          No authentication required. Returns title, author, series,
          runtime, ASIN and enough metadata to populate candidates.

ENRICH  → AudNexus community API (api.audnexus.app)
          Used for get_book_by_asin() — richer narrator/series data.

Why two sources:
  AudNexus does not expose a title/author search endpoint. It is
  ASIN-based only. The Audible catalog API has a public search endpoint
  that works without login for basic queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import (
    AUDNEXUS_BASE_URL,
    AUDNEXUS_REGION,
    AUDNEXUS_REQUEST_DELAY_MS,
)
from app.models.candidate import AudibleCandidate
from app.providers.base import BaseMetadataProvider

logger = logging.getLogger(__name__)

_MAX_RETRIES     = 3
_RETRY_BASE_DELAY = 1.0
_TIMEOUT         = 15.0

# Audible catalog API base URLs keyed by region code
_AUDIBLE_REGION_URLS: dict[str, str] = {
    "us": "https://api.audible.com",
    "uk": "https://api.audible.co.uk",
    "au": "https://api.audible.com.au",
    "ca": "https://api.audible.ca",
    "de": "https://api.audible.de",
    "fr": "https://api.audible.fr",
    "it": "https://api.audible.it",
    "es": "https://api.audible.es",
    "jp": "https://api.audible.co.jp",
    "in": "https://api.audible.in",
}

_AUDIBLE_SEARCH_PATH = "/1.0/catalog/products"
_AUDIBLE_SEARCH_RESPONSE_GROUPS = (
    "media,contributors,series,product_desc,product_extended_attrs"
)


def _audible_base_url(region: str) -> str:
    return _AUDIBLE_REGION_URLS.get(region.lower(), _AUDIBLE_REGION_URLS["us"])


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _extract_names(items: list | None) -> list[str]:
    """Extract name strings from a list of name dicts or plain strings."""
    if not items:
        return []
    names = []
    for item in items:
        if isinstance(item, dict):
            name = (item.get("name") or item.get("authorName")
                    or item.get("narratorName") or "")
        else:
            name = str(item)
        if name.strip():
            names.append(name.strip())
    return names


def _parse_runtime(minutes: Any) -> float | None:
    if minutes is None:
        return None
    try:
        return float(minutes) * 60.0
    except (TypeError, ValueError):
        return None


def _map_audible_product(raw: dict) -> AudibleCandidate | None:
    """
    Map a single product from the Audible catalog API response to an
    AudibleCandidate.

    Audible catalog response fields used:
      asin, title, authors[], narrators[], series[]{title, sequence},
      runtime_length_min, language, issue_date, product_images{500}
    """
    asin = raw.get("asin", "").strip()
    if not asin:
        return None

    title = raw.get("title") or raw.get("product_title") or ""

    # Series: Audible returns a list; use the first entry
    series_name: str | None = None
    series_pos:  str | None = None
    series_list = raw.get("series") or []
    if isinstance(series_list, list) and series_list:
        s = series_list[0]
        if isinstance(s, dict):
            series_name = s.get("title") or s.get("seriesName")
            series_pos  = s.get("sequence") or s.get("position")

    # Image URL
    images = raw.get("product_images") or {}
    image_url = (
        images.get("500") or images.get("1024")
        or images.get("240") or None
    )

    # Release date
    release_date = raw.get("issue_date") or raw.get("release_date")
    if release_date and len(str(release_date)) > 10:
        release_date = str(release_date)[:10]

    return AudibleCandidate(
        provider_id="audible_catalog",
        asin=asin,
        title=title,
        subtitle=raw.get("subtitle"),
        authors=_extract_names(raw.get("authors")),
        narrators=_extract_names(raw.get("narrators")),
        series_name=series_name,
        series_position=str(series_pos).strip() if series_pos is not None else None,
        runtime_seconds=_parse_runtime(raw.get("runtime_length_min")),
        image_url=image_url,
        language=raw.get("language"),
        release_date=release_date,
        raw_payload_json=json.dumps(raw, ensure_ascii=False),
    )


def _map_audnexus_book(raw: dict) -> AudibleCandidate | None:
    """Map a response from AudNexus /books/{asin} to AudibleCandidate."""
    asin = raw.get("asin", "").strip()
    if not asin:
        return None

    series_name: str | None = None
    series_pos:  str | None = None
    sp = raw.get("seriesPrimary") or {}
    if isinstance(sp, dict):
        series_name = sp.get("name")
        series_pos  = sp.get("position")
    elif isinstance(sp, list) and sp:
        series_name = sp[0].get("name")
        series_pos  = sp[0].get("position")

    release_date = raw.get("releaseDate")
    if release_date and len(str(release_date)) > 10:
        release_date = str(release_date)[:10]

    return AudibleCandidate(
        provider_id="audnexus",
        asin=asin,
        title=raw.get("title") or "",
        subtitle=raw.get("subtitle"),
        authors=_extract_names(raw.get("authors")),
        narrators=_extract_names(raw.get("narrators")),
        series_name=series_name,
        series_position=str(series_pos).strip() if series_pos is not None else None,
        runtime_seconds=_parse_runtime(raw.get("runtimeLengthMin")),
        image_url=raw.get("image"),
        language=raw.get("language"),
        release_date=release_date,
        raw_payload_json=json.dumps(raw, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class AudNexusProvider(BaseMetadataProvider):
    """
    Two-source metadata provider.
    - search_books      → Audible catalog API (title/author search, no auth)
    - get_book_by_asin  → AudNexus community API (rich ASIN lookup)
    """

    def __init__(self) -> None:
        self._delay = AUDNEXUS_REQUEST_DELAY_MS / 1000.0
        audible_base = _audible_base_url(AUDNEXUS_REGION)

        # Client for Audible catalog search
        self._audible = httpx.AsyncClient(
            base_url=audible_base,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            },
            follow_redirects=True,
        )

        # Client for AudNexus ASIN lookups
        self._audnexus = httpx.AsyncClient(
            base_url=AUDNEXUS_BASE_URL,
            timeout=_TIMEOUT,
            headers={"User-Agent": "abs-rename/1.0 (personal use)"},
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._audible.aclose()
        await self._audnexus.aclose()

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict | None = None,
        label: str = "API",
    ) -> Any:
        """Shared GET with retry + exponential backoff."""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.get(path, params=params)

                if response.status_code == 429:
                    wait = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning("%s rate limit. Waiting %.1fs.", label, wait)
                    await asyncio.sleep(wait)
                    continue

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException:
                logger.warning("%s timeout (attempt %d/%d): %s", label, attempt, _MAX_RETRIES, path)
            except httpx.HTTPStatusError as exc:
                logger.warning("%s HTTP error (attempt %d/%d): %s", label, attempt, _MAX_RETRIES, exc)
            except Exception as exc:
                logger.warning("%s error (attempt %d/%d): %s", label, attempt, _MAX_RETRIES, exc)

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BASE_DELAY * attempt)

        logger.error("%s failed after %d attempts: %s", label, _MAX_RETRIES, path)
        return None

    async def search_books(
        self,
        title: str,
        author: str | None = None,
    ) -> list[AudibleCandidate]:
        """
        Search the Audible catalog API by title (and optionally author).
        Returns up to 10 candidates ordered by Audible's relevance ranking.
        No authentication required.
        """
        params: dict = {
            "title":                title,
            "num_results":          "10",
            "response_groups":      _AUDIBLE_SEARCH_RESPONSE_GROUPS,
            "products_sort_by":     "Relevance",
        }
        if author:
            params["author"] = author

        logger.debug("Audible search: title=%r author=%r", title, author)
        await asyncio.sleep(self._delay)

        data = await self._get(
            self._audible,
            _AUDIBLE_SEARCH_PATH,
            params=params,
            label="Audible",
        )

        if not data:
            logger.debug("Audible returned no data for %r", title)
            return []

        products = data.get("products") or []
        candidates = []
        for raw in products:
            if not isinstance(raw, dict):
                continue
            c = _map_audible_product(raw)
            if c:
                candidates.append(c)

        logger.debug("Audible returned %d candidate(s) for %r", len(candidates), title)
        return candidates

    async def get_book_by_asin(self, asin: str) -> AudibleCandidate | None:
        """
        Fetch full book details by ASIN from AudNexus.
        Falls back to the Audible catalog API if AudNexus returns nothing.
        """
        logger.debug("AudNexus ASIN lookup: %s", asin)
        await asyncio.sleep(self._delay)

        # Try AudNexus first
        data = await self._get(
            self._audnexus,
            f"/books/{asin}",
            params={"region": AUDNEXUS_REGION},
            label="AudNexus",
        )
        if data and isinstance(data, dict):
            c = _map_audnexus_book(data)
            if c:
                return c

        # Fall back to Audible catalog API
        logger.debug("AudNexus miss for %s, trying Audible catalog", asin)
        data = await self._get(
            self._audible,
            f"/1.0/catalog/products/{asin}",
            params={"response_groups": _AUDIBLE_SEARCH_RESPONSE_GROUPS},
            label="Audible",
        )
        if data and isinstance(data, dict):
            raw = data.get("product") or data
            return _map_audible_product(raw)

        return None
