import tempfile
import unittest

from memory_core import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)

    def test_add_is_idempotent(self):
        args = dict(
            request_id="req-1",
            user_id="user-a",
            session_id="session-a",
            content="The project moved from SQLite to Postgres in 2026.",
        )
        first = self.store.add(**args)
        second = self.store.add(**args)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.search(user_id="user-a", query="Postgres 2026")), 1)

    def test_users_are_isolated(self):
        self.store.add(request_id="a", user_id="one", session_id="s", content="Private blue bicycle")
        self.store.add(request_id="b", user_id="two", session_id="s", content="Private blue bicycle")
        result = self.store.search(user_id="one", query="blue bicycle")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Private blue bicycle")

    def test_search_returns_evidence_only(self):
        self.store.add(request_id="a", user_id="one", session_id="s", content="The launch is on 2026-08-07 in Shanghai.")
        result = self.store.search(user_id="one", query="When and where is the launch?")
        self.assertEqual(result[0]["content"], "The launch is on 2026-08-07 in Shanghai.")
        self.assertEqual(set(result[0]), {"id", "content"})


if __name__ == "__main__":
    unittest.main()
