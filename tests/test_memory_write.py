"""Cycle 2 ordered messages, atomic Add, and retry semantics."""

import tempfile
import unittest
from pathlib import Path

from memory_core import MemoryStore


class MemoryWriteTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store._connection().close()

    def test_batch_keeps_each_message_role_time_and_order(self):
        messages = [
            {"role": "user", "timestamp": 1704153600000, "content": "I visited the library."},
            {"role": "assistant", "timestamp": 1704153601000, "content": "You visited the library."},
        ]
        ids = self.store.add_messages(
            request_id="write-1", user_id="alice", session_id="session-1", messages=messages
        )
        rows = self.store._connection().execute(
            "SELECT memory_id,content,role,timestamp_ms,message_index,sequence_no "
            "FROM memories WHERE user_id=? ORDER BY sequence_no", ("alice",)
        ).fetchall()
        self.assertEqual(len(ids), 2)
        self.assertEqual([row["memory_id"] for row in rows], ids)
        self.assertEqual([row["content"] for row in rows], [m["content"] for m in messages])
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual([row["timestamp_ms"] for row in rows], [1704153600000, 1704153601000])
        self.assertEqual([row["message_index"] for row in rows], [0, 1])
        self.assertEqual([row["sequence_no"] for row in rows], [1, 2])

    def test_exact_retry_is_idempotent_and_changed_payload_is_rejected(self):
        messages = [{"role": "user", "content": "My preferred city is Kyoto."}]
        args = dict(request_id="retry-1", user_id="alice", session_id="session-1", messages=messages)
        first = self.store.add_messages(**args)
        self.assertEqual(self.store.add_messages(**args), first)
        self.assertEqual(self.store._connection().execute("SELECT count(*) FROM memories").fetchone()[0], 1)
        changed = [{"role": "user", "content": "My preferred city is Osaka."}]
        with self.assertRaisesRegex(ValueError, "request_id was reused"):
            self.store.add_messages(
                request_id="retry-1", user_id="alice", session_id="session-1", messages=changed
            )
        self.assertEqual(self.store._connection().execute("SELECT count(*) FROM memories").fetchone()[0], 1)

    def test_same_text_twice_in_one_batch_remains_two_source_events(self):
        ids = self.store.add_messages(
            request_id="repeat", user_id="alice", session_id="session-1",
            messages=[
                {"role": "user", "content": "I went to school."},
                {"role": "user", "content": "I went to school."},
            ],
        )
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(self.store._connection().execute("SELECT count(*) FROM memories").fetchone()[0], 2)

    def test_source_content_preserves_leading_and_trailing_whitespace(self):
        source = "  I visited Paris.\n"
        memory_id = self.store.add_messages(
            request_id="raw-format", user_id="alice", session_id="session-1",
            messages=[{"role": "user", "content": source}],
        )[0]
        row = self.store._connection().execute(
            "SELECT content FROM memories WHERE memory_id=?", (memory_id,),
        ).fetchone()
        self.assertEqual(row["content"], source)

    def test_second_message_failure_rolls_back_whole_chunk(self):
        original = self.store._replace_structure_indexes
        calls = 0

        def fail_on_second(conn, memory_id, user_id, structure):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected indexing failure")
            return original(conn, memory_id, user_id, structure)

        self.store._replace_structure_indexes = fail_on_second
        with self.assertRaisesRegex(RuntimeError, "injected indexing failure"):
            self.store.add_messages(
                request_id="atomic", user_id="alice", session_id="session-1",
                messages=[
                    {"role": "user", "content": "First observation."},
                    {"role": "assistant", "content": "Second observation."},
                ],
            )
        conn = self.store._connection()
        self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT count(*) FROM add_requests").fetchone()[0], 0)
        if self.store._fts_enabled:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0], 0)

    def test_message_schema_rejects_bad_role_content_and_timestamp(self):
        bad_batches = [
            [],
            [{"role": "system", "content": "secret"}],
            [{"role": "user", "content": ""}],
            [{"role": "user", "content": "hello", "timestamp": True}],
            [{"role": "user", "content": "hello", "timestamp": "1704153600000"}],
        ]
        for index, messages in enumerate(bad_batches):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.store.add_messages(
                    request_id=f"bad-{index}", user_id="alice", session_id="session-1",
                    messages=messages,
                )
        self.assertEqual(self.store._connection().execute("SELECT count(*) FROM memories").fetchone()[0], 0)

    def test_committed_add_survives_store_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "new-data-directory" / "memory.sqlite3")
            first = MemoryStore(path)
            try:
                first.add_messages(
                    request_id="persist", user_id="alice", session_id="s1",
                    messages=[{"role": "user", "content": "My archive code is Q7K."}],
                )
            finally:
                first.close()
            reopened = MemoryStore(path)
            try:
                rows = reopened.search(user_id="alice", query="archive code Q7K", top_k=1)
                self.assertEqual(len(rows), 1)
                self.assertIn("Q7K", rows[0]["content"])
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
