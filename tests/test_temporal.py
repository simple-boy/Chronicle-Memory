"""Source timestamps, relative event dates, and retrieval order."""

import unittest

from memory_core import MemoryStore, extract_structure


class TemporalMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store._connection().close()

    def test_yesterday_is_resolved_against_message_timestamp(self):
        # 2024-01-02 00:00:00 UTC; timestamp is Unix milliseconds.
        structure = extract_structure("Yesterday I went to the supermarket.", 1704153600000)
        self.assertEqual(structure["event"]["event_time"], "2024-01-01")
        self.assertEqual(structure["event"]["temporal_expression"].casefold(), "yesterday")

    def test_relative_date_without_source_timestamp_stays_unknown(self):
        structure = extract_structure("Yesterday I went to the supermarket.")
        self.assertIsNone(structure["event"]["event_time"])
        self.assertEqual(structure["event"]["temporal_expression"].casefold(), "yesterday")

    def test_absolute_date_query_ranks_matching_source_event_first(self):
        self.store.add_messages(
            request_id="places", user_id="alice", session_id="s1",
            messages=[
                {"role": "user", "timestamp": 1704153600000,
                 "content": "Yesterday I went to the supermarket."},
                {"role": "user", "timestamp": 1704153600000,
                 "content": "Today I went to school."},
            ],
        )
        rows = self.store.search(
            user_id="alice", query="Where did I go on 2024-01-01?", top_k=2
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("supermarket", rows[0]["content"])

    def test_yesterday_query_prefers_yesterday_source_statement(self):
        self.store.add_messages(
            request_id="daily", user_id="alice", session_id="s1",
            messages=[
                {"role": "user", "timestamp": 1704153600000,
                 "content": "Yesterday I went to the supermarket."},
                {"role": "user", "timestamp": 1704153600000,
                 "content": "Today I went to school."},
            ],
        )
        rows = self.store.search(user_id="alice", query="Where did I go yesterday?", top_k=2)
        self.assertIn("supermarket", rows[0]["content"])

    def test_latest_explicit_event_is_ranked_before_older_event(self):
        self.store.add_messages(
            request_id="older", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "The Atlas review happened in January 2025."}],
        )
        self.store.add_messages(
            request_id="newer", user_id="alice", session_id="s2",
            messages=[{"role": "user", "content": "The Atlas review happened in March 2026."}],
        )
        rows = self.store.search(user_id="alice", query="What happened at the latest Atlas review?", top_k=2)
        self.assertEqual(len(rows), 2)
        self.assertIn("March 2026", rows[0]["content"])

    def test_multiple_clauses_keep_event_sequence_and_dates(self):
        structure = extract_structure(
            "Atlas started in January 2025. Atlas migrated in March 2026."
        )
        self.assertEqual(
            [event["event_time"] for event in structure["events"]], ["2025-01", "2026-03"]
        )

    def test_before_and_after_retrieve_adjacent_events_across_sessions(self):
        self.store.add_messages(
            request_id="prior", user_id="alice", session_id="day-1",
            messages=[{"role": "user", "timestamp": 1704067200000,
                       "content": "I went to the supermarket."}],
        )
        self.store.add_messages(
            request_id="later", user_id="alice", session_id="day-2",
            messages=[{"role": "user", "timestamp": 1704153600000,
                       "content": "I had a meeting."}],
        )
        before = self.store.search(user_id="alice", query="What did I do before the meeting?", top_k=2)
        after = self.store.search(user_id="alice", query="What did I do after the supermarket?", top_k=2)
        self.assertIn("supermarket", before[0]["content"])
        self.assertIn("meeting", after[0]["content"])

    def test_latest_preference_uses_cross_session_ingest_order_without_source_time(self):
        self.store.add_messages(
            request_id="old", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "I prefer coffee now."}],
        )
        self.store.add_messages(
            request_id="new", user_id="alice", session_id="s2",
            messages=[{"role": "user", "content": "I prefer tea."}],
        )
        rows = self.store.search(
            user_id="alice", query="What do I prefer now?", top_k=2, include_source=True,
        )
        self.assertIn("prefer tea", rows[0]["content"])
        self.assertTrue(all("ingested_at=" in row["content"] for row in rows))


if __name__ == "__main__":
    unittest.main()
