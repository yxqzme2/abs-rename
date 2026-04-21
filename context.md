# ABS Rename — Project Context

## Overview

**ABS Rename** is a local-first web application for scanning, identifying, and organizing `.m4b` audiobook files for import into [Audiobookshelf](https://www.audiobookshelf.org/).

**Repository:** [yxqzme2/abs-rename](https://github.com/yxqzme2/abs-rename)

**Core workflow:**
1. User selects local folders containing `.m4b` audiobook files
2. App scans recursively and extracts embedded metadata (mutagen)
3. App searches AudNexus (free Audible metadata mirror) for matches
4. App scores candidates using weighted fuzzy matching
5. User reviews & approves matches (with dry-run safety)
6. App copies files into a clean folder structure based on naming templates
7. All operations logged to SQLite for auditing

**Key principle:** Originals are never modified or deleted — only copies are created.

---

## Deployment Modes

### Local Development
```bash
cd H:\ABS_Rename
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your DEFAULT_OUTPUT_FOLDER
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# Open http://127.0.0.1:8000
```

### Docker
```bash
# Build
bash scripts/docker/build.sh

# Run
docker run -p 8000:8000 \
  -v "C:\Audiobooks":/audiobooks \
  -v "C:\Audiobooks\Organized":/output \
  -v "C:\ABSData":/data \
  abs-rename
```

**Volume mappings:**
- `/audiobooks` — mount your source audiobook library (read-only recommended)
- `/output` — mount your destination folder for organized copies (read-write)
- `/data` — persistent SQLite database location

### Unraid/Container Orchestration
The `scripts/docker/unraid-template.xml` provides a preconfigured container template for Unraid:
- Web UI port configuration
- Persistent data volume for SQLite
- Environment variable defaults
- Documentation strings for each config

---

## Directory Tree & File Purposes

```
H:/ABS_Rename/
├── app/                              # FastAPI application root
│   ├── main.py                       # Entry point; routes & startup
│   ├── config.py                     # Centralized config from .env
│   │
│   ├── api/routes/                   # HTTP API endpoints
│   │   ├── scan.py                   # POST /api/scan — start scan
│   │   ├── match.py                  # Match/approve/search operations
│   │   ├── copy.py                   # Execute copy with SSE streaming
│   │   ├── templates.py              # Template CRUD & preview
│   │   └── history.py                # Past batch run queries
│   │
│   ├── services/                     # Core business logic
│   │   ├── scanner.py                # Recursive .m4b discovery
│   │   ├── metadata_reader.py        # mutagen tag extraction
│   │   ├── matcher.py                # Confidence scoring (weighted)
│   │   ├── preview_planner.py        # Build rename plans from matches
│   │   └── copy_executor.py          # File copy with progress (SSE)
│   │
│   ├── providers/                    # Pluggable metadata providers
│   │   ├── base.py                   # Abstract provider interface
│   │   └── audnexus.py               # AudNexus API implementation
│   │
│   ├── models/                       # Pydantic data models
│   │   ├── local_audiobook.py        # Scanned file record
│   │   ├── candidate.py              # Provider search result
│   │   ├── match_result.py           # Score & decision
│   │   ├── rename_plan.py            # Destination path + approval
│   │   ├── copy_operation.py         # Copy action log
│   │   ├── batch_run.py              # Session record
│   │   └── template_preference.py    # Saved naming templates
│   │
│   ├── db/                           # Data access layer
│   │   ├── connection.py             # aiosqlite connection manager
│   │   ├── schema.py                 # SQLite CREATE TABLE statements
│   │   └── queries/                  # Query helpers per domain
│   │       ├── batch_runs.py
│   │       ├── results.py
│   │       └── templates.py
│   │
│   ├── path_engine/                  # Path & template utilities
│   │   ├── template_engine.py        # Render templates with fallbacks
│   │   └── sanitizer.py              # Filename/path sanitization
│   │
│   ├── templates/                    # Jinja2 HTML pages
│   │   ├── scan.html                 # Main scan page
│   │   ├── results.html              # Results review page
│   │   ├── execute.html              # Copy execution page
│   │   ├── history.html              # Batch history list
│   │   ├── history_detail.html       # Past run details
│   │   └── template_settings.html    # Template config page
│   │
│   ├── static/                       # CSS & JavaScript
│   │   ├── css/app.css               # Styling
│   │   └── js/app.js                 # Client-side logic
│   │
│   └── utils/                        # Utility modules
│       ├── logging.py                # Structured logging setup
│       └── file_utils.py             # Path/filesystem helpers
│
├── tests/                            # pytest test suite
│   ├── test_sanitizer.py             # Filename sanitization tests
│   ├── test_template_engine.py       # Template rendering tests
│   ├── test_matcher.py               # Confidence scoring tests
│   ├── test_dry_run.py               # Dry-run mode tests
│   ├── test_conflict_detection.py    # Conflict detection tests
│   └── conftest.py                   # pytest fixtures & config
│
├── scripts/                          # Development & deployment utilities
│   ├── docker/
│   │   ├── build.sh                  # Docker build script
│   │   └── unraid-template.xml       # Unraid container template
│   └── dev/                          # (Reserved for future dev tools)
│
├── .env                              # Local environment config (gitignored)
├── .env.example                      # Template for .env setup
├── .gitignore                        # Git exclusions
├── Dockerfile                        # Container image spec
├── README.md                         # User guide & quick start
├── requirements.txt                  # Python dependencies
├── goal.md                           # Original design document
├── context.md                        # This file
└── abs_rename.db                     # SQLite database (local)
```

---

## Core Routes & APIs

### UI Pages (server-rendered)
- **GET `/`** → Scan page (enter sources, output, template, dry-run)
- **GET `/results/{batch_run_id}`** → Results review (approve/edit matches)
- **GET `/execute/{batch_run_id}`** → Execute copy (progress & summary)
- **GET `/history`** → Batch history list
- **GET `/history/{batch_run_id}`** → Detail view of past run
- **GET `/templates-settings`** → Template editor & preview

### API Endpoints (JSON)
- **POST `/api/scan`** → Start a new scan (returns `batch_run_id`)
- **POST `/api/match/approve`** → Approve a match
- **POST `/api/match/select`** → Pick alternate candidate
- **POST `/api/match/search-again`** → Custom search for one file
- **POST `/api/copy/execute/{batch_run_id}`** → Run copy (SSE stream)
- **GET `/api/templates`** → List all templates
- **POST `/api/templates`** → Create custom template
- **POST `/api/templates/preview`** → Preview template output
- **GET `/api/history/{batch_run_id}`** → Fetch past run details
- **GET `/api/history`** → List all past runs

---

## Key Systems & How They Work

### 1. File Scanning (`app/services/scanner.py`)
- Recursively walks user-selected folder(s)
- Finds all `.m4b` files
- Creates `LocalAudiobook` records in database
- Non-blocking on errors — logs warnings and continues
- Returns list of discovered files with paths

### 2. Metadata Extraction (`app/services/metadata_reader.py`)
- Uses **mutagen** to read ID3/MP4 tags from each `.m4b`
- Extracts: title, author, album, narrator, series, series_index, duration, cover presence
- Stores raw tag JSON for debugging
- Falls back gracefully if tags are missing or corrupted
- Attempts multiple tag field variants to handle different taggers

### 3. Search Query Derivation (Priority Order)
1. **Title + Author tags** (if both present) → most reliable
2. **Title tag only** (if author missing)
3. **Parsed filename** (strip extensions, noise patterns like "Unabridged", "Part 1")
4. **Parent folder name** (last resort)

All strategies logged so user knows which was used.

### 4. AudNexus Provider (`app/providers/audnexus.py`)
- **Free, no-auth** community API for Audible metadata
- **Endpoints used:**
  - `GET /books?title=...&author=...&region=us` → search candidates
  - `GET /books/{asin}` → fetch full details by ASIN
- **Rate limiting:** Configurable delay between requests (default 400ms)
- **Abstracted behind `BaseMetadataProvider`** — swappable for future providers
- Handles transient failures with exponential backoff
- Never crashes the scan; marks unmatched items instead

### 5. Confidence Scoring (`app/services/matcher.py`)

**Weighted scoring formula:**
```
overall_score = (
    title_score    × 0.45 +
    author_score   × 0.25 +
    narrator_score × 0.10 +
    series_score   × 0.15 +
    runtime_score  × 0.05
) × 100
```

**Scoring rules by field:**
- **Title/Author/Series**: RapidFuzz token_set_ratio (0–100)
- **Narrator**: Simple overlap check (0 or 1)
- **Runtime**: Tolerance bands:
  - ±5% of file duration → full score (1.0)
  - ±5–15% → partial score (0.5)
  - >15% difference → zero

**Confidence thresholds:**
- `≥90` — **High confidence** (pre-checked in UI)
- `75–89` — **Review suggested** (shown unchecked)
- `<75` — **Review required** (flagged in UI)
- **No match** → Item marked unmatched, excluded from copy plan

**Normalization (title/series only, for comparison):**
- Lowercase
- Strip punctuation & extra whitespace
- Remove noise: "unabridged", "audiobook", "a novel", "the complete"
- Strip part/book numbers: "book 2", "part 3" → ignored during comparison

### 6. Template Engine (`app/path_engine/template_engine.py`)

**Available tokens:**
- `{author}` — First author name
- `{title}` — Book title
- `{series}` — Series name
- `{series_index}` — Series position (supports format spec: `{series_index:02d}`)
- `{year}` — Publication year
- `{narrator}` — First narrator
- `{asin}` — Audible ASIN

**Fallback rules (applied at render time):**
- No series → replace segment with `Standalone`
- No series_index → omit numeric prefix entirely
- No author → `Unknown Author`
- No title → `Unknown Title`
- No year → omit year segment

**Predefined templates:**
1. **ABS Series Format** (default)
   ```
   {author}/{series}/{series_index:02d} - {title}
   ```
   Produces: `Patrick Rothfuss/The Kingkiller Chronicle/01 - The Name of the Wind/`

2. **ABS Standalone Format**
   ```
   {author}/{title}
   ```
   Produces: `Patrick Rothfuss/The Name of the Wind/`

3. **Series with Year**
   ```
   {author}/{series}/{series_index:02d} - {title} ({year})
   ```

4. **Flat Author/Title**
   ```
   {author}/{title}
   ```

Users can also create custom templates via the UI with live preview.

### 7. Path Sanitization (`app/path_engine/sanitizer.py`)

**Invalid characters (replaced with hyphen):**
- Windows: `\ / : * ? " < > |`
- Reserved names: `CON`, `PRN`, `AUX`, `NUL`, `COM1–9`, `LPT1–9` (prefixed with `_`)
- Trailing periods/spaces per segment
- Multiple consecutive spaces/hyphens collapsed

Ensures paths are valid on both Windows and Linux filesystems.

### 8. Copy Executor (`app/services/copy_executor.py`)

**Workflow:**
1. Validate source exists
2. Check destination parent is writable
3. Detect conflicts (file already exists)
4. Create destination directories as needed
5. Copy file with `shutil.copy2` (preserves metadata)
6. Stream per-item progress via SSE
7. Skip if destination exists (configurable conflict mode in v1)
8. Return summary: copied, skipped, errors

**Dry-run mode:**
- Performs all validations and planning
- Does NOT write to filesystem
- Returns what would be copied

### 9. Database (`app/db/`)

**aiosqlite** for async SQLite access. Schema initialized on startup.

**Core tables:**
- `local_audiobooks` — scanned files
- `local_metadata` — embedded tag data
- `audible_candidates` — provider search results
- `match_results` — scoring decisions
- `rename_plans` — destination paths & approval status
- `copy_operations` — execution log
- `batch_runs` — session records (scan + copy)
- `user_template_preferences` — saved naming templates

**Key fields:**
- `match_status` — auto | review_required | unmatched | user_selected
- `confidence_score` — 0–100 float
- `user_approved` — must be true before copy proceeds

### 10. Server-Sent Events (SSE) Streaming

The **copy executor** streams progress in real-time using FastAPI's `StreamingResponse`:
```json
data: {"index": 1, "total": 50, "file": "...", "status": "success"}
data: {"index": 2, "total": 50, "file": "...", "status": "success"}
...
```

Frontend listens and updates progress bar, per-item status, final summary.

---

## Configuration Reference (`.env`)

| Key | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `./abs_rename.db` | SQLite database location |
| `DEFAULT_OUTPUT_FOLDER` | _(empty)_ | Pre-fill output folder in UI |
| `AUDNEXUS_BASE_URL` | `https://api.audnexus.app` | Metadata API endpoint |
| `AUDNEXUS_REGION` | `us` | Audible region (us, uk, au, ca, de, fr, it, es, jp, in) |
| `AUDNEXUS_REQUEST_DELAY_MS` | `400` | Delay between API calls (ms) |
| `CONFIDENCE_AUTO_APPROVE` | `90` | Score threshold for pre-checking |
| `CONFIDENCE_REVIEW_REQUIRED` | `75` | Score threshold for "review required" flag |
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable verbose logging |

**Scoring weights** (hardcoded in `app/config.py`):
```python
{
    "title": 0.45,
    "author": 0.25,
    "narrator": 0.10,
    "series": 0.15,
    "runtime": 0.05,
}
```

Adjust `SCORE_WEIGHTS` in `config.py` to reweight the scoring formula.

---

## Build & Deploy

### Local Development
```bash
# Setup
python -m venv .venv
source .venv/bin/activate  # or: .venv\Scripts\activate (Windows)
pip install -r requirements.txt

# Run
cp .env.example .env
# Edit .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Docker
```bash
# Build
bash scripts/docker/build.sh
# or manually: docker build -t abs-rename .

# Run (local testing)
docker run -p 8000:8000 \
  -v "$(pwd)/data":/data \
  -v "/mnt/audiobooks":/audiobooks \
  -v "/mnt/audiobooks-organized":/output \
  abs-rename
```

### GitHub Container Registry (GHCR)
Published at: `ghcr.io/yxqzme2/abs-rename:latest`

Build & publish:
```bash
docker build -t ghcr.io/yxqzme2/abs-rename:latest .
docker push ghcr.io/yxqzme2/abs-rename:latest
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_sanitizer.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

**Test coverage:**
- `test_sanitizer.py` — Filename/path sanitization (Windows reserved names, invalid chars)
- `test_template_engine.py` — Template rendering and fallback rules
- `test_matcher.py` — Confidence scoring edge cases, weight validation
- `test_dry_run.py` — Dry-run mode (no filesystem writes)
- `test_conflict_detection.py` — File-exists detection, skip behavior

---

## Stack & Dependencies

**Backend:**
- **FastAPI 0.111.0** — Web framework & routing
- **Uvicorn 0.29.0** — ASGI server
- **Jinja2 3.1.4** — HTML templating
- **mutagen 1.47.0** — Audiobook tag extraction
- **httpx 0.27.0** — Async HTTP (AudNexus API calls)
- **RapidFuzz 3.9.3** — Fuzzy string matching (confidence scoring)
- **aiosqlite 0.20.0** — Async SQLite
- **python-dotenv 1.0.1** — .env file loading

**Testing:**
- **pytest 8.2.0**
- **pytest-asyncio 0.23.7**

**Containerization:**
- **Docker** (base: `python:3.12-slim`)
- **Unraid support** (XML template in `scripts/docker/unraid-template.xml`)

**Python version:** 3.12+

---

## Common Development Tasks

### Add a New API Endpoint
1. Create route function in `app/api/routes/{domain}.py`
2. Use Pydantic models for request/response
3. Register router in `app.main:app.include_router()`
4. Add tests in `tests/test_{domain}.py`

### Swap Metadata Provider
1. Implement `BaseMetadataProvider` in `app/providers/`
2. Update provider instantiation in `app/api/routes/scan.py`
3. Test with mock API responses
4. Update `.env` if needed for new provider credentials

### Adjust Confidence Scoring
1. Modify weights in `app/config.py:SCORE_WEIGHTS`
2. Adjust thresholds: `CONFIDENCE_AUTO_APPROVE`, `CONFIDENCE_REVIEW_REQUIRED`
3. Test against known matches: `pytest tests/test_matcher.py`

### Add a Naming Template
1. Create template object in code or via UI (`/templates-settings`)
2. Test with **live preview** before saving
3. Verify all tokens render correctly with fallbacks
4. Check sanitization on Windows reserved names

### Modify Database Schema
1. Edit `app/db/schema.py` (CREATE TABLE statements)
2. Increment schema version logic if needed (v1 uses simple bootstrap)
3. Re-initialize DB: delete `abs_rename.db`, restart app
4. For future: implement migration system

### Run in Debug Mode
```bash
DEBUG=true uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Enables verbose logging; check console for detailed trace output.

---

## Known Limitations in v1

1. **AudNexus coverage** — Most Audible catalog available, but obscure titles may be unmatched
2. **No SLA** — AudNexus is community-run; if down, scans show unmatched results (handled gracefully)
3. **`.m4b` only** — No `.mp3`, `.flac`, `.opus`, or other formats
4. **Originals untouched** — Only copies created; sources never modified
5. **Conflict handling** — Skip-only; no overwrite option (v1)
6. **Multi-file audiobooks** — Split books (Part 1, Part 2) treated as individual files; may produce duplicate plans
7. **No tag writing** — Metadata not updated in copied files
8. **No cover art** — Images not downloaded or embedded
9. **No background processing** — Synchronous; large libraries may take minutes
10. **Text-based folder selection** — No OS-level file browser dialog

---

## Recent Work & Current Status

**Latest commits:**
- `c6abea1` — Add live scan progress log with SSE streaming
- `334174f` — Replace uploaded icon with generated PNG
- `e916363` — Point icon to PNG
- `389b32b` — Add files via upload
- `973feb6` — Fix icon URL to use raw GitHub path
- `4b7faf8` — Add GitHub Actions workflow (build & publish to GHCR)

**Current state:** Feature-complete for v1. Core workflow (scan → match → approve → copy) is stable. SSE streaming for real-time progress is implemented. Database logging for audit trail working. Ready for personal use and community testing.

**No known critical bugs.** See GitHub issues for feature requests.

---

## Important Notes

### Rebuild Requirements
- **Add/modify dependencies?** Run `pip freeze > requirements.txt` before committing
- **Change database schema?** Delete `abs_rename.db`; app will reinitialize on next startup
- **Update environment variables?** Edit `.env` or pass via `-e` flags in Docker

### Caching Behavior
- **AudNexus results** cached in SQLite per-batch (same query won't hit API twice in one scan)
- **Template previews** computed on-demand (no caching)
- **Static files** (CSS, JS) served with ETag headers

### Port Assignments
- **Development:** Default `8000` (configured in `.env`)
- **Docker:** Default `8000` exposed; override with `-p 8001:8000`
- **Unraid:** Configurable in container settings; default `8756`

### Volume Considerations
- SQLite DB grows ~5–10 MB per 1,000 scanned files (with history)
- Audiobook scans do **not** copy audio data into database; only metadata
- Source files not touched (read-only access recommended)

---

## Quick Reference Commands

```bash
# Local dev
uvicorn app.main:app --reload

# Run tests
pytest tests/ -v

# Build Docker image
docker build -t abs-rename .

# Run Docker (local)
docker run -p 8000:8000 -v /data:/data -v /audiobooks:/audiobooks -v /output:/output abs-rename

# Activate venv
source .venv/bin/activate

# Install/update deps
pip install -r requirements.txt

# Generate requirements.txt
pip freeze > requirements.txt

# Debug logging
DEBUG=true uvicorn app.main:app --reload
```

---

**Last updated:** 2026-04-21  
**Maintained by:** Community  
**License:** (check GitHub repo)
