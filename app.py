"""Cycle 2 Textual Add/Search service."""

from __future__ import annotations

import json
import os
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from memory_core import MemoryStore


STORE = MemoryStore(os.getenv("MEMORY_DB_PATH", "data/memories.sqlite3"))
API_KEY = os.getenv("MEMORY_API_KEY", "").strip()
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(32 * 1024 * 1024)))


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ChronicleMemory/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        candidates = [self.headers.get("X-Api-Key", "")]
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer ") or auth.lower().startswith("token "):
            candidates.append(auth.split(" ", 1)[1])
        return any(hmac.compare_digest(candidate, API_KEY) for candidate in candidates)

    def _send(self, status: int, value: Any) -> None:
        data = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            self._send(HTTPStatus.OK, {"status": "ok", "service": "chronicle-memory-v2"})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/add", "/search"):
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError(f"Content-Length must be between 1 and {MAX_REQUEST_BYTES} bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if path == "/add":
                response = self._add(payload)
            else:
                response = self._search(payload)
            self._send(HTTPStatus.OK, response)
        except ValueError as exc:
            status = HTTPStatus.CONFLICT if "request_id was reused" in str(exc) else HTTPStatus.UNPROCESSABLE_ENTITY
            self._send(status, {"detail": {"reason": str(exc)}})
        except Exception as exc:
            print(f"request failed: {type(exc).__name__}")
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": {"reason": "internal_error"}})

    @staticmethod
    def _required(payload: dict[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        return value

    def _add(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(payload, "request_id")
        user_id = self._required(payload, "user_id")
        session_id = self._required(payload, "session_id")
        messages = payload.get("messages")
        STORE.add_messages(
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            messages=messages,
        )
        return {
            "success": True,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
        }

    def _search(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._required(payload, "user_id")
        query = self._required(payload, "query")
        top_k = payload.get("top_k")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        options = payload.get("options")
        if options is not None and (not isinstance(options, list) or any(not isinstance(option, str) for option in options)):
            raise ValueError("options must be an array of strings")
        return {
            "data": STORE.search(
                user_id=user_id,
                query=query,
                top_k=top_k,
                options=options,
                include_source=True,
            )
        }


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    print(f"Chronicle Memory listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
