"""Check diagnostic integrity rather than tuning retrieval to the fixture."""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


DIAGNOSTIC = Path(__file__).resolve().parents[1] / "evaluation" / "smoke_upgrade_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("smoke_upgrade_diagnostic_tests", DIAGNOSTIC)
diagnostic = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diagnostic
SPEC.loader.exec_module(diagnostic)


class UpgradeDiagnosticTests(unittest.TestCase):
    def test_checked_in_fixture_is_exact_deterministic_generation(self):
        actual = diagnostic.load_cases(diagnostic.DEFAULT_INPUT)
        expected = diagnostic.generate_cases()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 120)
        self.assertEqual(len({case["case_id"] for case in actual}), 120)
        self.assertEqual(len({case["writes"][0]["user_id"] for case in actual}), 120)
        for group in diagnostic.GROUPS:
            self.assertEqual(sum(case["group"] == group for case in actual), 8)
        for case in actual:
            self.assertEqual(sum(write["record_id"].startswith("noise-") for write in case["writes"]), 20)
            for question in case["queries"]:
                self.assertEqual(question["top_k"], 10)
                self.assertTrue(set(question["gold_record_ids"]) <= {write["record_id"] for write in case["writes"]})

    def test_two_and_three_record_evidence_are_required_individually(self):
        cases = diagnostic.generate_cases(1)
        two = next(case for case in cases if case["group"] == "two_hop")
        three = next(case for case in cases if case["group"] == "causal_chain")
        broad = next(case for case in cases if case["group"] == "broad_summary")
        self.assertEqual(len(two["queries"][0]["gold_record_ids"]), 2)
        self.assertEqual(len(three["queries"][0]["gold_record_ids"]), 3)
        self.assertEqual(len(broad["queries"][0]["gold_record_ids"]), 5)
        project = "Project Atlas00"
        tail = next(write for write in three["writes"] if write["record_id"] == "connector-cause")
        self.assertNotIn(project, tail["messages"][0]["content"])

    def test_complete_support_and_first_support_mrr_are_distinct(self):
        question = {"gold_record_ids": ["a", "b"]}
        rows = [{"id": "one", "content": "A"}, {"id": "two", "content": "unrelated"}]
        result = diagnostic.evaluate_query(question, rows, {"one": "a", "two": "noise"})
        self.assertEqual(result["recall_at_1"], 0.5)
        self.assertEqual(result["recall_at_10"], 0.5)
        self.assertEqual(result["mrr"], 1.0)
        self.assertEqual(result["all_support_at_10"], 0.0)
        self.assertIsNone(result["expected_first_success"])

    def test_expected_first_requires_record_and_source_attribution(self):
        question = {"gold_record_ids": ["current"], "gold_first_record_id": "current",
                    "expected_source": {"role": "user", "session": "verified", "timestamp_ms": 0}}
        rows = [{"id": "new", "content": "[role=assistant; session=verified; source_time=1970-01-01T00:00:00+00:00] value"}]
        result = diagnostic.evaluate_query(question, rows, {"new": "current"})
        self.assertEqual(result["expected_first_success"], 1.0)
        self.assertEqual(result["expected_first_attributed_success"], 0.0)
        rows[0]["content"] = "[role=user; session=verified; source_time=1970-01-01T00:00:00+00:00] value"
        result = diagnostic.evaluate_query(question, rows, {"new": "current"})
        self.assertEqual(result["expected_first_attributed_success"], 1.0)

    def test_legacy_gold_substrings_and_budget_validation(self):
        metrics = diagnostic.evaluate_query({"gold_substrings": ["Blue Bicycle"]}, [{"id": "r", "content": "blue bicycle"}], {})
        self.assertEqual(metrics["all_support_at_10"], 1.0)
        with self.assertRaisesRegex(ValueError, "budget"):
            diagnostic.evaluate_query({"gold_substrings": ["x"]}, [{"id": str(i), "content": "x"} for i in range(11)], {})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.jsonl"
            case = {"writes": [], "queries": [{"gold_substrings": ["x"], "top_k": 100}]}
            path.write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "top_k=10"):
                diagnostic.load_cases(path)

    def test_dynamic_core_import_handles_dataclasses(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "memory_core.py"
            path.write_text("from __future__ import annotations\nfrom dataclasses import dataclass\n@dataclass\nclass Memory:\n    content: str\nclass MemoryStore:\n    pass\n", encoding="utf-8")
            module = diagnostic.load_core(path, "dataclass_check")
            self.assertEqual(module.Memory("raw").content, "raw")
            self.assertIs(sys.modules[module.__name__], module)

    def test_annotations_never_reach_write_or_search(self):
        calls = []

        class FakeStore:
            def __init__(self, path, **kwargs):
                self.path = path

            def add_messages(self, *, request_id, user_id, session_id, messages):
                calls.append(("write", request_id, user_id, session_id, messages))
                return ["memory-one"]

            def search(self, *, user_id, query, options, top_k, include_source):
                calls.append(("search", user_id, query, options, top_k, include_source))
                return [{"id": "memory-one", "content": "stored raw evidence"}]

            def _connection(self):
                return self

            def execute(self, statement):
                return self

            def close(self):
                pass

        case = {"case_id": "isolated", "group": "probe", "writes": [
            {"record_id": "support", "request_id": "r", "user_id": "u", "session_id": "s", "messages": [{"role": "user", "content": "stored raw evidence"}], "gold_attack": "must not enter core"}],
            "queries": [{"user_id": "u", "query": "retrieve evidence", "gold_record_ids": ["support"], "gold_substrings": ["hidden_gold_marker"]}]}
        with patch.object(diagnostic, "load_core", return_value=types.SimpleNamespace(MemoryStore=FakeStore)):
            result = diagnostic.run_store([case], DIAGNOSTIC, "probe")
        self.assertEqual(result["overall"]["all_support_at_10"], 1.0)
        self.assertEqual(calls[1], ("search", "u", "retrieve evidence", None, 10, True))
        self.assertNotIn("hidden_gold_marker", str(calls))
        self.assertNotIn("gold_attack", str(calls))
        self.assertNotIn("must not enter core", str(calls))


if __name__ == "__main__":
    unittest.main()
