"""Independent edge-case review; synthetic evidence, no official questions."""

import unittest
from datetime import datetime, timezone

from memory_core import MemoryStore
from temporal import parse_event_time, query_time_window


def ts(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


class UpgradeReviewTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store.close()

    def write(self, request, content, timestamp=None):
        message = {"role": "user", "content": content}
        if timestamp is not None:
            message["timestamp"] = timestamp
        return self.store.add_messages(request_id=request, user_id="u", session_id=request, messages=[message])[0]

    def test_denial_of_old_scalar_value_does_not_revoke_different_current_value(self):
        self.write("old", "My phone is 555-0101.")
        current = self.write("current", "My phone is 555-0202.")
        self.write("deny-old", "My phone is not 555-0101.")
        state = self.store._connection().execute("SELECT status FROM memory_facts WHERE memory_id=? AND polarity=1", (current,)).fetchone()["status"]
        self.assertEqual(state, "active")
        rows = self.store.search(user_id="u", query="What is my current phone number?", top_k=1)
        self.assertEqual(rows[0]["id"], current)

    def test_denial_of_same_scalar_value_revokes_that_value(self):
        old = self.write("old", "My phone is 555-0101.")
        denial = self.write("deny", "My phone is not 555-0101.")
        rows = self.store._connection().execute("SELECT memory_id,status,polarity FROM memory_facts WHERE predicate='phone'").fetchall()
        by_id = {row["memory_id"]: dict(row) for row in rows}
        self.assertEqual(by_id[old]["status"], "superseded")
        self.assertEqual(by_id[denial]["status"], "active")
        self.assertEqual(by_id[denial]["polarity"], 0)

    def test_explicit_before_date_excludes_clearly_later_events(self):
        earlier = self.write("earlier", "Atlas launched on March 2, 2025.")
        later = self.write("later", "Atlas launched on March 4, 2025.")
        rows = self.store.search(user_id="u", query="What did Atlas do before March 3, 2025?", top_k=10)
        self.assertIn(earlier, {row["id"] for row in rows})
        self.assertNotIn(later, {row["id"] for row in rows})

    def test_explicit_after_date_uses_event_date_instead_of_report_date(self):
        earlier = self.write("earlier", "Atlas launched on March 2, 2025.", ts("2025-03-10"))
        later = self.write("later", "Atlas launched on March 4, 2025.", ts("2025-03-10"))
        rows = self.store.search(user_id="u", query="What did Atlas do after March 3, 2025?", top_k=10)
        self.assertIn(later, {row["id"] for row in rows})
        self.assertNotIn(earlier, {row["id"] for row in rows})

    def test_full_range_does_not_reward_only_its_first_boundary(self):
        first = self.write("first", "Atlas review happened in 2024.")
        last = self.write("last", "Atlas review happened in 2026.")
        self.store._fts_enabled = False  # Equal lexical evidence, no FTS tie order.
        captured = {}
        original = self.store._select_evidence

        def observe(scored, *args):
            captured.update({doc.memory_id: score for score, doc in scored})
            return original(scored, *args)

        self.store._select_evidence = observe
        self.store.search(user_id="u", query="Atlas events from 2024 to 2026", top_k=2)
        self.assertAlmostEqual(captured[first], captured[last], delta=0.00001)

    def test_absolute_date_with_weekday_is_not_lost_to_unanchored_weekday(self):
        text = "Saturday, May 3rd, 2025"
        self.assertEqual(parse_event_time(text)[0], "2025-05-03")
        self.assertEqual(query_time_window("What happened on " + text + "?"), ("2025-05-03", "2025-05-04"))

    def test_hypothetical_change_cannot_bypass_safe_fact_parser(self):
        old = self.write("actual", "Atlas uses SQLite.")
        self.write("example", "Atlas changed from SQLite to Postgres in a hypothetical example.")
        conn = self.store._connection()
        self.assertEqual(conn.execute("SELECT status FROM memories WHERE memory_id=?", (old,)).fetchone()["status"], "active")
        self.assertEqual(conn.execute("SELECT status FROM memory_facts WHERE memory_id=?", (old,)).fetchone()["status"], "active")

    def test_coarse_month_crossing_boundary_is_not_treated_as_its_first_day(self):
        coarse = self.write("coarse", "Atlas review happened in May 2025.")
        later = self.write("later", "Atlas review happened on May 22, 2025.")
        earlier = self.write("earlier", "Atlas review happened on May 2, 2025.")
        rows = self.store.search(user_id="u", query="What did Atlas do after May 20, 2025?", top_k=10)
        ids = {row["id"] for row in rows}
        self.assertIn(later, ids)
        self.assertIn(coarse, ids)  # Possible supporting context; exact day unknown.
        self.assertNotIn(earlier, ids)


if __name__ == "__main__":
    unittest.main()
