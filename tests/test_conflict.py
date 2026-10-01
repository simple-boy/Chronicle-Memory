"""Conservative explicit state transitions with retained history."""

import unittest

from memory_core import MemoryStore


class ConflictMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store._connection().close()

    def add(self, request_id, user_id, session_id, content):
        return self.store.add_messages(
            request_id=request_id, user_id=user_id, session_id=session_id,
            messages=[{"role": "user", "content": content}],
        )[0]

    def test_explicit_change_marks_old_fact_but_preserves_both_sources(self):
        old_id = self.add("old", "alice", "s1", "The Atlas project used SQLite.")
        new_id = self.add("new", "alice", "s2", "The Atlas project changed from SQLite to Postgres.")
        conn = self.store._connection()
        old = conn.execute(
            "SELECT status,superseded_by,content FROM memories WHERE memory_id=?", (old_id,)
        ).fetchone()
        new = conn.execute("SELECT status FROM memories WHERE memory_id=?", (new_id,)).fetchone()
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], new_id)
        self.assertEqual(new["status"], "active")
        current = self.store.search(user_id="alice", query="What database does Atlas use now?", top_k=2)
        historical = self.store.search(user_id="alice", query="What database did Atlas use before?", top_k=2)
        self.assertIn("Postgres", current[0]["content"])
        self.assertTrue(any("used SQLite" in row["content"] for row in historical))

    def test_new_preference_does_not_falsely_delete_older_preference(self):
        old_id = self.add("old", "alice", "s1", "I like coffee.")
        new_id = self.add("new", "alice", "s2", "I prefer tea now.")
        rows = self.store._connection().execute(
            "SELECT memory_id,status FROM memories WHERE memory_id IN (?,?)", (old_id, new_id)
        ).fetchall()
        self.assertEqual({row["status"] for row in rows}, {"active"})
        current = self.store.search(user_id="alice", query="What do I prefer now?", top_k=2)
        self.assertIn("tea", current[0]["content"])
        current_paraphrase = self.store.search(user_id="alice", query="What do I like now?", top_k=2)
        self.assertIn("tea", current_paraphrase[0]["content"])

    def test_change_for_one_user_cannot_supersede_another_user(self):
        bob_id = self.add("bob-old", "bob", "s1", "The Atlas project used SQLite.")
        self.add("alice-change", "alice", "s2", "The Atlas project changed from SQLite to Postgres.")
        status = self.store._connection().execute(
            "SELECT status FROM memories WHERE memory_id=?", (bob_id,)
        ).fetchone()["status"]
        self.assertEqual(status, "active")

    def test_backfilled_older_change_does_not_supersede_later_source_fact(self):
        newer_id = self.store.add_messages(
            request_id="latest-source", user_id="alice", session_id="s1",
            messages=[{"role": "user", "timestamp": 1704153600000,
                       "content": "The Atlas project used SQLite."}],
        )[0]
        self.store.add_messages(
            request_id="older-source", user_id="alice", session_id="s2",
            messages=[{"role": "user", "timestamp": 1704067200000,
                       "content": "The Atlas project changed from SQLite to Postgres."}],
        )
        row = self.store._connection().execute(
            "SELECT status FROM memories WHERE memory_id=?", (newer_id,),
        ).fetchone()
        self.assertEqual(row["status"], "active")

    def test_explicit_forget_removes_only_matching_users_fact_and_instruction(self):
        old_id = self.add("alice-coffee", "alice", "s1", "I like coffee.")
        self.add("alice-tea", "alice", "s2", "I prefer tea now.")
        bob_id = self.add("bob-coffee", "bob", "s1", "I like coffee.")
        forget_args = dict(
            request_id="alice-forget", user_id="alice", session_id="s3",
            messages=[{"role": "user", "content": "Please forget that I like coffee."}],
        )
        first = self.store.add_messages(**forget_args)
        self.assertEqual(self.store.add_messages(**forget_args), first)
        conn = self.store._connection()
        self.assertIsNone(conn.execute("SELECT 1 FROM memories WHERE memory_id=?", (old_id,)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT 1 FROM memories WHERE memory_id=?", (bob_id,)).fetchone())
        alice = self.store.search(user_id="alice", query="What do I prefer now?", top_k=10)
        self.assertTrue(alice)
        self.assertIn("tea", alice[0]["content"])
        self.assertFalse(any("coffee" in row["content"].casefold() or "forget" in row["content"].casefold()
                             for row in alice))
        self.assertTrue(any("coffee" in row["content"] for row in self.store.search(
            user_id="bob", query="coffee", top_k=10,
        )))


if __name__ == "__main__":
    unittest.main()
