from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import apply_database_migrations  # noqa: E402


EXPECTED_JOB_COLUMNS = {
    "source_group_id",
    "timezone_name",
    "page_size",
    "next_max_mid",
    "checkpoint_oldest_at",
    "page_count",
    "attempt_count",
    "duplicate_count",
    "filtered_red_packet_count",
    "filtered_system_notice_count",
    "stop_code",
    "stop_reason",
    "last_http_status",
    "last_error_code",
    "stop_requested_at",
    "confirmed_at",
    "last_progress_at",
    "heartbeat_at",
    "resume_not_before",
    "updated_at",
}

EXPECTED_ATTEMPT_COLUMNS = {
    "id",
    "job_id",
    "attempt_no",
    "status",
    "start_max_mid",
    "end_max_mid",
    "started_at",
    "finished_at",
    "page_count",
    "total_seen_count",
    "inserted_count",
    "skipped_count",
    "duplicate_count",
    "filtered_red_packet_count",
    "filtered_system_notice_count",
    "failed_count",
    "stop_code",
    "stop_reason",
    "last_http_status",
    "last_error_code",
    "created_at",
}

EXPECTED_PAGE_COLUMNS = {
    "id",
    "job_id",
    "attempt_id",
    "job_page_no",
    "attempt_page_no",
    "request_max_mid",
    "next_max_mid",
    "newest_sent_at",
    "oldest_sent_at",
    "raw_count",
    "in_range_count",
    "inserted_count",
    "skipped_count",
    "duplicate_count",
    "filtered_red_packet_count",
    "filtered_system_notice_count",
    "outside_range_count",
    "fetched_at",
    "committed_at",
}

EXPECTED_RUNTIME_COLUMNS = {
    "id",
    "worker_id",
    "lease_expires_at",
    "current_job_id",
    "global_request_count",
    "next_allowed_request_at",
    "last_request_at",
    "updated_at",
}


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_collection_jobs_is_upgraded_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE weibo_accounts (
                        id INTEGER PRIMARY KEY,
                        display_name TEXT NOT NULL
                    );
                    CREATE TABLE chat_groups (
                        id INTEGER PRIMARY KEY,
                        account_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        source_group_id TEXT
                    );
                    CREATE TABLE collection_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER NOT NULL,
                        group_id INTEGER NOT NULL,
                        range_start TEXT NOT NULL,
                        range_end TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        started_at TEXT,
                        finished_at TEXT,
                        total_seen_count INTEGER NOT NULL DEFAULT 0,
                        inserted_count INTEGER NOT NULL DEFAULT 0,
                        skipped_count INTEGER NOT NULL DEFAULT 0,
                        failed_count INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT,
                        collector_type TEXT NOT NULL DEFAULT 'manual_import',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO weibo_accounts (id, display_name)
                    VALUES (1, 'legacy-account');
                    INSERT INTO chat_groups (id, account_id, name, source_group_id)
                    VALUES (7, 1, 'legacy-group', 'source-group-7');
                    INSERT INTO collection_jobs (
                        id, account_id, group_id, range_start, range_end
                    ) VALUES (
                        11, 1, 7, '2026-08-01 00:00:00', '2026-08-02 00:00:00'
                    );
                    """
                )

                apply_database_migrations(connection)
                apply_database_migrations(connection)

                job_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(collection_jobs)")
                }
                self.assertTrue(EXPECTED_JOB_COLUMNS.issubset(job_columns))

                migrated_job = connection.execute(
                    """
                    SELECT source_group_id, timezone_name, page_size, next_max_mid,
                           page_count, attempt_count, duplicate_count,
                           filtered_red_packet_count, filtered_system_notice_count,
                           updated_at
                    FROM collection_jobs
                    WHERE id = 11
                    """
                ).fetchone()
                self.assertEqual(
                    migrated_job[:-1],
                    ("source-group-7", "Asia/Shanghai", 20, "0", 0, 0, 0, 0, 0),
                )
                self.assertIsNotNone(migrated_job[-1])

                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "collection_job_attempts",
                        "collection_job_pages",
                        "collector_runtime_state",
                    }.issubset(tables)
                )
                for table_name, expected_columns in (
                    ("collection_job_attempts", EXPECTED_ATTEMPT_COLUMNS),
                    ("collection_job_pages", EXPECTED_PAGE_COLUMNS),
                    ("collector_runtime_state", EXPECTED_RUNTIME_COLUMNS),
                ):
                    actual_columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table_name})")
                    }
                    self.assertEqual(actual_columns, expected_columns)
                self.assertEqual(
                    connection.execute(
                        "SELECT id, global_request_count FROM collector_runtime_state"
                    ).fetchall(),
                    [(1, 0)],
                )

                attempt_id = connection.execute(
                    """
                    INSERT INTO collection_job_attempts (job_id, attempt_no)
                    VALUES (11, 1)
                    """
                ).lastrowid
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO collection_job_attempts (job_id, attempt_no)
                        VALUES (11, 1)
                        """
                    )

                connection.execute(
                    """
                    INSERT INTO collection_job_pages (
                        job_id, attempt_id, job_page_no, attempt_page_no,
                        request_max_mid
                    ) VALUES (11, ?, 1, 1, '0')
                    """,
                    (attempt_id,),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO collection_job_pages (
                            job_id, attempt_id, job_page_no, attempt_page_no,
                            request_max_mid
                        ) VALUES (11, ?, 2, 2, '0')
                        """,
                        (attempt_id,),
                    )


if __name__ == "__main__":
    unittest.main()
