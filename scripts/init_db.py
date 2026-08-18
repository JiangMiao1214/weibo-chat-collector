import argparse
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "weibo_chat_collector.sqlite3"
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import apply_database_migrations  # noqa: E402


def initialize_database(db_path: Path, seed_placeholders: bool) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        apply_database_migrations(connection)

        if seed_placeholders:
            seed_default_accounts(connection)


def seed_default_accounts(connection: sqlite3.Connection) -> None:
    accounts = [("account_a", "group_a"), ("account_b", "group_b")]

    for account_name, group_name in accounts:
        cursor = connection.execute(
            "SELECT id FROM weibo_accounts WHERE display_name = ?",
            (account_name,),
        )
        account_row = cursor.fetchone()
        if account_row is None:
            cursor = connection.execute(
                "INSERT INTO weibo_accounts (display_name, notes) VALUES (?, ?)",
                (account_name, "Placeholder account; replace with the real Weibo account name."),
            )
            account_id = cursor.lastrowid
        else:
            account_id = account_row[0]

        cursor = connection.execute(
            "SELECT id FROM chat_groups WHERE account_id = ? AND name = ?",
            (account_id, group_name),
        )
        if cursor.fetchone() is None:
            connection.execute(
                "INSERT INTO chat_groups (account_id, name, description) VALUES (?, ?, ?)",
                (account_id, group_name, "Placeholder group; replace with the real group name."),
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the local SQLite database.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path.",
    )
    parser.add_argument(
        "--seed-placeholders",
        action="store_true",
        help="Seed account_a/group_a and account_b/group_b placeholders.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    initialize_database(args.db, args.seed_placeholders)
    print(f"Database initialized at {args.db}")
