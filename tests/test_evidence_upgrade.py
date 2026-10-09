"""Failure-mode checks for the Textual Smoke upgrade (not official questions)."""

import tempfile
from pathlib import Path
import unittest

from memory_core import MemoryStore, query_terms


class EvidenceUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store.close()

    def write(self, request, text, role="user", timestamp=None, session=None):
        message = {"role": role, "content": text}
        if timestamp is not None:
            message["timestamp"] = timestamp
        return self.store.add_messages(request_id=request, user_id="u", session_id=session or request, messages=[message])[0]

    def test_interrogative_expansion_precedes_stopword_removal(self):
        self.assertIn("because", query_terms("Why was the delivery delayed?"))
        self.assertIn("date", query_terms("When did the delivery arrive?"))
        self.assertIn("database", query_terms("What database did Atlas use?"))

    def test_scalar_current_state_without_explicit_from_to(self):
        old = self.write("old", "My phone number is 555-0101.")
        new = self.write("new", "My phone number is 555-0202.")
        result = self.store.search(user_id="u", query="What is my phone number?", top_k=2, include_source=True)
        self.assertEqual(result[0]["id"], new)
        self.assertEqual({row["id"] for row in result}, {old, new})
        self.assertIn("superseded", result[1]["content"])

    def test_multi_attribute_source_preserves_other_active_attribute(self):
        old = self.write("old", "My phone number is 555-0101. My email is river@example.test.")
        self.write("new", "My phone number is 555-0202.")
        result = self.store.search(user_id="u", query="What is my email?", top_k=1, include_source=True)
        self.assertEqual(result[0]["id"], old)
        self.assertIn("email:active", result[0]["content"])
        self.assertIn("phone:superseded", result[0]["content"])

    def test_speaker_and_named_subject_do_not_overwrite_user(self):
        own = self.write("own", "I live in Kyoto.")
        self.write("assistant", "I live in Paris.", role="assistant")
        self.write("other", "Alice lives in Berlin.")
        result = self.store.search(user_id="u", query="Where do I live now?", top_k=1)
        self.assertEqual(result[0]["id"], own)

    def test_assistant_claim_about_user_preserves_self_report_history(self):
        own = self.write("own", "I live in Kyoto.", timestamp=1704067200000)
        guess = self.write("guess", "You currently live in Paris.", role="assistant", timestamp=1704153600000)
        state = self.store._connection().execute("SELECT status FROM memory_facts WHERE memory_id=?", (own,)).fetchone()["status"]
        self.assertEqual(state, "active")
        rows = self.store.search(user_id="u", query="Where do I currently live according to my own statement?", top_k=2)
        self.assertEqual(rows[0]["id"], own)
        self.assertIn(guess, {row["id"] for row in rows})

    def test_current_user_property_prefers_self_report_without_source_phrase(self):
        own = self.write("own", "My phone number is 555-0101.")
        self.write("guess", "Your phone number is 555-0202.", role="assistant")
        rows = self.store.search(user_id="u", query="What is my current phone number?", top_k=1)
        self.assertEqual(rows[0]["id"], own)

    def test_assistant_source_question_can_retrieve_assistant_claim(self):
        self.write("own", "I live in Kyoto.")
        guess = self.write("guess", "You live in Paris.", role="assistant")
        rows = self.store.search(user_id="u", query="What did the assistant say about where I live?", top_k=1)
        self.assertEqual(rows[0]["id"], guess)

    def test_missing_assistant_timestamp_does_not_reorder_user_history(self):
        current = self.write("current", "My phone is 555-0202.", timestamp=1704153600000)
        self.write("guess", "Your phone is 555-0303.", role="assistant")
        self.write("old", "My phone is 555-0101.", timestamp=1704067200000)
        rows = self.store.search(user_id="u", query="What is my current phone number?", top_k=1)
        self.assertEqual(rows[0]["id"], current)

    def test_range_interior_date_outranks_later_trip_report(self):
        trip = self.write("trip", "I visited the Atlas workshop in Oslo from June 10 to June 12, 2026.", timestamp=1782864000000)
        self.write("outside", "I visited the Atlas office in Lima on July 20, 2026.", timestamp=1782950400000)
        rows = self.store.search(user_id="u", query="Where was I for Atlas on 2026-06-11?", top_k=2)
        self.assertEqual(rows[0]["id"], trip)

    def test_interval_candidate_retrieval_without_lexical_seed(self):
        trip = self.write("trip", "I stayed in Oslo from June 10 to June 12, 2026.", timestamp=1782864000000)
        self.write("outside", "I stayed in Lima on July 20, 2026.", timestamp=1782950400000)
        self.store._fts_query = lambda text: ""
        self.store.features = frozenset({"temporal"})
        rows = self.store.search(user_id="u", query="What happened on 2026-06-11?", top_k=1)
        self.assertEqual(rows[0]["id"], trip)

    def test_out_of_order_state_uses_source_time_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(str(Path(folder) / "state.sqlite3"))
            new = store.add_messages(request_id="new", user_id="u", session_id="s2", messages=[{"role": "user", "timestamp": 1704153600000, "content": "My phone is 555-0202."}])[0]
            store.add_messages(request_id="old", user_id="u", session_id="s1", messages=[{"role": "user", "timestamp": 1704067200000, "content": "My phone is 555-0101."}])
            store.close()
            store = MemoryStore(str(Path(folder) / "state.sqlite3"))
            try:
                self.assertEqual(store.search(user_id="u", query="What is my current phone?", top_k=1)[0]["id"], new)
            finally:
                store.close()

    def test_latest_query_ignores_unrelated_recent_topic(self):
        self.write("old", "Atlas review happened in January 2025.")
        newest = self.write("new", "Atlas review happened in March 2026.")
        self.write("noise", "Boreal review happened in June 2026.")
        rows = self.store.search(user_id="u", query="What happened at the latest Atlas review?", top_k=3)
        self.assertEqual(rows[0]["id"], newest)

    def test_chain_bundle_includes_bridge_before_unrelated_database(self):
        first = self.write("first", "Alice manages Nimbus.")
        second = self.write("second", "Nimbus uses Postgres.")
        for i in range(20):
            self.write(f"noise-{i}", f"Repository Module{i} uses SQLite database.")
        rows = self.store.search(user_id="u", query="Which database is used by the project Alice manages?", top_k=5)
        self.assertIn(first, {row["id"] for row in rows})
        self.assertIn(second, {row["id"] for row in rows})

    def test_global_summary_covers_sessions_beyond_recent_notes(self):
        early = self.write("early", "I completed the lighthouse trip.", session="early")
        for i in range(20):
            self.write(f"late-{i}", f"Today I logged routine receipt {i}.", session="late")
        rows = self.store.search(user_id="u", query="Summarize my entire history across sessions.", top_k=5)
        self.assertIn(early, {row["id"] for row in rows})

    def test_calendar_window_uses_event_time_instead_of_report_time(self):
        self.write("days", "Two days ago I visited Kyoto.", timestamp=1704326400000)
        self.write("today", "Today I visited Osaka.", timestamp=1704326400000)
        rows = self.store.search(user_id="u", query="Where did I visit on January 2, 2024?", top_k=2)
        self.assertIn("Kyoto", rows[0]["content"])

    def test_failed_chunk_rolls_back_prior_property_state(self):
        old = self.write("old", "My phone is 555-0101.")
        original = self.store._replace_structure_indexes
        calls = 0

        def fail(conn, memory_id, user_id, structure):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected second-message failure")
            return original(conn, memory_id, user_id, structure)

        self.store._replace_structure_indexes = fail
        with self.assertRaises(RuntimeError):
            self.store.add_messages(request_id="broken", user_id="u", session_id="s", messages=[
                {"role": "user", "content": "My phone is 555-0202."},
                {"role": "user", "content": "My email is another@example.test."},
            ])
        row = self.store._connection().execute("SELECT status FROM memory_facts WHERE memory_id=?", (old,)).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(self.store._connection().execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)

    def test_single_message_keeps_attribute_state_order(self):
        self.write("correction", "My phone is 555-0101. My phone is 555-0202.")
        states = self.store._connection().execute("SELECT value,status FROM memory_facts WHERE predicate='phone'").fetchall()
        self.assertEqual({row["value"]: row["status"] for row in states}, {"555-0101": "superseded", "555-0202": "active"})


if __name__ == "__main__":
    unittest.main()
