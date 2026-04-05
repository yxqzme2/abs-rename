Build a local-first audiobook metadata and copy/rename web app.

Purpose:
This is a personal-use tool designed to keep .m4b audiobook files clean and consistently organized
for import into Audiobookshelf (ABS). It is not intended for distribution. No authentication,
multi-user support, or hardening for public exposure is required.

Core goal:
Create an app that scans user-selected local folders for audiobook files (limited to .m4b only),
searches for matching metadata via the AudNexus community API (no login required), shows a preview
of the match and proposed destination path, and then copies the files into a newly organized folder
structure based on a user-selectable naming pattern. The app must never modify or rename the
original source files.

Product constraints:
- Input file type: .m4b only
- Works only on audiobooks the user already owns locally
- Manual folder selection only for v1 (text input fields — not OS file dialogs)
- No watch folders
- No automatic background scanning
- No modification of original files
- Output should be copied files in new folders
- Include a dry-run checkbox that simulates all actions without copying
- GUI must allow the user to choose the output naming pattern
- GUI must allow preview before execution
- If a match is uncertain, do not auto-apply it

Preferred stack:
- Backend: Python 3.12+
- API/web server: FastAPI
- Metadata reading: mutagen
- Fuzzy matching: RapidFuzz
- HTTP client: httpx (async-friendly, used for AudNexus requests)
- Frontend: simple server-rendered HTML + JS (plain, no React needed)
- Storage: SQLite for scan history, match decisions, and copy logs
- Progress streaming: Server-Sent Events (SSE) via FastAPI's StreamingResponse
- Packaging: Docker-ready, but it must also run locally without Docker during development

Architecture requirements:
Use a clean modular architecture with these components:
1. scanner
2. metadata reader
3. audnexus provider (metadata source — abstracted behind a provider interface)
4. matcher/scoring engine
5. preview planner
6. copy executor
7. path template engine
8. persistence layer
9. web UI

Important:
The metadata provider must be abstracted behind a provider interface so it can be swapped later.
The AudNexus implementation is the default for v1. Do not tightly couple the app to AudNexus-specific
response models. Use an internal normalized book metadata model.

---

Metadata provider: AudNexus (v1)

AudNexus is a free, community-maintained audiobook metadata API with no authentication required.
It is built specifically to support tools like Audiobookshelf and covers the majority of the
Audible catalog. It is the preferred provider for this project.

Base URL: https://api.audnexus.app

Key endpoints used:
- GET /books?title={title}&author={author}&region=us  — search for candidates
- GET /books/{asin}                                   — fetch full book details by ASIN
- GET /authors/{asin}                                 — fetch author details if needed

No API key, login, or device registration required.

Rate limiting: AudNexus is generous for personal use but the app should still add a small delay
between batch requests (e.g., 300–500ms between calls) to be a good citizen and avoid hitting
any soft limits.

Add retry logic with exponential backoff for transient failures. Log all provider errors cleanly.
Do not crash the scan if a lookup fails for one item — mark that item as unmatched instead.

If AudNexus does not have a result for a specific title, fall back to returning an empty candidate
list and marking the item as unmatched. Do not attempt a secondary scrape of audible.com in v1 —
that path is fragile and adds significant complexity.

Provider interface (abstract base):
  - search_books(query: str, author: str | None) -> list[AudibleCandidate]
  - get_book_by_asin(asin: str) -> AudibleCandidate | None

AudNexus returns normalized data including title, subtitle, authors, narrators, series name,
series position, runtime in minutes, language, ASIN, and cover image URL.

Note: AudNexus uses 'series position' as a string (e.g., "2", "2.5", "Book 1"). Store it as a
string internally and convert to float for scoring comparisons.

---

Audiobookshelf compatibility:

The primary purpose of this tool is to produce a folder structure that ABS can cleanly import.
ABS expects audiobooks organized as: one audiobook = one folder. The folder name and parent path
are how ABS derives the title, author, and series.

Recommended ABS-compatible output structures:
  Standalone:  {author}/{title}/{title}.m4b
  Series:      {author}/{series}/{series_index:02d} - {title}/{series_index:02d} - {title}.m4b

The app should default to the series format when series data is available, and fall back to the
standalone format when it is not. The file inside the folder should share the folder name (ABS
convention). The path template engine must support this two-level naming (folder name + filename).

---

Main workflow:
1. User enters one or more source folder paths (text input)
2. User enters the output folder path (text input)
3. App recursively scans for .m4b files
4. App reads existing embedded metadata from each file where available
5. App derives search queries using this priority order:
     a. title tag + author tag (if both present)
     b. title tag only (if author tag missing)
     c. parsed filename (strip extensions, common noise like " - Unabridged", part numbers)
     d. parent folder name as last resort
6. App searches AudNexus and retrieves candidate matches
7. App scores candidates
8. App shows for each file:
   - source file path
   - extracted local metadata
   - best AudNexus match
   - confidence score
   - alternate candidates if relevant
   - proposed destination path based on current naming template
9. User can:
   - accept match (pre-checked if confidence >= 90)
   - reject match
   - manually choose alternate candidate
   - edit destination path inline if needed
   - trigger "search again" with a custom query (opens a search input for that row)
10. App executes copy operation into newly created destination folders
11. App logs every operation so the batch can be audited later

---

Strict behavior rules:
- Never rename or move source files
- Only copy source files to destination
- Create destination folders as needed
- Skip copy if destination file already exists unless overwrite is explicitly enabled later;
  for v1 just skip and report conflict
- If confidence is below 90, do not pre-check the item — still show it, require explicit user approval
- If confidence is below 75, mark item as "review required" and do not pre-check
- If no good match is found, mark as unmatched and do nothing
- Dry-run mode must perform everything except the actual filesystem copy
- All actions must be logged in SQLite

Confidence thresholds:
- >= 90: high confidence — pre-checked in UI, user can uncheck
- 75–89: review suggested — shown but unchecked, user must explicitly accept
- < 75: low confidence — shown as "review required", unchecked, flagged in UI
- no match: item marked unmatched, excluded from copy plan

---

Data model requirements:
Create normalized internal models for:
- LocalAudiobook   — one record per .m4b file discovered by the scanner
- LocalMetadata    — embedded tag data extracted from the file (1:1 with LocalAudiobook)
- AudibleCandidate — a single candidate result from the metadata provider
- MatchResult      — the scoring decision linking a LocalAudiobook to a candidate
- RenamePlan       — the resolved destination path for one file
- CopyOperation    — one copy action with status and error info
- BatchRun         — a single execution run (scan + copy session)
- UserTemplatePreference — saved naming template selection

Note on LocalAudiobook vs LocalMetadata:
LocalAudiobook is the file-level record (path, size, extension, scan status).
LocalMetadata holds the tag values read from inside the file. They are 1:1 but separated so the
scanner can create the LocalAudiobook record before tag reading is complete, and tag reading
failures don't block file tracking.

Suggested fields:

LocalAudiobook:
- id
- source_path
- filename
- folder_path
- extension
- file_size
- scan_status            (pending | scanned | matched | unmatched | review_required | error)

LocalMetadata:
- id
- local_audiobook_id     (FK -> LocalAudiobook)
- duration_seconds       (float — read from mutagen)
- title_from_tags
- author_from_tags
- album_from_tags
- narrator_from_tags
- series_from_tags
- series_index_from_tags (string — store raw, e.g., "2", "2.5")
- has_embedded_cover     (bool)
- raw_tags_json          (full tag dump for debugging)

AudibleCandidate:
- id
- batch_run_id           (FK -> BatchRun)
- local_audiobook_id     (FK -> LocalAudiobook)
- provider_id            (e.g., "audnexus")
- asin
- title
- subtitle
- authors                (JSON array of strings)
- narrators              (JSON array of strings)
- series_name
- series_position        (string — store raw, e.g., "2.5")
- runtime_seconds        (converted from provider minutes)
- image_url
- language
- raw_payload_json

MatchResult:
- id
- local_audiobook_id     (FK -> LocalAudiobook)
- batch_run_id           (FK -> BatchRun)
- selected_candidate_asin
- confidence_score       (float 0–100)
- match_status           (auto | review_required | unmatched | user_selected)
- title_score
- author_score
- narrator_score
- series_score
- runtime_score
- notes

RenamePlan:
- id
- local_audiobook_id     (FK -> LocalAudiobook)
- batch_run_id           (FK -> BatchRun)
- template_used
- destination_dir
- destination_filename
- full_destination_path
- is_conflict            (bool)
- is_dry_run             (bool)
- user_approved          (bool — must be true before copy proceeds)

CopyOperation:
- id
- batch_run_id           (FK -> BatchRun)
- source_path
- destination_path
- status                 (pending | success | skipped_conflict | error | dry_run)
- error_message
- timestamp

BatchRun:
- id
- started_at
- completed_at
- source_folders         (JSON array of paths)
- output_folder
- template_used
- is_dry_run             (bool)
- total_scanned
- total_matched
- total_review_required
- total_unmatched
- total_planned
- total_copied
- total_skipped_conflicts
- total_errors

UserTemplatePreference:
- id
- name                   (display name, e.g., "ABS Series Format")
- template_string        (e.g., "{author}/{series}/{series_index:02d} - {title}")
- is_default             (bool)
- created_at

---

Matching/scoring requirements:
Implement a weighted scoring system using:
- exact/fuzzy title similarity
- author overlap
- narrator overlap if available
- series name similarity if available
- series position similarity if available
- runtime similarity if available
- language check if available

Weights (configurable in code via a dict or settings):
- title:    45%
- author:   25%
- narrator: 10%
- series:   15%
- runtime:   5%

Runtime similarity scoring:
Compare local duration_seconds to candidate runtime_seconds.
Use a tolerance window: within ±5% = full score, ±5–15% = partial score, >15% = zero.
This matters because abridged editions may differ significantly from unabridged.

Normalize titles before matching (comparison only — preserve originals for display):
- lowercase
- strip punctuation
- collapse whitespace
- remove noise tokens: "unabridged", "audiobook", "a novel", "the complete", part/book
  number patterns like "book 1", "part 2" — for comparison only

Series position comparison:
Convert both local and candidate series positions to float before comparing.
Handle common strings like "Book 2.5", "Part 1", "2.5" -> 2.5

---

Template/path engine requirements:
The GUI must let the user choose from predefined templates and optionally type a custom template.
The template engine must produce both the destination folder path and the destination filename,
since ABS convention is for the filename to match the folder name.

Predefined templates:
1. ABS Series Format (default):
     folder:   {author}/{series}/{series_index:02d} - {title}
     filename: {series_index:02d} - {title}.m4b

2. ABS Standalone Format:
     folder:   {author}/{title}
     filename: {title}.m4b

3. Series with Year:
     folder:   {author}/{series}/{series_index:02d} - {title} ({year})
     filename: {series_index:02d} - {title} ({year}).m4b

4. Flat Author/Title:
     folder:   {author}/{title}
     filename: {title}.m4b

Template variable reference:
  {author}         — first author name
  {title}          — book title
  {series}         — series name
  {series_index}   — series position (numeric, format with :02d for zero-padding)
  {year}           — publication year (if available from provider)
  {narrator}       — first narrator name
  {asin}           — Audible ASIN

Fallback rules (applied at render time):
- If no series: replace series path segment with "Standalone"
  e.g., {author}/Standalone/{title}
- If no series_index: omit the series_index prefix from filename
- If no author: use "Unknown Author"
- If no title: use "Unknown Title"
- If no year: omit year segment entirely

Sanitization rules:
- Replace characters invalid on Windows and Linux: \ / : * ? " < > |
- Replace with a hyphen or strip, depending on context
- Trim trailing periods and spaces from each path segment (Windows restriction)
- Replace reserved Windows device names (CON, PRN, AUX, NUL, COM1–COM9, LPT1–LPT9) with
  a safe prefix (e.g., "_CON") if they appear as a full path segment
- Collapse multiple consecutive spaces or hyphens

---

Progress reporting:
The copy executor must stream progress back to the UI using Server-Sent Events (SSE).
The FastAPI backend should expose a streaming endpoint that yields events as each file is copied.
The frontend should listen to this stream and update a progress bar and per-item status in real time.
Events should include: item index, total items, current file path, status (success/skipped/error).

"Search again" behavior:
When the user clicks "Search again" on a results row, a search input should appear inline for that
row, pre-populated with the current title + author. The user can edit the query and submit.
The app sends a new search request for that row only, returns the new candidates, and the user can
select from them. The row's match result and rename plan update accordingly.

---

Copy behavior requirements:
- Copy, never move
- Preserve file extension
- Ensure destination directories exist (create them)
- Use buffered copy (shutil.copy2 preserves metadata)
- Validate source exists before copy
- Validate destination parent path is writable
- Detect conflicts before copy
- For v1: if destination file already exists, skip it and log the conflict
- Stream per-file progress via SSE
- Return a summary at the end:
  - scanned
  - matched
  - review required
  - unmatched
  - planned copies
  - completed copies
  - skipped conflicts
  - errors

---

Database requirements:
Use SQLite with a bootstrap schema (simple CREATE TABLE IF NOT EXISTS approach for v1, migrations
can be added later). Initialize the schema on app startup.

Store:
- scanned files (LocalAudiobook)
- extracted local metadata (LocalMetadata)
- provider candidates chosen by user (AudibleCandidate)
- match scores (MatchResult)
- rename plans (RenamePlan)
- copy history (CopyOperation)
- batch runs (BatchRun)
- saved naming templates (UserTemplatePreference)
- app settings (key/value table for things like default output folder, confidence threshold)

---

Web UI requirements:
Build a clean, simple interface. Plain HTML + vanilla JS is fine. No framework required.
Keep it functional. Correct behavior matters more than visual polish.

Pages/sections:

1. Scan page
   - Source folder path input (text field — user types absolute path)
   - Add another source folder (allow multiple)
   - Output folder path input (text field)
   - Dry-run checkbox (checked by default for safety)
   - Template selector (dropdown)
   - Scan button
   - Link to batch history

2. Results page
   - Table/grid of scanned audiobooks
   - Columns:
     - Checkbox (approve this item)
     - Source file
     - Local title / local author
     - Best match title / best match author
     - Series + position
     - Confidence (color-coded: green >= 90, yellow 75–89, red < 75)
     - Proposed destination path
     - Status badge
   - Actions per row:
     - Accept (check the row)
     - Reject (uncheck and mark rejected)
     - Choose alternate candidate (dropdown of other candidates)
     - Search again (inline query input)
     - Edit destination path (inline edit)
   - Bulk actions: Select all high-confidence, Deselect all, Invert selection
   - Proceed to Execute button (disabled until at least one row is approved)

3. Template settings
   - Choose predefined template (radio buttons)
   - Create/edit custom template (text input with token reference guide)
   - Live preview of rendered example path using sample data

4. Execute page
   - Summary of approved plans (count, list of destination paths)
   - Dry-run indicator if active
   - Run copy button
   - Real-time progress bar (SSE-fed)
   - Per-item status as each copy completes
   - Final summary report

5. Batch history
   - Past runs (date, source folders, output folder, template, dry-run flag)
   - Counts: scanned / matched / copied / conflicts / errors
   - Expandable detail: copied paths, skipped items, error messages

---

Search query derivation algorithm:
For each file, build the AudNexus search query using this priority order:
1. If title tag AND author tag are both present: search(title=title_tag, author=author_tag)
2. If only title tag is present: search(title=title_tag)
3. If no title tag: parse the filename — strip extension, remove trailing noise patterns
   (e.g., " - Unabridged", "_Part1", " (MP3)"), use result as title query
4. If filename parsing yields nothing useful: use the immediate parent folder name as title query
Log which derivation strategy was used for each file.

---

Metadata extraction requirements:
For each .m4b, use mutagen to read:
- title
- author (©ART or ©wrt or TPE1 depending on tag format)
- album (may contain series info in some taggers)
- narrator (check ©nrt, TXXX:NARRATOR, or similar custom tags)
- duration (in seconds, from mutagen info)
- whether an embedded cover image is present
- series name (check custom tags: TXXX:SERIES, ©mvn, etc.)
- series index (check TXXX:SERIES-PART, ©mvi, etc.)
If tags are missing or unreadable, fall back to filename/folder parsing as described above.
Log a warning if mutagen raises an exception on a file; mark that file's tag extraction as failed
but continue processing.

---

Multi-file audiobook note:
Some audiobooks are split into multiple .m4b files (e.g., Part 1 and Part 2). This app treats
each file independently in v1. Split sets may produce duplicate or conflicting rename plans.
This is a known limitation and is documented in "Known limits in v1."

---

Implementation preferences:
- Generate a working MVP, not a fake prototype
- Prioritize working scan -> match -> preview -> copy flow
- Use type hints throughout
- Use logging (Python logging module, not print statements)
- Handle filesystem errors carefully
- Keep code readable and well-commented — this is a personal project and comments help
- Avoid overengineering
- Do not add user authentication
- Do not add background workers for v1
- Do not add file tag writing for v1
- Do not add cover download/saving for v1
- Do not add move/delete operations for v1

---

Project output requested:
1. Full project structure
2. Complete runnable code
3. requirements.txt or pyproject.toml
4. README with setup instructions
5. .env.example (for things like default output path, confidence threshold)
6. SQLite schema/init script
7. Notes about where to adjust AudNexus base URL or swap provider
8. Dockerfile
9. Basic tests for:
   - path rendering (all templates + fallback cases)
   - filename sanitization (Windows reserved names, invalid chars)
   - confidence scoring (weighted formula, edge cases)
   - dry-run behavior (no filesystem writes occur)
   - conflict detection (destination already exists)
   - runtime tolerance scoring

---

Suggested folder structure:
- app/
  - main.py
  - api/
      routes/
        scan.py
        match.py
        copy.py
        templates.py
        history.py
  - services/
      scanner.py
      metadata_reader.py
      matcher.py
      preview_planner.py
      copy_executor.py
  - providers/
      base.py            (abstract provider interface)
      audnexus.py        (AudNexus implementation)
  - models/
      local_audiobook.py
      local_metadata.py
      candidate.py
      match_result.py
      rename_plan.py
      copy_operation.py
      batch_run.py
  - db/
      connection.py
      schema.py
      queries/
  - path_engine/
      template_engine.py
      sanitizer.py
  - templates/           (Jinja2 HTML templates)
  - static/              (CSS, JS)
  - utils/
      logging.py
      file_utils.py
- tests/
- Dockerfile
- README.md
- .env.example

---

Known limits in v1:
- Audible matching uses AudNexus, a community API. Coverage is broad but not complete.
  If a book is not in the AudNexus database, it will be marked as unmatched.
- AudNexus is a third-party service with no SLA. If it is unavailable, scans will produce
  unmatched results. The app handles this gracefully and does not crash.
- .m4b only — no support for .mp3, .flac, .opus, or other formats
- Originals are never modified — only copies are created in the output folder
- Conflict handling is skip-only — if a destination file already exists, it is skipped and logged
- Multi-file (split) audiobooks are treated as individual files, which may produce duplicate plans
- No tag writing — the app does not update embedded metadata in source or destination files
- No cover art downloading or embedding
- No move or delete operations — copy only
- No background processing — all operations run synchronously in v1
- Folder selection is via text input — no OS-level file browser dialog
