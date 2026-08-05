"""Evidence-only memory storage and retrieval for the Agent Memory benchmark."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


_TERM_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def normalize(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def terms(text: str) -> list[str]:
    """Tokenize Latin words and CJK characters, preserving exact phrase matching."""
    return _TERM_RE.findall(text.casefold())


def query_terms(text: str) -> list[str]:
    raw = terms(text)
    expanded = list(raw)
    # Small, transparent expansion for common English question forms.
    aliases = {
        "when": ["date", "time", "year"],
        "where": ["location", "place"],
        "why": ["reason", "because"],
        "who": ["person", "author", "team"],
        "how": ["method", "process", "way"],
    }
    for token in raw:
        expanded.extend(aliases.get(token, ()))
    return expanded


def stable_id(user_id: str, session_id: str, content: str) -> str:
    raw = f"{user_id}\0{session_id}\0{normalize(content)}".encode("utf-8")
    return "mem_" + hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class Memory:
    memory_id: str
    user_id: str
    session_id: str
    request_id: str
    content: str
    created_at: float
    token_list: tuple[str, ...]
    model_terms: tuple[str, ...]


class MemoryStore:
    def __init__(self, db_path: str = "data/memories.sqlite3") -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        import os

        folder = os.path.dirname(self.db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = self._connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                token_json TEXT NOT NULL,
                model_term_json TEXT NOT NULL,
                UNIQUE(user_id, request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(user_id, session_id, memory_id);
            """
        )
        conn.commit()

    def add(
        self,
        *,
        request_id: str,
        user_id: str,
        session_id: str,
        content: str,
        model_terms: Iterable[str] = (),
    ) -> str:
        if not all(isinstance(value, str) and value.strip() for value in (request_id, user_id, session_id, content)):
            raise ValueError("request_id, user_id, session_id, and content are required")
        if len(content) > 120_000:
            raise ValueError("content is too long")

        memory_id = stable_id(user_id, session_id, content)
        token_list = terms(content)
        model_list = [normalize(term) for term in model_terms if isinstance(term, str) and term.strip()]
        with self._lock:
            conn = self._connection()
            existing = conn.execute(
                "SELECT memory_id FROM memories WHERE user_id=? AND request_id=?",
                (user_id, request_id),
            ).fetchone()
            if existing:
                return str(existing["memory_id"])
            duplicate = conn.execute(
                "SELECT memory_id FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
            if duplicate:
                return str(duplicate["memory_id"])
            conn.execute(
                "INSERT INTO memories(memory_id,user_id,session_id,request_id,content,created_at,token_json,model_term_json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    user_id,
                    session_id,
                    request_id,
                    content.strip(),
                    time.time(),
                    json.dumps(token_list, ensure_ascii=False),
                    json.dumps(model_list, ensure_ascii=False),
                ),
            )
            conn.commit()
        return memory_id

    def _load_user(self, user_id: str) -> list[Memory]:
        rows = self._connection().execute(
            "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [
            Memory(
                memory_id=row["memory_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                request_id=row["request_id"],
                content=row["content"],
                created_at=float(row["created_at"]),
                token_list=tuple(json.loads(row["token_json"])),
                model_terms=tuple(json.loads(row["model_term_json"])),
            )
            for row in rows
        ]

    @staticmethod
    def _idf(documents: list[Memory], token: str) -> float:
        df = sum(token in set(doc.token_list) or token in set(doc.model_terms) for doc in documents)
        return math.log(1.0 + (len(documents) - df + 0.5) / (df + 0.5))

    def search(self, *, user_id: str, query: str, top_k: int = 100, session_id: str | None = None) -> list[dict[str, str]]:
        if not isinstance(user_id, str) or not user_id.strip() or not isinstance(query, str) or not query.strip():
            raise ValueError("user_id and query are required")
        top_k = max(1, min(int(top_k), 100))
        documents = self._load_user(user_id)
        if not documents:
            return []

        q_terms = query_terms(query)
        q_set = set(q_terms)
        phrase = normalize(query)
        scored: list[tuple[float, Memory]] = []
        avg_len = max(1.0, sum(len(doc.token_list) for doc in documents) / len(documents))
        for doc in documents:
            doc_terms = list(doc.token_list) + list(doc.model_terms)
            counts: dict[str, int] = {}
            for token in doc_terms:
                counts[token] = counts.get(token, 0) + 1
            doc_len = max(1, len(doc.token_list))
            bm25 = 0.0
            for token in q_set:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                idf = self._idf(documents, token)
                denominator = tf + 1.5 * (0.75 + 0.25 * doc_len / avg_len)
                bm25 += idf * (tf * 2.5) / denominator

            normalized_content = normalize(doc.content)
            phrase_bonus = 3.0 if len(phrase) > 3 and phrase in normalized_content else 0.0
            overlap = len(q_set.intersection(doc_terms)) / max(1, len(q_set))
            years_in_query = set(_YEAR_RE.findall(query))
            years_in_doc = set(_YEAR_RE.findall(doc.content))
            temporal_question = bool(q_set.intersection({"when", "date", "time", "year"}))
            has_date_signal = bool(years_in_doc or re.search(r"\b\d{1,4}[-/]\d{1,2}(?:[-/]\d{1,4})?\b", doc.content))
            temporal_bonus = 1.25 if years_in_query.intersection(years_in_doc) else (0.85 if temporal_question and has_date_signal else 0.0)
            session_bonus = 0.75 if session_id and doc.session_id == session_id else 0.0
            age_days = max(0.0, (time.time() - doc.created_at) / 86400.0)
            recency_bonus = 0.35 * math.exp(-age_days / 90.0)
            score = bm25 + phrase_bonus + overlap + temporal_bonus + session_bonus + recency_bonus
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda pair: (-pair[0], -pair[1].created_at, pair[1].memory_id))
        # Mild diversity: do not spend the whole top-k on one source session when alternatives are relevant.
        selected: list[Memory] = []
        session_counts: dict[str, int] = {}
        for score, doc in scored:
            count = session_counts.get(doc.session_id, 0)
            if count >= 5 and len(selected) < top_k - 1:
                continue
            selected.append(doc)
            session_counts[doc.session_id] = count + 1
            if len(selected) >= top_k:
                break
        return [{"id": doc.memory_id, "content": doc.content} for doc in selected]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
