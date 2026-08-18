from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator

from .settings import PROJECT_ROOT, get_settings


SCHEMA_PATH = PROJECT_ROOT / "scripts" / "schema.sql"


COLLECTION_JOB_COLUMN_MIGRATIONS = {
    "source_group_id": "TEXT",
    "timezone_name": "TEXT NOT NULL DEFAULT 'Asia/Shanghai'",
    "page_size": "INTEGER NOT NULL DEFAULT 20",
    "next_max_mid": "TEXT NOT NULL DEFAULT '0'",
    "checkpoint_oldest_at": "TEXT",
    "page_count": "INTEGER NOT NULL DEFAULT 0",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "duplicate_count": "INTEGER NOT NULL DEFAULT 0",
    "filtered_red_packet_count": "INTEGER NOT NULL DEFAULT 0",
    "filtered_system_notice_count": "INTEGER NOT NULL DEFAULT 0",
    "stop_code": "TEXT",
    "stop_reason": "TEXT",
    "last_http_status": "INTEGER",
    "last_error_code": "TEXT",
    "stop_requested_at": "TEXT",
    "confirmed_at": "TEXT",
    "last_progress_at": "TEXT",
    "heartbeat_at": "TEXT",
    "resume_not_before": "TEXT",
    # SQLite does not allow adding a column with CURRENT_TIMESTAMP as its
    # default to a non-empty table. A trigger below supplies the value for
    # databases upgraded from the original schema.
    "updated_at": "TEXT",
}


POST_SCHEMA_MIGRATIONS = """
CREATE TRIGGER IF NOT EXISTS trg_collection_jobs_set_updated_at
AFTER INSERT ON collection_jobs
FOR EACH ROW
WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE collection_jobs
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;
"""


def get_database_path() -> Path:
    return get_settings().database_path


def connect() -> sqlite3.Connection:
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def apply_database_migrations(
    connection: sqlite3.Connection,
    schema_path: Path = SCHEMA_PATH,
) -> None:
    """Apply the full schema plus explicit upgrades for existing databases."""
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")

    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'collection_jobs'"
    ).fetchone()
    if table_exists:
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(collection_jobs)")
        }
        for column_name, column_definition in COLLECTION_JOB_COLUMN_MIGRATIONS.items():
            if column_name not in existing_columns:
                connection.execute(
                    f'ALTER TABLE collection_jobs ADD COLUMN "{column_name}" '
                    f"{column_definition}"
                )

    connection.executescript(schema_path.read_text(encoding="utf-8"))
    connection.executescript(POST_SCHEMA_MIGRATIONS)
    connection.execute(
        """
        UPDATE collection_jobs
        SET source_group_id = (
            SELECT source_group_id
            FROM chat_groups
            WHERE chat_groups.id = collection_jobs.group_id
        )
        WHERE source_group_id IS NULL
        """
    )
    connection.execute(
        "UPDATE collection_jobs SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO collector_runtime_state (id) VALUES (1)"
    )


def initialize_database() -> None:
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        apply_database_migrations(connection)


def get_connection() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def connection_context() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
