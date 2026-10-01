"""Retention cleanup must not race with Add or leave source text in SQLite files."""

import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from memory_core import MemoryStore


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "purge_expired.py"
SPEC = importlib.util.spec_from_file_location("purge_expired", SCRIPT)
purge_expired = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(purge_expired)


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "memory.sqlite3"
        self.store = MemoryStore(str(self.db_path))

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def _make_expired(self, user_id):
        conn = self.store._connection()
        conn.execute(
            "UPDATE memories SET created_at=? WHERE user_id=?",
            (time.time() - 40 * 86400, user_id),
        )
        conn.commit()

    def test_apply_selects_expired_scopes_inside_writer_transaction(self):
        self.store.add_messages(
            request_id="old", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "Expired Alice fact."}],
        )
        self._make_expired("alice")
        self.store.close()

        real_connect = sqlite3.connect
        selected_under_lock = []

        class ObservedConnection:
            def __init__(self, connection):
                object.__setattr__(self, "connection", connection)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def __setattr__(self, name, value):
                setattr(self.connection, name, value)

            def execute(self, sql, *parameters):
                if sql.startswith("SELECT user_id,COUNT(*) AS records FROM memories"):
                    # If false, another Add could commit after selection and
                    # before the broad DELETE WHERE user_id below.
                    selected_under_lock.append(self.connection.in_transaction)
                return self.connection.execute(sql, *parameters)

        def observed_connect(*args, **kwargs):
            return ObservedConnection(real_connect(*args, **kwargs))

        with patch.object(purge_expired.sqlite3, "connect", observed_connect):
            result = purge_expired.purge(self.db_path, days=29, apply=True)

        self.assertEqual(result["records"], 1)
        self.assertEqual(selected_under_lock, [True])

    def test_default_dry_run_keeps_expired_source(self):
        self.store.add_messages(
            request_id="old", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "Keep this during dry run."}],
        )
        self._make_expired("alice")
        result = purge_expired.purge(self.db_path, days=29, apply=False)
        self.assertEqual(result, {"scopes": 1, "records": 1, "applied": 0})
        self.assertEqual(
            self.store._connection().execute("SELECT COUNT(*) FROM memories WHERE user_id='alice'").fetchone()[0],
            1,
        )

    def test_apply_handles_database_without_fts5_table(self):
        self.store.add_messages(
            request_id="old", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "An expired source fact."}],
        )
        self._make_expired("alice")
        self.store._connection().execute("DROP TABLE memories_fts")
        self.store._connection().commit()
        self.store.close()
        result = purge_expired.purge(self.db_path, days=29, apply=True)
        self.assertEqual(result["records"], 1)

    def test_apply_removes_deleted_source_bytes_from_database_and_wal(self):
        secret = "UNIQUE_SECRET_9F12_DO_NOT_RETAIN"
        self.store.add_messages(
            request_id="private", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": secret}],
        )
        self._make_expired("alice")
        self.store.close()

        result = purge_expired.purge(self.db_path, days=29, apply=True)
        self.assertEqual(result["scopes"], 1)
        self.assertEqual(result["records"], 1)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        finally:
            conn.close()
        for path in self.db_path.parent.glob(self.db_path.name + "*"):
            with self.subTest(path=path.name):
                self.assertNotIn(secret.encode("utf-8"), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
