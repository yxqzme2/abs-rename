"""
app/db/schema.py
----------------
SQLite bootstrap schema.
All CREATE TABLE statements use IF NOT EXISTS so this is safe to run on
every startup. No migration framework needed for v1.
"""

# Each statement is a string so connection.py can execute them one by one.
SCHEMA_STATEMENTS: list[str] = [

    # ------------------------------------------------------------------
    # batch_runs — one record per scan+copy session
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS batch_runs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at              TEXT NOT NULL,
        completed_at            TEXT,
        source_folders          TEXT NOT NULL,   -- JSON array of paths
        output_folder           TEXT NOT NULL,
        template_used           TEXT,
        is_dry_run              INTEGER NOT NULL DEFAULT 1,  -- 0/1 bool
        total_scanned           INTEGER DEFAULT 0,
        total_matched           INTEGER DEFAULT 0,
        total_review_required   INTEGER DEFAULT 0,
        total_unmatched         INTEGER DEFAULT 0,
        total_planned           INTEGER DEFAULT 0,
        total_copied            INTEGER DEFAULT 0,
        total_skipped_conflicts INTEGER DEFAULT 0,
        total_errors            INTEGER DEFAULT 0
    )
    """,

    # ------------------------------------------------------------------
    # local_audiobooks — one record per .m4b file found by the scanner
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS local_audiobooks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_run_id    INTEGER NOT NULL REFERENCES batch_runs(id),
        source_path     TEXT NOT NULL,
        filename        TEXT NOT NULL,
        folder_path     TEXT NOT NULL,
        extension       TEXT NOT NULL DEFAULT '.m4b',
        file_size       INTEGER DEFAULT 0,
        scan_status     TEXT NOT NULL DEFAULT 'pending'
        -- scan_status values: pending | scanned | matched | unmatched
        --                     review_required | error
    )
    """,

    # ------------------------------------------------------------------
    # local_metadata — tag data extracted from inside each .m4b file
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS local_metadata (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        local_audiobook_id      INTEGER NOT NULL UNIQUE
                                    REFERENCES local_audiobooks(id),
        duration_seconds        REAL,
        title_from_tags         TEXT,
        author_from_tags        TEXT,
        album_from_tags         TEXT,
        narrator_from_tags      TEXT,
        series_from_tags        TEXT,
        series_index_from_tags  TEXT,   -- stored as raw string e.g. "2.5"
        has_embedded_cover      INTEGER DEFAULT 0,  -- 0/1 bool
        raw_tags_json           TEXT    -- full tag dump for debugging
    )
    """,

    # ------------------------------------------------------------------
    # audible_candidates — results returned by the metadata provider
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS audible_candidates (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_run_id        INTEGER NOT NULL REFERENCES batch_runs(id),
        local_audiobook_id  INTEGER NOT NULL REFERENCES local_audiobooks(id),
        provider_id         TEXT NOT NULL DEFAULT 'audnexus',
        asin                TEXT NOT NULL,
        title               TEXT,
        subtitle            TEXT,
        authors             TEXT,   -- JSON array of strings
        narrators           TEXT,   -- JSON array of strings
        series_name         TEXT,
        series_position     TEXT,   -- raw string e.g. "2", "2.5"
        runtime_seconds     REAL,
        image_url           TEXT,
        language            TEXT,
        release_date        TEXT,
        raw_payload_json    TEXT
    )
    """,

    # ------------------------------------------------------------------
    # match_results — scoring decision for each file
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS match_results (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        local_audiobook_id      INTEGER NOT NULL UNIQUE
                                    REFERENCES local_audiobooks(id),
        batch_run_id            INTEGER NOT NULL REFERENCES batch_runs(id),
        selected_candidate_asin TEXT,
        confidence_score        REAL DEFAULT 0.0,
        match_status            TEXT NOT NULL DEFAULT 'unmatched',
        -- match_status values: auto | review_required | unmatched | user_selected
        title_score             REAL DEFAULT 0.0,
        author_score            REAL DEFAULT 0.0,
        narrator_score          REAL DEFAULT 0.0,
        series_score            REAL DEFAULT 0.0,
        runtime_score           REAL DEFAULT 0.0,
        notes                   TEXT
    )
    """,

    # ------------------------------------------------------------------
    # rename_plans — resolved destination path for one file
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS rename_plans (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        local_audiobook_id      INTEGER NOT NULL
                                    REFERENCES local_audiobooks(id),
        batch_run_id            INTEGER NOT NULL REFERENCES batch_runs(id),
        template_used           TEXT,
        destination_dir         TEXT,
        destination_filename    TEXT,
        full_destination_path   TEXT,
        is_conflict             INTEGER NOT NULL DEFAULT 0,  -- 0/1 bool
        is_dry_run              INTEGER NOT NULL DEFAULT 1,
        user_approved           INTEGER NOT NULL DEFAULT 0
    )
    """,

    # ------------------------------------------------------------------
    # copy_operations — one row per file copy attempt
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS copy_operations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_run_id        INTEGER NOT NULL REFERENCES batch_runs(id),
        source_path         TEXT NOT NULL,
        destination_path    TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'pending',
        -- status values: pending | success | skipped_conflict | error | dry_run
        error_message       TEXT,
        timestamp           TEXT NOT NULL
    )
    """,

    # ------------------------------------------------------------------
    # user_template_preferences — saved naming templates
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS user_template_preferences (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        template_string TEXT NOT NULL,
        is_default      INTEGER NOT NULL DEFAULT 0,  -- 0/1 bool
        created_at      TEXT NOT NULL
    )
    """,

    # ------------------------------------------------------------------
    # app_settings — key/value store for persistent app configuration
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key     TEXT PRIMARY KEY,
        value   TEXT
    )
    """,

    # ------------------------------------------------------------------
    # Seed default naming templates (only if table is empty)
    # ------------------------------------------------------------------
    """
    INSERT OR IGNORE INTO user_template_preferences
        (id, name, template_string, is_default, created_at)
    VALUES
        (1, 'ABS Series Format',
         '{author}/{series}/{series_index:02d} - {title}',
         1,
         datetime('now')),
        (2, 'ABS Standalone Format',
         '{author}/{title}',
         0,
         datetime('now')),
        (3, 'Series with Year',
         '{author}/{series}/{series_index:02d} - {title} ({year})',
         0,
         datetime('now')),
        (4, 'Flat Author/Title',
         '{author}/{title}',
         0,
         datetime('now'))
    """,
]
