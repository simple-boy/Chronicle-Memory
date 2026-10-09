"""Grounding and semantic-scope checks for the lexical fact parser."""

import unittest

from fact_index import extract_facts, fact_index_terms


class FactIndexTests(unittest.TestCase):
    def facts(self, text, role="user"):
        facts = extract_facts(text, role)
        for fact in facts:
            self.assertEqual(text[fact["source_start"]:fact["source_end"]], fact["text"])
            self.assertIn(fact["text"], text)
            self.assertIsInstance(fact["polarity"], bool)
            self.assertIsInstance(fact["exclusive"], bool)
            self.assertIsInstance(fact["explicit_update"], bool)
        return facts

    def test_precise_contacts_and_multiple_attributes(self):
        text = "My name is River. My phone number is +1 555-0101, and my email is River@example.test. My address is 12 Main St, Apt 4."
        facts = self.facts(text)
        self.assertEqual([(f["predicate"], f["value"]) for f in facts], [
            ("name", "river"), ("phone", "+1 555-0101"),
            ("email", "river@example.test"), ("address", "12 main st, apt 4"),
        ])
        self.assertTrue(all(f["subject"] == "__user__" and f["exclusive"] for f in facts))

    def test_speaker_and_named_entity_attribution(self):
        self.assertEqual(self.facts("I live in Kyoto.")[0]["subject"], "__user__")
        self.assertEqual(self.facts("I live in Paris.", "assistant")[0]["subject"], "__assistant__")
        self.assertEqual(self.facts("Your email is river@example.test.", "assistant")[0]["subject"], "__user__")
        facts = self.facts("Alice lives in Berlin. The project Atlas uses database Postgres. Bob's phone is 555-0303.")
        self.assertEqual([(f["subject"], f["predicate"], f["value"]) for f in facts], [
            ("alice", "location", "berlin"), ("atlas", "uses:database", "postgres"),
            ("bob", "phone", "555-0303"),
        ])

    def test_explicit_updates_extract_new_value_only_for_scalar(self):
        facts = self.facts("My phone changed from 555-0101 to 555-0202. Alice's email was updated to new@example.test.")
        self.assertEqual([(f["subject"], f["value"]) for f in facts], [
            ("__user__", "555-0202"), ("alice", "new@example.test"),
        ])
        self.assertTrue(all(f["explicit_update"] for f in facts))
        fact = self.facts("I updated my address from 12 Main St to 18 Lake Ave.")[0]
        self.assertEqual((fact["predicate"], fact["value"]), ("address", "18 lake ave"))
        self.assertEqual(self.facts("Atlas changed from Postgres to SQLite."), [])

    def test_preference_is_not_implicitly_exclusive(self):
        facts = self.facts("I like coffee. I now prefer tea.")
        self.assertEqual([(f["predicate"], f["value"], f["polarity"], f["exclusive"]) for f in facts], [
            ("preference:general", "coffee", True, False),
            ("preference:general", "tea", True, False),
        ])
        facts = self.facts("I prefer tea instead of coffee.")
        self.assertEqual([(f["value"], f["polarity"]) for f in facts], [("coffee", False), ("tea", True)])
        self.assertTrue(all(f["explicit_update"] for f in facts))

    def test_negation_does_not_become_positive(self):
        for text in ("I no longer like coffee.", "I don't like coffee anymore.", "Alice does not prefer coffee."):
            with self.subTest(text=text):
                facts = self.facts(text)
                self.assertEqual([(f["value"], f["polarity"], f["exclusive"]) for f in facts], [("coffee", False, False)])
        self.assertFalse(self.facts("My phone is not 555-0101.")[0]["polarity"])
        self.assertEqual(self.facts("I never liked coffee."), [])
        self.assertEqual(self.facts("Alice is not a doctor."), [])

    def test_statement_order_preserved_in_one_message(self):
        facts = self.facts("My phone is 555-0101. My phone is 555-0202. I like coffee. I no longer like coffee.")
        self.assertEqual([f["value"] for f in facts], ["555-0101", "555-0202", "coffee", "coffee"])
        self.assertEqual([f["source_start"] for f in facts], sorted(f["source_start"] for f in facts))
        self.assertTrue(facts[-2]["polarity"])
        self.assertFalse(facts[-1]["polarity"])

    def test_reaffirmation_is_not_lost_after_same_message_negation(self):
        facts = self.facts("I like coffee. I no longer like coffee. I like coffee.")
        self.assertEqual([f["polarity"] for f in facts], [True, False, True])
        self.assertLess(facts[1]["source_start"], facts[2]["source_start"])

    def test_retrospective_and_negative_scalar_do_not_claim_new_current_value(self):
        facts = self.facts("My phone is 555-0202. My phone was 555-0101.")
        self.assertEqual([f["value"] for f in facts], ["555-0202"])
        facts = self.facts("My phone is 555-0202. My phone is not 555-0101.")
        self.assertEqual([(f["value"], f["polarity"], f["exclusive"]) for f in facts], [
            ("555-0202", True, True), ("555-0101", False, False),
        ])

    def test_explicit_preference_and_database_replacement(self):
        facts = self.facts("I changed my preference from coffee to tea. Atlas's database changed from Postgres to SQLite.")
        self.assertEqual([(f["predicate"], f["value"], f["polarity"]) for f in facts], [
            ("preference:general", "coffee", False), ("preference:general", "tea", True),
            ("uses:database", "postgres", False), ("uses:database", "sqlite", True),
        ])

    def test_multiple_properties_under_one_explicit_subject(self):
        facts = self.facts("Alice lives in Berlin and works at Acme.")
        self.assertEqual([(f["subject"], f["predicate"], f["value"]) for f in facts], [
            ("alice", "location", "berlin"), ("alice", "employer", "acme"),
        ])
        facts = self.facts("我的电话是123，我的邮箱是lin@example.test。")
        self.assertEqual([(f["predicate"], f["value"]) for f in facts], [("phone", "123"), ("email", "lin@example.test")])

    def test_questions_quotes_and_hypotheses_not_written(self):
        rejected = [
            "Is my phone 555-0101?", "My phone is 555-0101?", "Where does Alice live?",
            "If I live in Paris, I will walk.", "Imagine Alice lives in Berlin.",
            'Alice said "I live in Berlin."', '"My phone is 555-0101."',
            "Quote: Alice lives in Paris.", "I might work at Acme.",
            "假设我住在北京。", "我的手机号是123吗？", "据说Alice住在上海。",
        ]
        for text in rejected:
            with self.subTest(text=text):
                self.assertEqual(self.facts(text), [])
        self.assertEqual(self.facts("Where does Alice live? My phone is 555-0101.")[0]["predicate"], "phone")

    def test_chinese_properties_state_and_named_subject(self):
        facts = self.facts("我的姓名是小林。我的手机号是13800138000。我的邮箱是LIN@example.test。我住在上海。Alice在Acme工作。项目Atlas使用数据库Postgres。")
        self.assertEqual([(f["subject"], f["predicate"], f["value"]) for f in facts], [
            ("__user__", "name", "小林"), ("__user__", "phone", "13800138000"),
            ("__user__", "email", "lin@example.test"), ("__user__", "location", "上海"),
            ("alice", "employer", "acme"), ("atlas", "uses:database", "postgres"),
        ])
        facts = self.facts("我的电话从123改为456。我喜欢咖啡。我现在偏好茶。我不再喜欢咖啡。")
        self.assertEqual([(f["predicate"], f["value"], f["polarity"]) for f in facts], [
            ("phone", "456", True), ("preference:general", "咖啡", True),
            ("preference:general", "茶", True), ("preference:general", "咖啡", False),
        ])

    def test_unknown_pronoun_and_adjective_not_assigned(self):
        self.assertEqual(self.facts("She lives in Berlin."), [])
        self.assertEqual(self.facts("Alice is happy."), [])
        self.assertEqual(self.facts("Alice is a doctor.")[0]["predicate"], "occupation")
        self.assertEqual(self.facts("I live in Kyoto because my friend works there."), [])

    def test_index_hints_do_not_create_a_new_fact(self):
        facts = self.facts("I live in Kyoto. Atlas uses Postgres.")
        original = [dict(f) for f in facts]
        terms = fact_index_terms(facts)
        self.assertIn("location", terms)
        self.assertIn("database", terms)
        self.assertIn("kyoto", terms)
        self.assertNotIn("__user__", terms)
        self.assertEqual(facts, original)
        self.assertEqual(fact_index_terms([]), [])


if __name__ == "__main__":
    unittest.main()
