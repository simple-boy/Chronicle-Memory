"""Entity and relation metadata must support evidence retrieval."""

import unittest

from memory_core import MemoryStore, extract_structure


class EntityMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store._connection().close()

    def test_transfer_sentence_identifies_recipient_and_item(self):
        structure = extract_structure("Alice gave Bob a book.")
        self.assertIn(
            {"subject": "alice", "predicate": "gave_to", "object": "bob", "item": "book"},
            structure["relations"],
        )
        self.assertTrue({"alice", "bob", "book"}.issubset(set(structure["entities"])))

    def test_two_hop_search_connects_person_project_and_database(self):
        self.store.add_messages(
            request_id="person", user_id="alice", session_id="s1",
            messages=[{"role": "user", "content": "Alice manages Atlas."}],
        )
        self.store.add_messages(
            request_id="project", user_id="alice", session_id="s2",
            messages=[{"role": "assistant", "content": "Atlas uses Postgres."}],
        )
        rows = self.store.search(
            user_id="alice", query="Which database does Alice's project use?", top_k=10
        )
        evidence = " ".join(row["content"] for row in rows)
        self.assertIn("Alice manages Atlas", evidence)
        self.assertIn("Atlas uses Postgres", evidence)

    def test_graph_expansion_never_crosses_user_boundary(self):
        for request_id, user_id, content in [
            ("a1", "alice", "Alice manages Atlas."),
            ("a2", "alice", "Atlas uses SQLite."),
            ("b1", "bob", "Atlas uses Postgres."),
        ]:
            self.store.add_messages(
                request_id=request_id, user_id=user_id, session_id="s1",
                messages=[{"role": "user", "content": content}],
            )
        rows = self.store.search(user_id="alice", query="What database does Alice's project use?", top_k=10)
        evidence = " ".join(row["content"] for row in rows)
        self.assertIn("SQLite", evidence)
        self.assertNotIn("Postgres", evidence)


if __name__ == "__main__":
    unittest.main()
