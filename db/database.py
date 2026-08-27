import sqlite3
from pathlib import Path
from config import config


class Database:
    """SQLite database for tracking scanned media and analysis results."""

    def __init__(self):
        db_path = Path((config.get("database") or {}).get("path") or "plex_ops.db")
        # A relative path is taken against the project, not against wherever the
        # command happened to be run from, so a script started from another
        # folder does not quietly open a second, empty database.
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parent.parent / db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS media_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                library TEXT,
                volume TEXT,
                size_bytes INTEGER,
                video_codec TEXT,
                resolution TEXT,
                audio_codec TEXT,
                duration_seconds REAL,
                tmdb_id INTEGER,
                manual_only BOOLEAN DEFAULT 0,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                library TEXT,
                gap_type TEXT NOT NULL,
                season_number INTEGER,
                episode_number INTEGER,
                episode_title TEXT,
                tmdb_id INTEGER,
                collection_name TEXT,
                detail TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS quality_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                library TEXT,
                container TEXT,
                video_codec TEXT,
                video_codec_tag TEXT,
                video_bitrate INTEGER,
                resolution TEXT,
                width INTEGER,
                height INTEGER,
                audio_codec TEXT,
                audio_channels INTEGER,
                audio_bitrate INTEGER,
                duration_seconds REAL,
                score INTEGER,
                issues TEXT,
                upgrade_recommendation TEXT,
                audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rename_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                suggested_path TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'medium',
                library TEXT,
                applied BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_type TEXT NOT NULL,
                files_found INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            -- One row per distinct gap. Franchise and alternate-version checks
            -- run once per owned film, so a collection you own seven films from
            -- reported its missing entry seven times; episode gaps stay distinct
            -- because the season and episode numbers are part of the key.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gaps_unique ON gaps (
                gap_type,
                lower(title),
                COALESCE(season_number, -1),
                COALESCE(episode_number, -1),
                lower(COALESCE(collection_name, '')),
                lower(COALESCE(detail, ''))
            );

            -- Notes the owner puts up for the household: a new library, a
            -- film night, whatever. Kept rather than only pushed, so somebody
            -- who was not holding their phone still sees it next time.
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                url TEXT,
                posted_by TEXT,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retired_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS wanted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                year INTEGER,
                search_query TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'wanted',
                notes TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_wanted_title_year
                ON wanted (lower(title), year);

            -- SQLite treats NULLs as distinct in a UNIQUE index, so the index
            -- above does not stop duplicates when the year is unknown.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wanted_title_noyear
                ON wanted (lower(title)) WHERE year IS NULL;

            CREATE INDEX IF NOT EXISTS idx_wanted_status
                ON wanted (status, added_at DESC);
        """)
        # Who asked for a wanted item, so they can be told when it lands.
        wanted_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(wanted)")}
        if "requested_by_id" not in wanted_columns:
            self.conn.execute("ALTER TABLE wanted ADD COLUMN requested_by_id TEXT")
        if "requested_by" not in wanted_columns:
            self.conn.execute("ALTER TABLE wanted ADD COLUMN requested_by TEXT")
        # A series is not a film of the same name, and season four is not the
        # whole series, so both belong in what makes an entry unique.
        if "kind" not in wanted_columns:
            self.conn.execute("ALTER TABLE wanted ADD COLUMN kind TEXT NOT NULL DEFAULT 'film'")
        if "season" not in wanted_columns:
            self.conn.execute("ALTER TABLE wanted ADD COLUMN season INTEGER")
        self.conn.execute("DROP INDEX IF EXISTS idx_wanted_title_year")
        self.conn.execute("DROP INDEX IF EXISTS idx_wanted_title_noyear")
        self.conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wanted_entry ON wanted (
                COALESCE(kind, 'film'), lower(title),
                COALESCE(year, -1), COALESCE(season, -1)
            )
        """)
        self.conn.commit()

    def upsert_media(self, file_path, **kwargs):
        """Insert or update a media file record."""
        fields = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        updates = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values())

        self.conn.execute(
            f"INSERT INTO media_files (file_path, {fields}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(file_path) DO UPDATE SET {updates}",
            [file_path] + values + values,
        )
        self.conn.commit()

    def get_media(self, file_path):
        """Get a media file record by path."""
        cursor = self.conn.execute(
            "SELECT * FROM media_files WHERE file_path = ?", (file_path,)
        )
        return cursor.fetchone()

    def get_all_media(self, library=None):
        """Get all media files, optionally filtered by library."""
        if library:
            cursor = self.conn.execute(
                "SELECT * FROM media_files WHERE library = ?", (library,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM media_files")
        return cursor.fetchall()

    def log_scan(self, scan_type, files_found):
        """Log a scan event."""
        self.conn.execute(
            "INSERT INTO scan_history (scan_type, files_found, completed_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (scan_type, files_found),
        )
        self.conn.commit()

    def clear_gaps(self, gap_type=None, library=None):
        """Clear gaps, optionally filtered by type and/or library."""
        query = "DELETE FROM gaps WHERE 1=1"
        params = []
        if gap_type:
            query += " AND gap_type = ?"
            params.append(gap_type)
        if library:
            query += " AND library = ?"
            params.append(library)
        self.conn.execute(query, params)
        self.conn.commit()

    def insert_gap(self, **kwargs):
        """Insert a gap record."""
        fields = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        # OR IGNORE: the unique index above decides what counts as the same gap.
        self.conn.execute(
            f"INSERT OR IGNORE INTO gaps ({fields}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        self.conn.commit()

    def get_gaps(self, gap_type=None, library=None):
        """Query gaps, optionally filtered."""
        query = "SELECT * FROM gaps WHERE 1=1"
        params = []
        if gap_type:
            query += " AND gap_type = ?"
            params.append(gap_type)
        if library:
            query += " AND library = ?"
            params.append(library)
        query += " ORDER BY title, season_number, episode_number"
        return self.conn.execute(query, params).fetchall()

    def get_non_manual_libraries(self):
        """Get distinct libraries that aren't manual_only."""
        rows = self.conn.execute(
            "SELECT DISTINCT library FROM media_files WHERE manual_only = 0"
        ).fetchall()
        return [r["library"] for r in rows]

    def upsert_quality(self, file_path, **kwargs):
        """Insert or update a quality score record."""
        fields = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        updates = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values())
        self.conn.execute(
            f"INSERT INTO quality_scores (file_path, {fields}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(file_path) DO UPDATE SET {updates}, audited_at = CURRENT_TIMESTAMP",
            [file_path] + values + values,
        )
        self.conn.commit()

    def get_quality(self, min_score=None, max_score=None, issue=None, library=None, limit=500):
        """Query quality scores with filters."""
        query = "SELECT * FROM quality_scores WHERE 1=1"
        params = []
        if min_score is not None:
            query += " AND score >= ?"
            params.append(min_score)
        if max_score is not None:
            query += " AND score <= ?"
            params.append(max_score)
        if issue:
            query += " AND issues LIKE ?"
            params.append(f"%{issue}%")
        if library:
            query += " AND library = ?"
            params.append(library)
        query += " ORDER BY score ASC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def insert_rename(self, original_path, suggested_path, issue_type, confidence, library=None):
        """Insert a rename suggestion (skip if same suggestion already exists)."""
        existing = self.conn.execute(
            "SELECT id FROM rename_suggestions WHERE original_path = ? AND suggested_path = ?",
            (original_path, suggested_path),
        ).fetchone()
        if existing:
            return
        self.conn.execute(
            "INSERT INTO rename_suggestions (original_path, suggested_path, issue_type, confidence, library) "
            "VALUES (?, ?, ?, ?, ?)",
            (original_path, suggested_path, issue_type, confidence, library),
        )
        self.conn.commit()

    def get_suggestions(self, issue_type=None, confidence=None, library=None, applied=None, limit=500):
        """Query rename suggestions with filters."""
        query = "SELECT * FROM rename_suggestions WHERE 1=1"
        params = []
        if issue_type:
            query += " AND issue_type = ?"
            params.append(issue_type)
        if confidence:
            query += " AND confidence = ?"
            params.append(confidence)
        if library:
            query += " AND library = ?"
            params.append(library)
        if applied is not None:
            query += " AND applied = ?"
            params.append(1 if applied else 0)
        query += " ORDER BY confidence DESC, issue_type, original_path LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def mark_applied(self, suggestion_id):
        """Mark a rename suggestion as applied."""
        self.conn.execute(
            "UPDATE rename_suggestions SET applied = 1, applied_at = CURRENT_TIMESTAMP WHERE id = ?",
            (suggestion_id,),
        )
        self.conn.commit()

    def clear_suggestions(self):
        """Clear all unapplied rename suggestions."""
        self.conn.execute("DELETE FROM rename_suggestions WHERE applied = 0")
        self.conn.commit()

    # ---- wanted list ----

    WANTED_STATUSES = ("wanted", "acquired", "dismissed")

    def find_wanted(self, title, year=None, kind="film", season=None):
        """Find a wanted entry. An unknown year, and each season, is its own identity."""
        return self.conn.execute(
            """SELECT * FROM wanted
               WHERE COALESCE(kind, 'film') = ? AND lower(title) = lower(?)
                 AND COALESCE(year, -1) = ? AND COALESCE(season, -1) = ?""",
            (kind, title, year if year is not None else -1,
             season if season is not None else -1),
        ).fetchone()

    def insert_wanted(self, title, year, search_query, notes=None, kind="film", season=None):
        """Add something missing. Returns (row, created) - never duplicates."""
        existing = self.find_wanted(title, year, kind, season)
        if existing is not None:
            return existing, False
        try:
            cursor = self.conn.execute(
                "INSERT INTO wanted (title, year, search_query, notes, kind, season) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, year, search_query, notes, kind, season),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            # Two searches raced; the unique index settled it.
            self.conn.rollback()
            existing = self.find_wanted(title, year, kind, season)
            if existing is None:
                raise
            return existing, False
        row = self.conn.execute(
            "SELECT * FROM wanted WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return row, True

    def get_wanted(self, status="wanted", limit=500):
        """Wanted entries, newest first. status=None returns every row."""
        if status:
            return self.conn.execute(
                "SELECT * FROM wanted WHERE status = ? ORDER BY added_at DESC, id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM wanted ORDER BY added_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()

    def set_wanted_status(self, wanted_id, status):
        """Set an entry's status. Rows are kept for history, never deleted."""
        if status not in self.WANTED_STATUSES:
            raise ValueError(
                f"Unknown status {status!r}; expected one of {', '.join(self.WANTED_STATUSES)}"
            )
        cursor = self.conn.execute(
            "UPDATE wanted SET status = ? WHERE id = ?", (status, wanted_id)
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return self.conn.execute(
            "SELECT * FROM wanted WHERE id = ?", (wanted_id,)
        ).fetchone()

    def count_wanted(self):
        """Tally of entries by status."""
        counts = {status: 0 for status in self.WANTED_STATUSES}
        for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM wanted GROUP BY status"):
            counts[row["status"]] = row["n"]
        return counts

    def close(self):
        self.conn.close()

    # ---- announcements ----------------------------------------------------

    def post_announcement(self, body, posted_by, url=None):
        """Put a note up for the household."""
        cursor = self.conn.execute(
            "INSERT INTO announcements (body, url, posted_by) VALUES (?, ?, ?)",
            (body, url, posted_by),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM announcements WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    def announcements(self, limit=3):
        """The notes still up, newest first."""
        return self.conn.execute(
            "SELECT * FROM announcements WHERE retired_at IS NULL "
            "ORDER BY posted_at DESC LIMIT ?", (limit,),
        ).fetchall()

    def retire_announcement(self, announcement_id):
        """Take a note down for everyone."""
        self.conn.execute(
            "UPDATE announcements SET retired_at = CURRENT_TIMESTAMP WHERE id = ?",
            (announcement_id,),
        )
        self.conn.commit()

