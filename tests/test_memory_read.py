"""Evidence retrieval, isolation, and a broad local latency smoke check."""

import os
import time
import unittest

from memory_core import MemoryStore


class MemoryReadTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store._connection().close()

    def add(self, request_id, user_id, session_id, content):
        return self.store.add_messages(
            request_id=request_id, user_id=user_id, session_id=session_id,
            messages=[{"role": "user", "content": content}],
        )

    def test_cross_session_retrieval_stays_within_user(self):
        self.add("a1", "alice", "morning", "I parked the blue bicycle beside the library.")
        self.add("a2", "alice", "evening", "I moved the blue bicycle beside the station.")
        self.add("b1", "bob", "evening", "Bob's blue bicycle is beside the museum.")
        rows = self.store.search(user_id="alice", query="Where was the blue bicycle?", top_k=10)
        contents = [row["content"] for row in rows]
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("library" in content for content in contents))
        self.assertTrue(any("station" in content for content in contents))
        self.assertFalse(any("museum" in content for content in contents))

    def test_single_long_session_can_fill_requested_top_k(self):
        for index in range(20):
            self.add(str(index), "alice", "long-session", f"Atlas evidence item {index}.")
        rows = self.store.search(user_id="alice", query="Atlas evidence item", top_k=20)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["id"] for row in rows}), 20)

    def test_search_returns_evidence_only_and_empty_list_when_no_match(self):
        self.add("launch", "alice", "s1", "The launch is in Shanghai on 2026-08-07.")
        rows = self.store.search(user_id="alice", query="Where is the launch?", top_k=5)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(set(row) == {"id", "content"} for row in rows))
        self.assertIn("Shanghai", rows[0]["content"])
        self.assertEqual(self.store.search(user_id="alice", query="unseenword74318", top_k=5), [])

    def test_options_can_supply_retrieval_cues_without_generating_an_answer(self):
        self.add("db", "alice", "s1", "The Atlas database is Postgres.")
        rows = self.store.search(
            user_id="alice", query="Which choice matches the stored database?",
            options=["SQLite", "Postgres"], top_k=5,
        )
        self.assertTrue(any("Postgres" in row["content"] for row in rows))
        self.assertTrue(all("answer" not in row for row in rows))

    def test_visit_query_finds_visited_evidence(self):
        self.add("trip", "alice", "s1", "I visited Paris.")
        for query in ("Where did I visit?", "What city did I visit?"):
            with self.subTest(query=query):
                rows = self.store.search(user_id="alice", query=query, top_k=1)
                self.assertEqual(len(rows), 1)
                self.assertIn("visited Paris", rows[0]["content"])

    def test_relevance_order_does_not_promote_weak_other_session_hit(self):
        for index in range(6):
            self.add(f"strong-{index}", "alice", "main", f"Atlas review Atlas review evidence {index}.")
        self.add("weak", "alice", "other", "Atlas.")
        rows = self.store.search(user_id="alice", query="Atlas review", top_k=7)
        self.assertEqual(len(rows), 7)
        self.assertTrue(all("review" in row["content"] for row in rows[:6]))
        self.assertEqual(rows[-1]["content"], "Atlas.")

    def test_latest_evidence_survives_long_history_candidate_limit(self):
        # The 820 older records exceed the lexical candidate limit for Top 100.
        for chunk in range(41):
            messages = [
                {"role": "user", "content": f"review review review review archived note {chunk * 20 + index}"}
                for index in range(20)
            ]
            self.store.add_messages(
                request_id=f"archive-{chunk}", user_id="alice", session_id="archive",
                messages=messages,
            )
        self.add("fresh", "alice", "archive", "review updated today")
        rows = self.store.search(user_id="alice", query="What happened in the latest review?", top_k=100)
        self.assertEqual(len(rows), 100)
        self.assertIn("review updated today", rows[0]["content"])

    def test_600_message_retrieval_latency_is_local_smoke_only(self):
        """Broad regression guard; printed timing is not an AML competition result."""
        for chunk in range(30):
            messages = [
                {"role": "user", "content": f"Archive record needle{chunk * 20 + index:04d} was stored."}
                for index in range(20)
            ]
            self.store.add_messages(
                request_id=f"chunk-{chunk}", user_id="alice", session_id=f"session-{chunk}",
                messages=messages,
            )
        start = time.perf_counter()
        rows = self.store.search(user_id="alice", query="needle0599", top_k=100)
        elapsed = time.perf_counter() - start
        print(f"LOCAL_SMOKE retrieval_600_messages_seconds={elapsed:.3f}")
        self.assertTrue(any("needle0599" in row["content"] for row in rows))
        self.assertLessEqual(len(rows), 100)
        limit = float(os.getenv("CHRONICLE_LATENCY_SMOKE_LIMIT_S", "15"))
        self.assertLess(elapsed, limit, "local smoke threshold; not a competition latency claim")


if __name__ == "__main__":
    unittest.main()
