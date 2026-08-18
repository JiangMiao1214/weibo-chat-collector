from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.weibo_api import (  # noqa: E402
    RunWeiboApiCollectionRequest,
    create_weibo_api_collection,
)
from app.collectors.weibo_api import CookieProfileStore  # noqa: E402
from app.database import apply_database_migrations  # noqa: E402


class CollectionJobApiTests(unittest.TestCase):
    def test_concurrent_create_keeps_one_unfinished_logical_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(
                database_path=root / "collector.sqlite3",
                weibo_api_auth_dir=root / "auth",
                weibo_api_timezone="Asia/Shanghai",
                weibo_api_page_size=20,
            )
            with sqlite3.connect(settings.database_path) as connection:
                apply_database_migrations(connection)
                connection.execute("INSERT INTO weibo_accounts (display_name) VALUES ('账号')")
                account_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO chat_groups (account_id, name, source_group_id)
                    VALUES (?, '群聊', '4761715839862414')
                    """,
                    (account_id,),
                )
                group_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            CookieProfileStore(settings.weibo_api_auth_dir).save(
                account_id,
                [{"name": "SUB", "value": "test", "domain": ".weibo.com"}],
            )
            request = RunWeiboApiCollectionRequest(
                account_id=account_id,
                group_id=group_id,
                range_start="2026-08-01 00:00:00",
                range_end="2026-08-02 00:00:00",
            )
            barrier = threading.Barrier(2)
            results: list[dict] = []
            errors: list[HTTPException] = []

            def create() -> None:
                barrier.wait()
                try:
                    results.append(create_weibo_api_collection(request))
                except HTTPException as error:
                    errors.append(error)

            with (
                patch("app.database.get_settings", return_value=settings),
                patch("app.api.weibo_api.get_settings", return_value=settings),
            ):
                threads = [threading.Thread(target=create) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(results), 1)
            self.assertEqual([error.status_code for error in errors], [409])
            with sqlite3.connect(settings.database_path) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM collection_jobs WHERE collector_type = 'weibo_api_v2'"
                ).fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
