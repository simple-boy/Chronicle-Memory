import tempfile
import unittest
import sqlite3
import json

from memory_core import MemoryStore, extract_structure


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

    def test_graph_expansion_stays_within_user(self):
        self.store.add(request_id="a1", user_id="one", session_id="s", content="Alice manages Atlas.")
        self.store.add(request_id="a2", user_id="one", session_id="s", content="Atlas uses SQLite.")
        self.store.add(request_id="b1", user_id="two", session_id="s", content="Atlas uses Postgres.")
        results = self.store.search(user_id="one", query="What database does Alice's project use?", top_k=10)
        contents = [item["content"] for item in results]
        self.assertIn("Atlas uses SQLite.", contents)
        self.assertNotIn("Atlas uses Postgres.", contents)

    def test_search_returns_evidence_only(self):
        self.store.add(request_id="a", user_id="one", session_id="s", content="The launch is on 2026-08-07 in Shanghai.")
        result = self.store.search(user_id="one", query="When and where is the launch?")
        self.assertEqual(result[0]["content"], "The launch is on 2026-08-07 in Shanghai.")
        self.assertEqual(set(result[0]), {"id", "content"})

    def test_search_uses_indexed_candidates(self):
        if not self.store._fts_enabled:
            self.skipTest("SQLite build does not provide FTS5")
        self.store.add(request_id="a", user_id="one", session_id="s", content="Indexed retrieval evidence")
        self.store._load_user = lambda user_id: (_ for _ in ()).throw(AssertionError("full scan used"))
        result = self.store.search(user_id="one", query="indexed retrieval")
        self.assertEqual(result[0]["content"], "Indexed retrieval evidence")

    def test_async_annotation_refreshes_search_index(self):
        self.store.add(request_id="a", user_id="one", session_id="s", content="A quiet river valley")
        self.assertEqual(self.store.search(user_id="one", query="hydropower"), [])
        memory_id = self.store.add(request_id="a", user_id="one", session_id="s", content="A quiet river valley")
        self.store.update_model_terms(memory_id=memory_id, model_terms=["hydropower project"])
        result = self.store.search(user_id="one", query="hydropower")
        self.assertEqual(result[0]["content"], "A quiet river valley")

    def test_extracts_temporal_event_and_relations(self):
        structure = extract_structure("The Atlas project migrated from SQLite to Postgres in March 2026.")
        self.assertEqual(structure["event"]["event_time"], "2026-03")
        self.assertEqual(structure["event"]["event_time_key"], 202603)
        self.assertEqual(structure["event"]["type"], "migration")
        self.assertIn("atlas project", structure["entities"])
        self.assertIn(
            {"subject": "atlas project", "predicate": "changed_to", "object": "postgres", "previous": "sqlite"},
            structure["relations"],
        )

    def test_preserves_event_sequence_with_multiple_dates(self):
        structure = extract_structure(
            "The Atlas project started in January 2025. The Atlas project migrated from SQLite to Postgres in March 2026."
        )
        self.assertEqual(len(structure["events"]), 2)
        self.assertEqual(structure["events"][0]["event_time"], "2025-01")
        self.assertEqual(structure["events"][1]["event_time"], "2026-03")
        self.assertEqual(structure["events"][1]["type"], "migration")

    def test_extracts_chinese_relations(self):
        structure = extract_structure("小明负责星河项目，项目从SQLite迁移到Postgres，发生在2026年3月。")
        self.assertEqual(structure["event"]["event_time"], "2026-03")
        self.assertIn({"subject": "小明", "predicate": "负责", "object": "星河项目"}, structure["relations"])
        self.assertIn(
            {"subject": "项目", "predicate": "changed_to", "object": "postgres", "previous": "sqlite"},
            structure["relations"],
        )

    def test_two_hop_retrieval_joins_shared_entities(self):
        self.store.add(request_id="a", user_id="one", session_id="s", content="Alice manages Atlas.")
        self.store.add(
            request_id="b",
            user_id="one",
            session_id="s",
            content="The Atlas project migrated from SQLite to Postgres in March 2026.",
        )
        results = self.store.search(user_id="one", query="What database did Alice's project use later?", top_k=10)
        self.assertTrue(results)
        self.assertIn("Postgres", " ".join(item["content"] for item in results))

    def test_relative_time_prefers_latest_event(self):
        self.store.add(request_id="old", user_id="one", session_id="s", content="The Atlas review happened in January 2025.")
        self.store.add(request_id="new", user_id="one", session_id="s", content="The Atlas review happened in March 2026.")
        results = self.store.search(user_id="one", query="What happened to the Atlas review later?", top_k=2)
        self.assertEqual(results[0]["content"], "The Atlas review happened in March 2026.")

    def test_relative_order_uses_session_sequence_without_dates(self):
        self.store.add(request_id="old", user_id="one", session_id="s", content="Alice first used SQLite for Atlas.")
        self.store.add(request_id="new", user_id="one", session_id="s", content="Alice now uses Postgres for Atlas.")
        results = self.store.search(user_id="one", query="Which database did Alice use later?", top_k=2)
        self.assertEqual(results[0]["content"], "Alice now uses Postgres for Atlas.")

    def test_legacy_database_is_migrated(self):
        legacy = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        legacy.close()
        conn = sqlite3.connect(legacy.name)
        conn.execute(
            "CREATE TABLE memories (memory_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, request_id TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL, token_json TEXT NOT NULL, model_term_json TEXT NOT NULL, UNIQUE(user_id, request_id))"
        )
        conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-1", "legacy-user", "s", "r", "Legacy event in 2024.", 1.0, json.dumps(["legacy", "event"]), "[]"),
        )
        conn.commit()
        conn.close()
        store = MemoryStore(legacy.name)
        row = store._connection().execute("SELECT event_time, structure_json FROM memories WHERE memory_id='legacy-1'").fetchone()
        self.assertEqual(row["event_time"], "2024")
        self.assertIn("event", json.loads(row["structure_json"]))
        self.assertEqual(store.search(user_id="legacy-user", query="legacy event")[0]["content"], "Legacy event in 2024.")


if __name__ == "__main__":
    unittest.main()
