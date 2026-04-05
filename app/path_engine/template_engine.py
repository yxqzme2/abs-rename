"""
app/path_engine/template_engine.py
------------------------------------
Renders naming templates into destination folder paths and filenames.

Templates use Python-style format strings with named tokens:
  {author}, {title}, {series}, {series_index}, {series_index:02d},
  {year}, {narrator}, {asin}

The engine applies fallback rules when data is missing, then sanitizes
each path segment before returning the final paths.

ABS convention: the destination filename (without extension) must match
the innermost folder name. The engine returns both components so callers
can construct the full path.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.models.candidate import AudibleCandidate
from app.models.local_audiobook import LocalAudiobook, LocalMetadata
from app.path_engine.sanitizer import sanitize_segment, sanitize_path

logger = logging.getLogger(__name__)

# Token reference — used in the UI template guide
AVAILABLE_TOKENS = [
    "{author}",
    "{title}",
    "{series}",
    "{series_index}",
    "{series_index:02d}",
    "{year}",
    "{narrator}",
    "{asin}",
]

# Built-in predefined templates (label -> folder_template)
# The filename is always derived from the innermost folder segment.
PREDEFINED_TEMPLATES: list[dict] = [
    {
        "id": 1,
        "name": "ABS Series Format",
        "template": "{author}/{series}/{series_index:02d} - {title}",
        "default": True,
    },
    {
        "id": 2,
        "name": "ABS Standalone Format",
        "template": "{author}/{title}",
        "default": False,
    },
    {
        "id": 3,
        "name": "Series with Year",
        "template": "{author}/{series}/{series_index:02d} - {title} ({year})",
        "default": False,
    },
    {
        "id": 4,
        "name": "Flat Author/Title",
        "template": "{author}/{title}",
        "default": False,
    },
]


def _build_vars(
    audiobook: LocalAudiobook,
    metadata: LocalMetadata | None,
    candidate: AudibleCandidate | None,
) -> dict:
    """
    Build the substitution variable dict from all available data sources.
    Candidate data takes precedence over local tag data (it is the
    enriched, authoritative metadata).
    """
    # --- Author ---
    author = ""
    if candidate and candidate.first_author:
        author = candidate.first_author
    elif metadata and metadata.author_from_tags:
        author = metadata.author_from_tags
    author = author.strip() or "Unknown Author"

    # --- Title ---
    title = ""
    if candidate:
        title = candidate.title or ""
    if not title and metadata:
        title = metadata.title_from_tags or metadata.album_from_tags or ""
    if not title:
        title = Path(audiobook.filename).stem
    title = title.strip() or "Unknown Title"

    # --- Series ---
    series = ""
    if candidate and candidate.series_name:
        series = candidate.series_name
    elif metadata and metadata.series_from_tags:
        series = metadata.series_from_tags
    series = series.strip()

    # --- Series index ---
    series_index_raw: str | None = None
    series_index_float: float | None = None
    if candidate and candidate.series_position is not None:
        series_index_raw = candidate.series_position
        series_index_float = candidate.series_position_as_float()
    elif metadata and metadata.series_index_from_tags:
        series_index_raw = metadata.series_index_from_tags
        series_index_float = metadata.series_index_as_float()

    # --- Year ---
    year = ""
    if candidate:
        year = candidate.release_year or ""

    # --- Narrator ---
    narrator = ""
    if candidate and candidate.first_narrator:
        narrator = candidate.first_narrator
    elif metadata and metadata.narrator_from_tags:
        narrator = metadata.narrator_from_tags
    narrator = narrator.strip()

    # --- ASIN ---
    asin = (candidate.asin if candidate else "") or ""

    return {
        "author": author,
        "title": title,
        "series": series,
        "series_index": series_index_float,
        "series_index_raw": series_index_raw,
        "year": year,
        "narrator": narrator,
        "asin": asin,
        "_has_series": bool(series),
        "_has_series_index": series_index_float is not None,
    }


def _apply_fallbacks(template: str, vars: dict) -> str:
    """
    Pre-process the template before rendering:
    - If {series} is missing, replace series path segment with "Standalone"
    - If {series_index} is missing, strip the series_index prefix from the segment
    - If {year} is empty, remove the "(year)" fragment

    This avoids ugly "{series}" or "None" appearing in rendered paths.
    """
    result = template

    # Handle missing year — remove "(year)" fragment
    if not vars["year"]:
        result = re.sub(r"\s*\(\{year\}\)", "", result)
        result = re.sub(r"\{year\}", "", result)

    # Handle missing narrator
    if not vars["narrator"]:
        result = re.sub(r"\{narrator\}", "", result)

    # Handle missing ASIN
    if not vars["asin"]:
        result = re.sub(r"\{asin\}", "", result)

    # Handle missing series — use the title as the series folder
    if not vars["_has_series"]:
        result = re.sub(r"\{series\}", vars["title"], result)
        # Remove any series_index prefix — no index for a standalone book
        result = re.sub(r"\{series_index(?::[^}]+)?\}\s*-\s*", "", result)

    # Handle missing series index (series present but no index)
    elif not vars["_has_series_index"]:
        result = re.sub(r"\{series_index(?::[^}]+)?\}\s*-\s*", "", result)

    # Clean up any double slashes or trailing slashes from removals
    result = re.sub(r"/+", "/", result).strip("/")

    return result


def _format_token(template: str, vars: dict) -> str:
    """
    Substitute tokens in the template. Handles {series_index:02d} format spec.
    Plain {token} substitutions use string replacement.
    """
    result = template

    # Handle {series_index:02d} or {series_index:Nd} format specs
    def replace_series_index(match: re.Match) -> str:
        fmt_spec = match.group(1)  # e.g. "02d"
        val = vars.get("series_index")
        if val is None:
            return ""
        try:
            # Convert to int if the format is integer-based
            if "d" in fmt_spec:
                return format(int(val), fmt_spec)
            return format(val, fmt_spec)
        except (ValueError, TypeError):
            return str(val)

    result = re.sub(
        r"\{series_index:([^}]+)\}",
        replace_series_index,
        result,
    )

    # Replace remaining {series_index} without format spec
    if "{series_index}" in result:
        val = vars.get("series_index")
        result = result.replace("{series_index}", str(int(val)) if val is not None else "")

    # Replace all other simple tokens
    for key in ["author", "title", "series", "year", "narrator", "asin"]:
        result = result.replace(f"{{{key}}}", str(vars.get(key, "")))

    return result


def render_template(
    template: str,
    audiobook: LocalAudiobook,
    metadata: LocalMetadata | None,
    candidate: AudibleCandidate | None,
    extension: str = ".m4b",
) -> tuple[str, str, str]:
    """
    Render the template into a destination folder path, filename, and full path.

    Two template syntaxes are supported:

    1. Simple (ABS default convention):
          {author}/{series}/{series_index:02d} - {title}
       The last path segment becomes both the innermost folder name AND the
       filename.  The file ends up inside a folder of the same name:
          Author/Series/01 - Title/01 - Title.m4b

    2. Explicit filename with | separator:
          {author}/{series}/Book {series_index:02d}|{series} Book {series_index:02d}
       Everything before | is the folder path; everything after | is the
       filename (extension is appended automatically).  The file is placed
       directly inside the last folder segment — no extra sub-folder:
          Author/Series/Book 01/Series Book 01.m4b

    Args:
        template:   template string (with optional | separator)
        audiobook:  the source file record
        metadata:   extracted tag data (may be None)
        candidate:  matched provider candidate (may be None)
        extension:  file extension to append (default ".m4b")

    Returns:
        (destination_dir, destination_filename, full_destination_path)
        All paths use forward slashes as separators.
    """
    vars = _build_vars(audiobook, metadata, candidate)

    # --- Split on | to get separate folder and filename templates ---
    if "|" in template:
        folder_template, filename_template = template.split("|", 1)
    else:
        folder_template, filename_template = template, None

    # --- Standalone override for | templates ---
    # When a | template is series-based (contains {series}) but the book has
    # no series, the literal text around the tokens (e.g. "Book ") produces
    # orphaned segments. Use the title as both the folder and filename instead.
    if filename_template is not None and not vars["_has_series"]:
        folder_template   = "{author}/{title}"
        filename_template = "{title}"

    # Apply fallbacks and render the folder portion
    processed_folder = _apply_fallbacks(folder_template.strip(), vars)
    rendered_folder  = _format_token(processed_folder, vars)

    folder_segments = [seg for seg in rendered_folder.split("/") if seg.strip()]
    sanitized_folder = [sanitize_segment(seg.strip()) for seg in folder_segments]

    if not sanitized_folder:
        sanitized_folder = ["Unknown Author", "Unknown Title"]

    destination_dir = "/".join(sanitized_folder)

    if filename_template is not None:
        # Explicit filename — render it independently
        processed_fname = _apply_fallbacks(filename_template.strip(), vars)
        rendered_fname  = _format_token(processed_fname, vars)
        destination_filename = sanitize_segment(rendered_fname.strip()) + extension
    else:
        # ABS convention: filename = last folder segment
        destination_filename = sanitized_folder[-1] + extension

    full_destination_path = destination_dir + "/" + destination_filename

    logger.debug(
        "Template render: '%s' -> dir='%s' file='%s'",
        template, destination_dir, destination_filename,
    )

    return destination_dir, destination_filename, full_destination_path


def render_example(template: str) -> str:
    """
    Render a template with sample data for preview in the UI.
    """
    from app.models.local_audiobook import LocalAudiobook, LocalMetadata, ScanStatus
    from app.models.candidate import AudibleCandidate

    sample_audiobook = LocalAudiobook(
        batch_run_id=0,
        source_path="/sample/The Name of the Wind.m4b",
        filename="The Name of the Wind.m4b",
        folder_path="/sample",
        file_size=0,
        scan_status=ScanStatus.SCANNED,
    )
    sample_metadata = LocalMetadata(
        local_audiobook_id=0,
        title_from_tags="The Name of the Wind",
        author_from_tags="Patrick Rothfuss",
        narrator_from_tags="Nick Podehl",
        series_from_tags="The Kingkiller Chronicle",
        series_index_from_tags="1",
        duration_seconds=47700,
    )
    sample_candidate = AudibleCandidate(
        asin="B002V0QUOC",
        title="The Name of the Wind",
        authors=["Patrick Rothfuss"],
        narrators=["Nick Podehl"],
        series_name="The Kingkiller Chronicle",
        series_position="1",
        runtime_seconds=47700,
        release_date="2007-03-27",
    )

    _, _, full = render_template(
        template, sample_audiobook, sample_metadata, sample_candidate
    )
    return full
