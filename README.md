# ABS Rename

A local-first web app for scanning, identifying, and organizing `.m4b` audiobook files
for import into [Audiobookshelf](https://www.audiobookshelf.org/).

It scans folders for `.m4b` files, fetches metadata from the
[AudNexus](https://api.audnexus.app) community API (no account required),
scores each match, and copies files into a clean folder structure ready for ABS.

**Originals are never modified or deleted. Only copies are created.**

---

## Requirements

- Python 3.12+
- pip

---

## Local Setup (without Docker)

### 1. Clone / download the project

```
cd H:\ABS_Rename
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

- **Windows (cmd):** `.venv\Scripts\activate.bat`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Linux/macOS:** `source .venv/bin/activate`

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
copy .env.example .env       # Windows
# or
cp .env.example .env         # Linux/macOS
```

Edit `.env` and set at minimum:

```
DEFAULT_OUTPUT_FOLDER=C:\Audiobooks\Organized
```

Everything else has sensible defaults.

### 5. Run the app

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open your browser: **http://127.0.0.1:8000**

---

## Docker Setup

### Build

```bash
docker build -t abs-rename .
```

### Run

```bash
docker run -p 8000:8000 \
  -v "C:\Audiobooks":/audiobooks \
  -v "C:\Audiobooks\Organized":/output \
  -v "C:\ABSData":/data \
  abs-rename
```

- `/audiobooks` — mount your source audiobook library here
- `/output` — mount your ABS library folder here
- `/data` — persistent storage for the SQLite database

Open: **http://localhost:8000**

---

## How to Use

### 1. Scan page (`/`)

1. Enter one or more **source folder** paths containing `.m4b` files.
   The scanner searches recursively.
2. Enter your **output folder** — where organized copies will be written.
3. Choose a **naming template** (default is ABS Series Format).
4. Leave **Dry run** checked for a safe first pass — no files will be copied.
5. Click **Start Scan**.

### 2. Results page

Each file is shown with:
- Extracted local metadata (tags from inside the file)
- Best AudNexus match + confidence score
- Proposed destination path

Color-coded confidence:
- **Green (≥90%)** — high confidence, pre-checked for approval
- **Yellow (75–89%)** — review suggested, unchecked by default
- **Red (<75%)** — low confidence, marked "review required"

For each row you can:
- Check/uncheck the approval checkbox
- Pick an alternate candidate from the dropdown
- Click **Search** to run a custom query for that file
- Edit the destination path inline

### 3. Execute page

Shows a summary of all approved plans, then runs the copy.
Progress streams in real time. A final summary shows counts for
copied / skipped / errors.

### 4. Templates settings (`/templates-settings`)

- View and select from predefined templates
- Create custom templates using token syntax
- Live preview with sample data

### 5. History (`/history`)

View past batch runs with counts and per-file operation logs.

---

## Naming Templates

Templates use Python-style format strings:

| Token | Description | Example |
|---|---|---|
| `{author}` | First author name | `Patrick Rothfuss` |
| `{title}` | Book title | `The Name of the Wind` |
| `{series}` | Series name | `The Kingkiller Chronicle` |
| `{series_index:02d}` | Series position, zero-padded | `01` |
| `{year}` | Release year | `2007` |
| `{narrator}` | First narrator | `Nick Podehl` |
| `{asin}` | Audible ASIN | `B002V0QUOC` |

**Fallbacks applied automatically:**
- No series → folder segment replaced with `Standalone`
- No author → `Unknown Author`
- No title → `Unknown Title`
- Missing `{year}` or `{narrator}` → token removed cleanly

**Default (ABS Series Format):**
```
{author}/{series}/{series_index:02d} - {title}
```
Produces:
```
Patrick Rothfuss/The Kingkiller Chronicle/01 - The Name of the Wind/
  01 - The Name of the Wind.m4b
```

---

## AudNexus Provider

This app uses the [AudNexus](https://github.com/laxamentumtech/audnexus) community API —
a free, no-auth Audible metadata mirror maintained by the ABS community.

- No account or API key required
- Covers the majority of the Audible catalog
- The app adds a polite delay between requests (configurable via `AUDNEXUS_REQUEST_DELAY_MS`)

To swap providers in a future version, implement `app/providers/base.py`'s
`BaseMetadataProvider` interface and update `app/api/routes/scan.py` to use it.

---

## Running Tests

```bash
pytest tests/ -v
```

Test coverage:
- `test_sanitizer.py` — filename/path sanitization
- `test_template_engine.py` — template rendering and fallbacks
- `test_matcher.py` — confidence scoring and match status
- `test_dry_run.py` — dry-run mode (no filesystem writes)
- `test_conflict_detection.py` — conflict detection and skip behavior

---

## Configuration Reference (`.env`)

| Key | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `./abs_rename.db` | Path to SQLite database file |
| `DEFAULT_OUTPUT_FOLDER` | _(empty)_ | Pre-fill output folder in UI |
| `AUDNEXUS_BASE_URL` | `https://api.audnexus.app` | AudNexus API URL |
| `AUDNEXUS_REGION` | `us` | Audible region (`us`, `uk`, `au`, `ca`, etc.) |
| `AUDNEXUS_REQUEST_DELAY_MS` | `400` | Delay between API calls (ms) |
| `CONFIDENCE_AUTO_APPROVE` | `90` | Score threshold for auto-checking items |
| `CONFIDENCE_REVIEW_REQUIRED` | `75` | Score threshold for "review required" flag |
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable verbose logging |

---

## Project Structure

```
app/
  main.py                 — FastAPI app, routes, lifespan
  config.py               — Settings loaded from .env
  api/routes/
    scan.py               — POST /api/scan
    match.py              — Results, approve, search-again, select
    copy.py               — SSE copy stream, summary
    templates.py          — Template CRUD + preview
    history.py            — Batch history
  services/
    scanner.py            — Recursive .m4b discovery
    metadata_reader.py    — mutagen tag extraction
    matcher.py            — Weighted confidence scoring
    preview_planner.py    — Rename plan builder
    copy_executor.py      — Copy with SSE progress
  providers/
    base.py               — Abstract provider interface
    audnexus.py           — AudNexus implementation
  models/                 — Pydantic data models
  db/
    connection.py         — aiosqlite connection manager
    schema.py             — CREATE TABLE statements
    queries/              — DB helpers per domain
  path_engine/
    template_engine.py    — Template rendering + fallbacks
    sanitizer.py          — Filename/path sanitization
  templates/              — Jinja2 HTML pages
  static/                 — CSS and JS
tests/                    — pytest test suite
```

---

## Known Limits in v1

- **AudNexus coverage** — Most of the Audible catalog is available, but
  obscure titles may not be found. Items with no match are marked "unmatched"
  and excluded from the copy plan.
- **AudNexus availability** — It is a community-run service with no SLA.
  If it is down, scans will produce unmatched results. The app handles this
  gracefully and never crashes.
- **Non-public API** — AudNexus mirrors Audible data. Its availability and
  response format may change without notice.
- **`.m4b` files only** — No support for `.mp3`, `.flac`, `.opus`, or other
  audio formats.
- **Originals untouched** — Only copies are created. Source files are never
  renamed, moved, or modified.
- **Conflict handling is skip-only** — If a destination file already exists,
  it is skipped and logged. No overwrite option in v1.
- **Multi-file audiobooks** — Books split into multiple `.m4b` parts
  (Part 1, Part 2, etc.) are treated as individual files. This may produce
  duplicate or inconsistent rename plans.
- **No tag writing** — The app does not update embedded metadata in any file.
- **No cover art** — Cover images are not downloaded or embedded.
- **No background processing** — All scan and copy operations run
  synchronously. Large libraries may take several minutes.
- **Folder selection via text input** — There is no OS-level file browser
  dialog. You must type the absolute path manually.
