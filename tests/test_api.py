"""Black-box HTTP checks for the public Cycle 2 Add/Search contract."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.tmp = tempfile.TemporaryDirectory()
        cls.log_path = Path(cls.tmp.name) / "server.log"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "HOST": "127.0.0.1", "PORT": str(port),
            "MEMORY_DB_PATH": str(Path(cls.tmp.name) / "api.sqlite3"),
            "MEMORY_API_KEY": "test-only-secret",
        })
        with cls.log_path.open("wb") as log:
            cls.process = subprocess.Popen(
                [sys.executable, str(cls.repo / "app.py")], cwd=cls.repo, env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                break
            try:
                with urlopen(cls.base_url + "/health", timeout=0.5) as response:
                    if 200 <= response.status < 300:
                        return
            except OSError:
                time.sleep(0.1)
        log = cls.log_path.read_text(encoding="utf-8", errors="replace")
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.tmp.cleanup()
        raise RuntimeError(f"HTTP service did not become healthy: {log}")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.tmp.cleanup()

    @classmethod
    def request(cls, method, path, payload=None, auth=True):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["X-Api-Key"] = "test-only-secret"
        request = Request(cls.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_health_is_open_even_when_add_search_require_key(self):
        status, body = self.request("GET", "/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        status, body = self.request("POST", "/search", {"query": "x", "user_id": "u", "top_k": 1}, auth=False)
        self.assertEqual(status, 401)
        self.assertIsInstance(body, dict)

    def test_add_then_search_matches_cycle2_required_json_schema(self):
        add = {
            "request_id": "api-add-1", "user_id": "api-alice", "session_id": "api-s1",
            "messages": [
                {"role": "user", "timestamp": 1704153600000,
                 "content": "I went to the supermarket yesterday."},
                {"role": "assistant", "content": "You visited the supermarket."},
            ],
        }
        status, body = self.request("POST", "/add", add)
        self.assertEqual(status, 200)
        self.assertIs(body.get("success"), True)
        for field in ("request_id", "user_id", "session_id"):
            self.assertEqual(body.get(field), add[field])
        status, result = self.request("POST", "/search", {
            "query": "Where did I go yesterday?", "user_id": "api-alice", "top_k": 100,
        })
        self.assertEqual(status, 200)
        self.assertIsInstance(result.get("data"), list)
        self.assertLessEqual(len(result["data"]), 100)
        self.assertTrue(any("supermarket" in row["content"] for row in result["data"]))
        for row in result["data"]:
            self.assertIsInstance(row.get("id"), str)
            self.assertTrue(row["id"])
            self.assertIsInstance(row.get("content"), str)
            self.assertTrue(row["content"])
            self.assertNotIn("answer", row)

    def test_retry_is_idempotent_and_changed_body_is_conflict(self):
        add = {
            "request_id": "api-retry", "user_id": "api-retry-user", "session_id": "api-s1",
            "messages": [{"role": "user", "content": "The secret color is indigo."}],
        }
        self.assertEqual(self.request("POST", "/add", add)[0], 200)
        self.assertEqual(self.request("POST", "/add", add)[0], 200)
        status, result = self.request("POST", "/search", {
            "query": "indigo", "user_id": "api-retry-user", "top_k": 10,
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(result["data"]), 1)
        changed = {**add, "messages": [{"role": "user", "content": "The secret color is green."}]}
        status, body = self.request("POST", "/add", changed)
        self.assertEqual(status, 409)
        self.assertIsInstance(body, dict)

    def test_invalid_schema_and_unknown_route_have_structured_errors(self):
        invalid_add = {
            "request_id": "old-shape", "user_id": "api-alice", "session_id": "api-s1",
            "content": "Old Cycle 1 content is not messages[].",
        }
        cases = [
            ("/add", invalid_add, 422),
            ("/add", {"request_id": "bad-role", "user_id": "u", "session_id": "s",
                      "messages": [{"role": "system", "content": "no"}]}, 422),
            ("/search", {"query": "x", "user_id": "u"}, 422),
            ("/search", {"query": "x", "user_id": "u", "top_k": True}, 422),
            ("/search", {"query": "x", "user_id": "u", "top_k": 1, "options": "A"}, 422),
            ("/unknown", {}, 404),
        ]
        for path, payload, expected in cases:
            with self.subTest(path=path, payload=payload):
                status, body = self.request("POST", path, payload)
                self.assertEqual(status, expected)
                self.assertIsInstance(body, dict)

    def test_no_results_is_data_empty_array(self):
        status, body = self.request("POST", "/search", {
            "query": "unseenword74318", "user_id": "never-added", "top_k": 100,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body, {"data": []})


if __name__ == "__main__":
    unittest.main()
