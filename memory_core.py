"""Evidence-only memory storage, temporal events, relations, and multi-hop retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fact_index import extract_facts, fact_index_terms, supports_state_update
from temporal import event_time_windows, parse_event_time, query_time_window


_TERM_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_QUERY_STOPWORDS = {
    "the", "a", "an", "this", "that", "it", "and", "or", "to", "of", "for", "with", "on", "in",
    "what", "which", "who", "why", "how", "did", "does", "do", "is", "are", "was", "were", "be",
    "i", "me", "my", "you", "your", "的", "是", "了", "吗", "呢", "请", "问", "什", "么", "哪", "个", "和", "与",
}
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DATE_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})[-/](?P<month>\d{1,2})(?:[-/](?P<day>\d{1,2}))?(?!\d)")
_CN_DATE_RE = re.compile(r"(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?:(?P<day>\d{1,2})日)?")
_MONTH_YEAR_RE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_TEMPORAL_ENTITY_NAMES = set(_MONTHS) | {name[:3] for name in _MONTHS} | {"sept"} | {
    "what", "which", "when", "where", "who", "why", "how", "did", "does", "do", "is", "are",
    "use", "used", "later", "after", "before", "current", "currently", "latest", "database",
}
_EN_RELATION_RE = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 _-]{0,60}?)\s+"
    r"(?P<predicate>reviewed|reviews|uses|used|prefers|preferred|likes|liked|owns|owned|works on|worked on|leads|led|manages|managed|visited|visits|located in|is in|is owned by|is managed by|is used by|belongs to|part of|depends on|depends upon|supports|contains|has|runs on|built with|deployed on)\s+"
    r"(?P<object>[A-Za-z0-9][^,.;!?]*?)(?=\s+(?:in|on|at|during|before|after|because|and)\b|[,.;!?]|$)",
    re.IGNORECASE,
)
_EN_CHANGE_RE = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 _-]{0,60}?)\s+"
    r"(?P<verb>moved|migrated|switched|changed|updated|replaced)\s+"
    r"from\s+(?P<old>[A-Za-z0-9.+#_-]+)\s+to\s+(?P<new>[A-Za-z0-9.+#_-]+)",
    re.IGNORECASE,
)
_EN_REPLACE_RE = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 _-]{0,60}?)\s+replaced\s+(?P<old>[A-Za-z0-9.+#_-]+)\s+with\s+(?P<new>[A-Za-z0-9.+#_-]+)",
    re.IGNORECASE,
)
_CN_CHANGE_RE = re.compile(r"(?P<subject>[^，。；,.;!?]{1,30}?)(?:从|由)(?P<old>[^，。；,.;!?]{1,30}?)(?:迁移到|切换到|改为|换成)(?P<new>[^，。；,.;!?]{1,30})")
_CN_RELATION_RE = re.compile(
    r"(?P<subject>[^，。；,.;!?]{1,30}?)(?P<predicate>使用|喜欢|偏好|负责|位于|在|依赖|访问)(?P<object>[^，。；,.;!?]{1,40})"
)
_EN_GIVE_RE = re.compile(
    r"\b(?P<giver>[A-Z][A-Za-z0-9_-]*)\s+(?:gave|gives)\s+"
    r"(?P<recipient>[A-Z][A-Za-z0-9_-]*)\s+(?:a|an|the)?\s*(?P<item>[A-Za-z0-9_-]+)",
)
_STOP_ENTITY = {
    "the", "a", "an", "this", "that", "it", "project", "system", "thing", "someone",
    "what", "which", "when", "where", "who", "why", "how", "did", "does", "do", "is", "are",
    "use", "used", "later", "after", "before", "current", "currently", "latest", "database",
    "routine", "memory", "note", "notes", "record", "records", "event", "events", "item", "items",
    "data", "text", "user", "users", "session", "topic", "information",
    "my", "your", "our", "we", "summarize", "summarise", "explain", "list", "tell", "describe", "please", "all",
    "yesterday", "today", "tomorrow", "if", "imagine", "assume",
    "on", "in", "at", "during", "from", "between", "last", "next", "this",
}
_EVENT_WORDS = {
    "review": "review", "migrat": "migration", "mov": "change", "switch": "change",
    "chang": "change", "updat": "update", "start": "start", "finish": "finish",
    "complet": "finish", "cancel": "cancel", "launch": "launch", "submit": "submit",
    "approv": "approval", "visit": "visit", "meet": "meeting", "迁移": "migration",
    "切换": "change", "提交": "submit", "完成": "finish", "访问": "visit",
}


def normalize(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def terms(text: str) -> list[str]:
    """Tokenize Latin words and CJK characters, preserving exact phrase matching."""
    return _TERM_RE.findall(text.casefold())


def query_terms(text: str) -> list[str]:
    source_tokens = terms(text)
    raw = [token for token in source_tokens if token not in _QUERY_STOPWORDS]
    expanded = list(raw)
    aliases = {
        "when": ["date", "time", "year"], "where": ["location", "place"],
        "why": ["reason", "because"], "who": ["person", "author", "team"],
        "how": ["method", "process", "way"], "before": ["earlier", "previous"],
        "after": ["later", "subsequent"], "later": ["after", "subsequent"],
        "current": ["now", "latest", "present"], "currently": ["now", "latest", "present"],
        "latest": ["current", "most", "recent"], "现在": ["当前", "目前"],
        "之前": ["先前", "以前"], "之后": ["后来", "随后"],
        "buy": ["bought", "purchase", "purchased"], "bought": ["buy", "purchase"],
        "go": ["went", "visited"], "went": ["go", "visited"],
        "give": ["gave", "given"], "gave": ["give", "given"],
        "receive": ["received", "got", "gave", "given"], "received": ["receive", "got", "gave", "given"],
        "recipient": ["received", "gave", "given"],
        "prefer": ["preferred", "preference", "like", "likes"],
        "preferred": ["prefer", "preference", "like"],
        "like": ["likes", "prefer", "preferred"], "likes": ["like", "prefer"],
        "visit": ["visited", "visits", "went"], "visited": ["visit", "visits"],
        "work": ["worked", "works"], "worked": ["work", "works"],
        "live": ["lived", "lives"], "lived": ["live", "lives"],
        "make": ["made"], "made": ["make"],
        "build": ["built"], "built": ["build"],
        "start": ["started"], "started": ["start"],
        "use": ["uses", "used"], "uses": ["use", "used"], "used": ["use", "uses"],
        "manage": ["manages", "managed"], "manages": ["manage", "managed"], "managed": ["manage", "manages"],
        "phone": ["telephone", "number"], "telephone": ["phone", "number"],
        "reside": ["live", "lives", "location"], "residence": ["live", "lives", "location"],
        "employer": ["work", "works", "company"], "company": ["employer", "work", "works"],
        "caused": ["because", "cause", "reason", "led", "result"],
        "reason": ["because", "cause", "caused"], "prevented": ["blocked", "failed", "because"],
        "delay": ["delayed", "blocked", "late"], "delayed": ["delay", "blocked", "late"],
        "cause": ["caused", "because", "reason", "led"], "failure": ["failed", "broke", "broken"],
    }
    # Expand interrogatives before stopword filtering. Their aliases are search
    # hints; they never become fabricated evidence in a Search response.
    for token in source_tokens:
        expanded.extend(aliases.get(token, ()))
    return list(dict.fromkeys(expanded))


def index_text(content: str, model_terms: Iterable[str] = ()) -> str:
    values = terms(content)
    for item in model_terms:
        values.extend(terms(item))
    return " ".join(values)


def structure_index_terms(structure: dict[str, Any]) -> list[str]:
    """Short fact keys enrich the index while raw messages remain the values."""
    values = fact_index_terms(structure.get("facts", []))
    if any(relation.get("predicate") == "gave_to" for relation in structure.get("relations", [])):
        values.extend(("recipient", "received", "giver"))
    return values


def explicit_forget_target(content: str) -> str | None:
    """Recognize only a direct user instruction to forget a quoted fact."""
    match = re.fullmatch(
        r"\s*(?:please\s+)?(?:forget|delete|remove)\s+(?:that\s+)?(.+?)\s*[.!]?\s*",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    target = index_text(match.group(1))
    return target if len(target.split()) >= 3 else None


def _clean_entity(value: str) -> str:
    value = normalize(value).strip(" ,.;:!?()[]{}\"'")
    value = re.sub(r"\b(the|a|an)\s+", "", value).strip()
    return value[:80]


def _is_indexable_entity(value: str | None) -> bool:
    if not value:
        return False
    value = _clean_entity(value)
    if not value or value in _STOP_ENTITY or value in _TEMPORAL_ENTITY_NAMES or len(value) < 2:
        return False
    if re.fullmatch(r"(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?", value):
        return False
    return True


def _add_entity(values: set[str], value: str) -> None:
    value = _clean_entity(value)
    if not _is_indexable_entity(value):
        return
    values.add(value)


def _looks_like_question(content: str) -> bool:
    normalized = normalize(content)
    if "?" in content or "？" in content:
        return True
    return bool(re.match(
        r"^(?:what|which|when|where|who|why|how|did|does|do|is|are|can|could|would|请问|什么|哪个|何时|哪里|谁|为什么|如何)",
        normalized,
    ))


def _detect_event_type(content: str, default: str = "fact") -> str:
    lowered = content.casefold()
    for marker, value in _EVENT_WORDS.items():
        if marker in lowered:
            return value
    return default


def _event_sequence(content: str, default_type: str, fallback_event: dict[str, Any], timestamp_ms: int | None = None) -> list[dict[str, Any]]:
    clauses = [part.strip() for part in re.split(r"[.!?。！？；;]+", content) if part.strip()]
    if not clauses:
        return [{**fallback_event, "event_index": 0}]
    events: list[dict[str, Any]] = []
    for index, clause in enumerate(clauses):
        event_time, event_time_key, temporal_expression = _event_time(clause, timestamp_ms)
        event_type = _detect_event_type(clause, default_type if len(clauses) == 1 else "fact")
        order_hint = (
            -1 if re.search(r"\b(before|earlier|first|previously|先前|之前|最初)\b", clause.casefold())
            else 1 if re.search(r"\b(after|later|then|subsequently|后来|之后)\b", clause.casefold()) else 0
        )
        events.append({
            "event_index": index,
            "type": event_type,
            "event_time": event_time,
            "event_time_key": event_time_key,
            "temporal_expression": temporal_expression,
            "time_windows": event_time_windows(clause, timestamp_ms),
            "order_hint": order_hint,
            "text": clause,
        })
    return events


def _structure_events(structure: dict[str, Any]) -> list[dict[str, Any]]:
    events = structure.get("events")
    if isinstance(events, list) and events:
        return [event for event in events if isinstance(event, dict)]
    event = structure.get("event")
    return [event] if isinstance(event, dict) else []


def _structure_time_keys(structure: dict[str, Any]) -> list[tuple[int, int, int]]:
    keys: list[tuple[int, int, int]] = []
    for event in _structure_events(structure):
        key = _time_sort_key(event.get("event_time"))
        if key is not None:
            keys.append(key)
    return keys


def _event_time(content: str, timestamp_ms: int | None = None) -> tuple[str | None, int | None, str | None]:
    return parse_event_time(content, timestamp_ms)


def _time_sort_key(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    parts = value.split("-")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 1:
        return numbers[0], 0, 0
    if len(numbers) == 2:
        return numbers[0], numbers[1], 0
    if len(numbers) == 3:
        return numbers[0], numbers[1], numbers[2]
    return None


def _time_bounds(key: tuple[int, int, int]) -> tuple[str, str]:
    """A coarse year/month is a period, never an invented event day."""
    year, month, day = key
    start = datetime(year, month or 1, day or 1)
    if day:
        end = start + timedelta(days=1)
    elif month:
        end = start.replace(year=year + (month == 12), month=month % 12 + 1)
    else:
        end = start.replace(year=year + 1)
    return start.date().isoformat(), end.date().isoformat()


def _chunks(values: Iterable[str], size: int) -> Iterable[list[str]]:
    chunk: list[str] = []
    for value in sorted(set(values)):
        chunk.append(value)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def extract_structure(content: str, timestamp_ms: int | None = None, role: str = "user") -> dict[str, Any]:
    """Extract conservative, auditable structure without generating benchmark answers."""
    entities: set[str] = set()
    relations: list[dict[str, str]] = []
    event_type = "fact"

    parse_relations = not _looks_like_question(content)
    for match in (_EN_CHANGE_RE.finditer(content) if parse_relations else ()):
        subject, old, new = (_clean_entity(match.group(name)) for name in ("subject", "old", "new"))
        if subject and old and new:
            relations.append({"subject": subject, "predicate": "changed_to", "object": new, "previous": old})
            for value in (subject, old, new):
                _add_entity(entities, value)
            event_type = "change"

    for match in (_EN_REPLACE_RE.finditer(content) if parse_relations else ()):
        subject, old, new = (_clean_entity(match.group(name)) for name in ("subject", "old", "new"))
        if subject and old and new:
            relations.append({"subject": subject, "predicate": "replaced", "object": new, "previous": old})
            for value in (subject, old, new):
                _add_entity(entities, value)
            event_type = "change"

    for match in (_EN_RELATION_RE.finditer(content) if parse_relations else ()):
        subject = _clean_entity(match.group("subject"))
        predicate = normalize(match.group("predicate"))
        object_value = _clean_entity(match.group("object"))
        if subject and object_value:
            relations.append({"subject": subject, "predicate": predicate, "object": object_value})
            _add_entity(entities, subject)
            _add_entity(entities, object_value)

    for match in (_CN_CHANGE_RE.finditer(content) if parse_relations else ()):
        subject, old, new = (_clean_entity(match.group(name)) for name in ("subject", "old", "new"))
        if subject and old and new:
            relations.append({"subject": subject, "predicate": "changed_to", "object": new, "previous": old})
            for value in (subject, old, new):
                _add_entity(entities, value)
            event_type = "change"

    for match in (_CN_RELATION_RE.finditer(content) if parse_relations else ()):
        subject = _clean_entity(match.group("subject"))
        predicate = normalize(match.group("predicate"))
        object_value = _clean_entity(match.group("object"))
        if subject and object_value:
            relations.append({"subject": subject, "predicate": predicate, "object": object_value})
            _add_entity(entities, subject)
            _add_entity(entities, object_value)

    for match in (_EN_GIVE_RE.finditer(content) if parse_relations else ()):
        giver = _clean_entity(match.group("giver"))
        recipient = _clean_entity(match.group("recipient"))
        item = _clean_entity(match.group("item"))
        relations.append({"subject": giver, "predicate": "gave_to", "object": recipient, "item": item})
        for value in (giver, recipient, item):
            _add_entity(entities, value)

    for value in re.findall(r"\"([^\"]+)\"|'([^']+)'", content):
        _add_entity(entities, value[0] or value[1])
    for value in re.findall(r"\b[A-Z][A-Za-z0-9.+#_-]{1,}(?:\s+[A-Z][A-Za-z0-9.+#_-]{1,})*", content):
        _add_entity(entities, value)

    lowered = content.casefold()
    event_type = _detect_event_type(content, event_type)
    unique_relations: list[dict[str, str]] = []
    seen_relations: set[tuple[str, str, str, str]] = set()
    for relation in relations:
        key = (relation.get("subject", ""), relation.get("predicate", ""), relation.get("object", ""), relation.get("previous", ""))
        if key not in seen_relations:
            seen_relations.add(key)
            unique_relations.append(relation)
    event_time, event_time_key, temporal_expression = _event_time(content, timestamp_ms)
    order_hint = -1 if re.search(r"\b(before|earlier|first|previously|formerly|original|old)\b|过去|先前|之前|最初|原来", lowered) else 1 if re.search(r"\b(after|later|then|subsequently|now|currently|current|latest)\b|最近|现在|当前|后来|之后", lowered) else 0
    event = {
        "type": event_type, "event_time": event_time, "event_time_key": event_time_key,
        "time_windows": event_time_windows(content, timestamp_ms),
        "temporal_expression": temporal_expression, "order_hint": order_hint,
    }
    events = _event_sequence(content, event_type, event, timestamp_ms)
    facts = extract_facts(content, role)
    for fact in facts:
        if not fact["subject"].startswith("__"):
            _add_entity(entities, fact["subject"])
        _add_entity(entities, fact["value"])
    return {"memory_type": event_type, "event": event, "events": events, "entities": sorted(entities), "relations": unique_relations, "facts": facts}


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
    structure: dict[str, Any]
    sequence_no: int
    role: str = "user"
    timestamp_ms: int | None = None
    message_index: int = 0
    status: str = "active"
    superseded_by: str | None = None


class MemoryStore:
    _GRAPH_CANDIDATE_LIMIT = 128
    DEFAULT_FEATURES = frozenset({"entity", "graph", "temporal", "adjacency", "conflict", "options", "facts", "coverage"})

    def __init__(self, db_path: str = "data/memories.sqlite3", features: Iterable[str] | None = None) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.features = set(self.DEFAULT_FEATURES if features is None else features)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._fts_enabled = True
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            # FULL makes a successful transaction durable before Add returns 200.
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA secure_delete=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close the current thread's SQLite connection (useful for local runs)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

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
                structure_json TEXT NOT NULL DEFAULT '{}',
                event_type TEXT,
                event_time TEXT,
                event_time_key INTEGER,
                sequence_no INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_entities (
                memory_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                entity TEXT NOT NULL,
                PRIMARY KEY(memory_id, entity)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_entities_lookup ON memory_entities(user_id, entity, memory_id);
            CREATE TABLE IF NOT EXISTS memory_relations (
                memory_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                previous TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(memory_id, subject, predicate, object)
            );
            CREATE INDEX IF NOT EXISTS idx_rel_subject ON memory_relations(user_id, subject, memory_id);
            CREATE INDEX IF NOT EXISTS idx_rel_object ON memory_relations(user_id, object, memory_id);
            CREATE INDEX IF NOT EXISTS idx_rel_previous ON memory_relations(user_id, previous, memory_id);
            CREATE TABLE IF NOT EXISTS memory_facts (
                memory_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                value TEXT NOT NULL,
                text TEXT NOT NULL,
                polarity INTEGER NOT NULL,
                exclusive INTEGER NOT NULL,
                explicit_update INTEGER NOT NULL,
                source_order INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT,
                PRIMARY KEY(memory_id,subject,predicate,value,polarity)
            );
            CREATE INDEX IF NOT EXISTS idx_fact_key ON memory_facts(user_id,subject,predicate,status);
            CREATE TABLE IF NOT EXISTS add_requests (
                user_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY(user_id, request_id)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        for name, definition in (
            ("structure_json", "TEXT NOT NULL DEFAULT '{}'"), ("event_type", "TEXT"),
            ("event_time", "TEXT"), ("event_time_key", "INTEGER"),
            ("sequence_no", "INTEGER NOT NULL DEFAULT 0"),
            ("role", "TEXT NOT NULL DEFAULT 'user'"),
            ("timestamp_ms", "INTEGER"),
            ("message_index", "INTEGER NOT NULL DEFAULT 0"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("superseded_by", "TEXT"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_event_time ON memories(user_id, event_time_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session_sequence ON memories(user_id, session_id, sequence_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_source_time ON memories(user_id, timestamp_ms)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(user_id, status)")
        if "source_order" not in {row["name"] for row in conn.execute("PRAGMA table_info(memory_facts)")}:
            conn.execute("ALTER TABLE memory_facts ADD COLUMN source_order INTEGER NOT NULL DEFAULT 0")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                "memory_id UNINDEXED, user_id UNINDEXED, search_text)"
            )
        except sqlite3.OperationalError:
            # ponytail: fallback remains available for SQLite builds without FTS5; official image has FTS5.
            self._fts_enabled = False
        if self._fts_enabled:
            version = "token-index-v4-fact-keys"
            row = conn.execute("SELECT value FROM memory_meta WHERE key='fts_version'").fetchone()
            if not row or row["value"] != version:
                conn.execute("DELETE FROM memories_fts")
                rows = conn.execute("SELECT memory_id,user_id,content,model_term_json,role FROM memories").fetchall()
                conn.executemany(
                    "INSERT INTO memories_fts(memory_id,user_id,search_text) VALUES(?,?,?)",
                    [(item["memory_id"], item["user_id"], index_text(item["content"], [*json.loads(item["model_term_json"]), *fact_index_terms(extract_facts(item["content"], item["role"]))])) for item in rows],
                )
                conn.execute(
                    "INSERT INTO memory_meta(key,value) VALUES('fts_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (version,),
                )
        self._backfill_structure(conn)
        conn.commit()

    def _backfill_structure(self, conn: sqlite3.Connection) -> None:
        version = "structure-v10-fact-source-calendar"
        version_row = conn.execute("SELECT value FROM memory_meta WHERE key='structure_version'").fetchone()
        if version_row and version_row["value"] == version:
            return
        conn.execute("DELETE FROM memory_facts")
        rows = conn.execute("SELECT memory_id,user_id,content,timestamp_ms,role FROM memories ORDER BY created_at,memory_id").fetchall()
        for row in rows:
            structure = extract_structure(row["content"], row["timestamp_ms"], row["role"])
            if "conflict" in self.features:
                self._inherit_explicit_change_key(conn, row["user_id"], row["content"], structure)
            event = structure["event"]
            conn.execute(
                "UPDATE memories SET structure_json=?,event_type=?,event_time=?,event_time_key=? WHERE memory_id=?",
                (json.dumps(structure, ensure_ascii=False), structure.get("memory_type", "fact"), event.get("event_time"), event.get("event_time_key"), row["memory_id"]),
            )
            self._replace_structure_indexes(conn, row["memory_id"], row["user_id"], structure)
            if self._fts_enabled:
                old_terms = conn.execute("SELECT model_term_json FROM memories WHERE memory_id=?", (row["memory_id"],)).fetchone()[0]
                conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (row["memory_id"],))
                conn.execute("INSERT INTO memories_fts(memory_id,user_id,search_text) VALUES(?,?,?)", (row["memory_id"], row["user_id"], index_text(row["content"], [*json.loads(old_terms), *structure_index_terms(structure)])))
        conn.execute(
            "INSERT INTO memory_meta(key,value) VALUES('structure_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (version,),
        )

    def _replace_structure_indexes(self, conn: sqlite3.Connection, memory_id: str, user_id: str, structure: dict[str, Any]) -> None:
        conn.execute("DELETE FROM memory_entities WHERE memory_id=?", (memory_id,))
        conn.execute("DELETE FROM memory_relations WHERE memory_id=?", (memory_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO memory_entities(memory_id,user_id,entity) VALUES(?,?,?)",
            [(memory_id, user_id, entity) for entity in structure.get("entities", [])],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO memory_relations(memory_id,user_id,subject,predicate,object,previous) VALUES(?,?,?,?,?,?)",
            [
                (memory_id, user_id, item.get("subject", ""), item.get("predicate", ""), item.get("object", ""), item.get("previous", ""))
                for item in structure.get("relations", []) if item.get("subject") and item.get("object")
            ],
        )
        conn.execute("DELETE FROM memory_facts WHERE memory_id=?", (memory_id,))
        for fact in structure.get("facts", []):
            conn.execute(
                "INSERT INTO memory_facts(memory_id,user_id,subject,predicate,value,text,polarity,exclusive,explicit_update,source_order) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(memory_id,subject,predicate,value,polarity) DO UPDATE SET text=excluded.text,source_order=excluded.source_order,exclusive=excluded.exclusive,explicit_update=excluded.explicit_update",
                (memory_id, user_id, fact["subject"], fact["predicate"], fact["value"], fact["text"],
                 int(fact["polarity"]), int(fact["exclusive"]), int(fact["explicit_update"]), int(fact.get("_state_order", fact.get("source_start", 0)))),
            )
            if "conflict" in self.features:
                self._reconcile_fact_key(conn, user_id, fact["subject"], fact["predicate"])

    @staticmethod
    def _inherit_explicit_change_key(conn: sqlite3.Connection, user_id: str, content: str, structure: dict[str, Any]) -> None:
        """Resolve a from-X-to-Y update only when a prior property is unique."""
        if not supports_state_update(content):
            return
        for relation in structure.get("relations", []):
            if relation.get("predicate") != "changed_to" or not relation.get("previous"):
                continue
            old = conn.execute(
                "SELECT DISTINCT subject,predicate,exclusive FROM memory_facts WHERE user_id=? AND subject=? AND value=? AND polarity=1",
                (user_id, relation["subject"], relation["previous"]),
            ).fetchall()
            if len(old) != 1:
                continue
            fact = {"subject": old[0]["subject"], "predicate": old[0]["predicate"], "value": relation["object"],
                    "text": content, "polarity": True, "exclusive": bool(old[0]["exclusive"]), "explicit_update": True,
                    "source_start": 0, "source_end": len(content), "_state_order": max(0, content.casefold().find(relation["object"]))}
            if not old[0]["exclusive"]:
                retired = {**fact, "value": relation["previous"], "polarity": False,
                           "_state_order": max(0, content.casefold().find(relation["previous"]))}
                if not any((f["subject"], f["predicate"], f["value"], f["polarity"]) == (retired["subject"], retired["predicate"], retired["value"], False) for f in structure["facts"]):
                    structure["facts"].append(retired)
            if not any((f["subject"], f["predicate"], f["value"]) == (fact["subject"], fact["predicate"], fact["value"]) for f in structure["facts"]):
                structure["facts"].append(fact)

    @staticmethod
    def _reconcile_fact_key(conn: sqlite3.Connection, user_id: str, subject: str, predicate: str) -> None:
        """Maintain atomic state history, including out-of-order source writes.

        Scalar attributes compete by key. Multi-valued preferences only compete
        when the same value is explicitly affirmed/negated; tea does not erase
        coffee. Whole source messages remain available for historical retrieval.
        """
        rows = conn.execute(
            "SELECT f.*,m.timestamp_ms,m.created_at,m.message_index,m.content,m.role FROM memory_facts f "
            "JOIN memories m ON m.memory_id=f.memory_id "
            "WHERE f.user_id=? AND f.subject=? AND f.predicate=?",
            (user_id, subject, predicate),
        ).fetchall()
        timed = all(row["timestamp_ms"] is not None for row in rows)
        timed_roles = {role: all(row["timestamp_ms"] is not None for row in rows if row["role"] == role) for role in {row["role"] for row in rows}}
        ordered = sorted(rows, key=lambda row: (row["timestamp_ms"] if (timed_roles[row["role"]] if subject == "__user__" else timed) else row["created_at"], row["created_at"], row["message_index"], row["memory_id"], row["source_order"]))
        scalar = any(row["exclusive"] for row in rows)
        active: list[sqlite3.Row] = []
        states: dict[tuple[str, str, int], tuple[str, str | None]] = {}
        for new in ordered:
            retained = []
            for old in active:
                # A generated statement about the user is a different source
                # of evidence from the user's self-report. Keep both source
                # histories instead of letting the assistant revoke it.
                if subject == "__user__" and old["role"] != new["role"]:
                    retained.append(old)
                    continue
                replaces = old["value"] == new["value"] or (scalar and new["polarity"] and old["polarity"])
                if replaces:
                    states[(old["memory_id"], old["value"], old["polarity"])] = ("superseded", new["memory_id"])
                else:
                    retained.append(old)
            active = [*retained, new]
            states[(new["memory_id"], new["value"], new["polarity"])] = ("active", None)
        for row in rows:
            status, replaced_by = states[(row["memory_id"], row["value"], row["polarity"])]
            conn.execute(
                "UPDATE memory_facts SET status=?,superseded_by=? WHERE memory_id=? AND subject=? AND predicate=? AND value=? AND polarity=?",
                (status, replaced_by, row["memory_id"], subject, predicate, row["value"], row["polarity"]),
            )

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        try:
            structure = json.loads(row["structure_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            structure = extract_structure(row["content"])
        return Memory(
            memory_id=row["memory_id"], user_id=row["user_id"], session_id=row["session_id"],
            request_id=row["request_id"], content=row["content"], created_at=float(row["created_at"]),
            token_list=tuple(json.loads(row["token_json"])), model_terms=tuple(json.loads(row["model_term_json"])),
            structure=structure, sequence_no=int(row["sequence_no"] or 0),
            role=row["role"], timestamp_ms=row["timestamp_ms"], message_index=int(row["message_index"] or 0),
            status=row["status"], superseded_by=row["superseded_by"],
        )

    def add(self, *, request_id: str, user_id: str, session_id: str, content: str, model_terms: Iterable[str] = ()) -> str:
        if not all(isinstance(value, str) and value.strip() for value in (request_id, user_id, session_id, content)):
            raise ValueError("request_id, user_id, session_id, and content are required")
        if len(content) > 120_000:
            raise ValueError("content is too long")
        memory_id = stable_id(user_id, session_id, content)
        token_list = terms(content)
        model_list = [normalize(term) for term in model_terms if isinstance(term, str) and term.strip()]
        structure = extract_structure(content)
        event = structure["event"]
        with self._lock:
            conn = self._connection()
            existing = conn.execute("SELECT memory_id FROM memories WHERE user_id=? AND request_id=?", (user_id, request_id)).fetchone()
            if existing:
                return str(existing["memory_id"])
            duplicate = conn.execute("SELECT memory_id FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
            if duplicate:
                return str(duplicate["memory_id"])
            seq_row = conn.execute("SELECT COALESCE(MAX(sequence_no),0)+1 AS next_seq FROM memories WHERE user_id=? AND session_id=?", (user_id, session_id)).fetchone()
            sequence_no = int(seq_row["next_seq"])
            conn.execute(
                "INSERT INTO memories(memory_id,user_id,session_id,request_id,content,created_at,token_json,model_term_json,structure_json,event_type,event_time,event_time_key,sequence_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (memory_id, user_id, session_id, request_id, content.strip(), time.time(), json.dumps(token_list, ensure_ascii=False), json.dumps(model_list, ensure_ascii=False), json.dumps(structure, ensure_ascii=False), structure.get("memory_type", "fact"), event.get("event_time"), event.get("event_time_key"), sequence_no),
            )
            self._replace_structure_indexes(conn, memory_id, user_id, structure)
            if self._fts_enabled:
                conn.execute("INSERT INTO memories_fts(memory_id,user_id,search_text) VALUES(?,?,?)", (memory_id, user_id, index_text(content.strip(), [*model_list, *structure_index_terms(structure)])))
            conn.commit()
        return memory_id

    def add_messages(self, *, request_id: str, user_id: str, session_id: str, messages: list[dict[str, Any]]) -> list[str]:
        """Persist one ordered Add chunk atomically. A retried request is a no-op."""
        if not all(isinstance(value, str) and value.strip() for value in (request_id, user_id, session_id)):
            raise ValueError("request_id, user_id, and session_id are required")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty array")
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                raise ValueError("each message needs role=user or assistant")
            if not isinstance(message.get("content"), str) or not message["content"].strip():
                raise ValueError("each message needs non-empty string content")
            timestamp = message.get("timestamp")
            if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, int)):
                raise ValueError("timestamp must be Unix milliseconds")
            if timestamp is not None:
                try:
                    datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                except (ValueError, OverflowError, OSError) as exc:
                    raise ValueError("timestamp is outside the supported datetime range") from exc
        payload_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT session_id,payload_hash FROM add_requests WHERE user_id=? AND request_id=?",
                    (user_id, request_id),
                ).fetchone()
                if existing:
                    if existing["session_id"] != session_id or existing["payload_hash"] != payload_hash:
                        raise ValueError("request_id was reused with a different payload")
                    ids = [self._message_id(user_id, request_id, index) for index in range(len(messages))]
                    conn.commit()
                    return ids
                seq_row = conn.execute(
                    "SELECT COALESCE(MAX(sequence_no),0) AS seq FROM memories WHERE user_id=? AND session_id=?",
                    (user_id, session_id),
                ).fetchone()
                sequence = int(seq_row["seq"])
                created_at = time.time()
                ids: list[str] = []
                for index, message in enumerate(messages):
                    memory_id = self._message_id(user_id, request_id, index)
                    content = message["content"]
                    forget_target = explicit_forget_target(content) if message["role"] == "user" else None
                    if forget_target is not None:
                        # The request receipt remains idempotent, but neither the
                        # forgotten fact nor the instruction becomes Search evidence.
                        self._forget_exact_fact(conn, user_id, forget_target)
                        ids.append(memory_id)
                        continue
                    timestamp_ms = message.get("timestamp")
                    structure = extract_structure(content, timestamp_ms, message["role"])
                    if "conflict" in self.features:
                        self._inherit_explicit_change_key(conn, user_id, content, structure)
                    event = structure["event"]
                    conn.execute(
                        "INSERT INTO memories(memory_id,user_id,session_id,request_id,content,created_at,token_json,model_term_json,structure_json,event_type,event_time,event_time_key,sequence_no,role,timestamp_ms,message_index) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (memory_id, user_id, session_id, f"{request_id}:msg:{index}", content, created_at + index * 0.000001,
                         json.dumps(terms(content), ensure_ascii=False), "[]", json.dumps(structure, ensure_ascii=False),
                         structure.get("memory_type", "fact"), event.get("event_time"), event.get("event_time_key"),
                         sequence + index + 1, message["role"], timestamp_ms, index),
                    )
                    self._replace_structure_indexes(conn, memory_id, user_id, structure)
                    if self._fts_enabled:
                        conn.execute(
                            "INSERT INTO memories_fts(memory_id,user_id,search_text) VALUES(?,?,?)",
                            (memory_id, user_id, index_text(content, structure_index_terms(structure))),
                        )
                    if "conflict" in self.features:
                        self._mark_explicit_supersessions(conn, user_id, memory_id, structure)
                    ids.append(memory_id)
                conn.execute(
                    "INSERT INTO add_requests(user_id,request_id,session_id,payload_hash) VALUES(?,?,?,?)",
                    (user_id, request_id, session_id, payload_hash),
                )
                conn.commit()
                return ids
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _message_id(user_id: str, request_id: str, index: int) -> str:
        value = f"{user_id}\0{request_id}\0{index}".encode("utf-8")
        return "mem_" + hashlib.sha256(value).hexdigest()[:24]

    def _forget_exact_fact(self, conn: sqlite3.Connection, user_id: str, target: str) -> None:
        """Delete matching complete source facts and all searchable indexes."""
        rows = conn.execute("SELECT memory_id,content FROM memories WHERE user_id=?", (user_id,)).fetchall()
        for row in rows:
            if index_text(row["content"]) != target:
                continue
            memory_id = row["memory_id"]
            if self._fts_enabled:
                conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_entities WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_relations WHERE memory_id=?", (memory_id,))
            keys = conn.execute("SELECT DISTINCT subject,predicate FROM memory_facts WHERE memory_id=?", (memory_id,)).fetchall()
            conn.execute("DELETE FROM memory_facts WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM memories WHERE memory_id=? AND user_id=?", (memory_id, user_id))
            for key in keys:
                self._reconcile_fact_key(conn, user_id, key["subject"], key["predicate"])

    @staticmethod
    def _mark_explicit_supersessions(conn: sqlite3.Connection, user_id: str, new_id: str, structure: dict[str, Any]) -> None:
        """Only explicit from-X-to-Y changes supersede matching older facts."""
        source = conn.execute("SELECT content FROM memories WHERE memory_id=?", (new_id,)).fetchone()
        if source is None or not supports_state_update(source["content"]):
            return
        for relation in structure.get("relations", []):
            if relation.get("predicate") != "changed_to" or not relation.get("previous"):
                continue
            rows = conn.execute(
                "SELECT DISTINCT r.memory_id FROM memory_relations r "
                "JOIN memories old ON old.memory_id=r.memory_id "
                "JOIN memories new ON new.memory_id=? "
                "WHERE r.user_id=? AND r.subject=? AND r.object=? AND r.memory_id<>? "
                "AND old.created_at<new.created_at "
                "AND (old.timestamp_ms IS NULL OR new.timestamp_ms IS NULL OR old.timestamp_ms<=new.timestamp_ms)",
                (new_id, user_id, relation["subject"], relation["previous"], new_id),
            ).fetchall()
            for row in rows:
                # Retiring one property cannot invalidate the other facts in
                # its original multi-attribute message.
                if conn.execute("SELECT 1 FROM memory_facts WHERE memory_id=? AND status='active' LIMIT 1", (row["memory_id"],)).fetchone():
                    continue
                conn.execute(
                    "UPDATE memories SET status='superseded',superseded_by=? WHERE memory_id=? AND user_id=? AND status='active'",
                    (new_id, row["memory_id"], user_id),
                )

    def update_model_terms(self, *, memory_id: str, model_terms: Iterable[str]) -> None:
        model_list = [normalize(term) for term in model_terms if isinstance(term, str) and term.strip()]
        with self._lock:
            conn = self._connection()
            row = conn.execute("SELECT user_id,content,structure_json FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
            if not row:
                return
            conn.execute("UPDATE memories SET model_term_json=? WHERE memory_id=?", (json.dumps(model_list, ensure_ascii=False), memory_id))
            if self._fts_enabled:
                conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
                conn.execute("INSERT INTO memories_fts(memory_id,user_id,search_text) VALUES(?,?,?)", (memory_id, row["user_id"], index_text(row["content"], [*model_list, *structure_index_terms(json.loads(row["structure_json"]))])))
            conn.commit()

    def _load_user(self, user_id: str) -> list[Memory]:
        rows = self._connection().execute("SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def _load_ids(self, user_id: str, memory_ids: Iterable[str]) -> list[Memory]:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self._connection().execute(f"SELECT * FROM memories WHERE user_id=? AND memory_id IN ({placeholders})", [user_id, *ids]).fetchall()
        by_id = {row["memory_id"]: self._row_to_memory(row) for row in rows}
        return [by_id[memory_id] for memory_id in ids if memory_id in by_id]

    @staticmethod
    def _fts_query(text: str) -> str:
        values: list[str] = []
        seen: set[str] = set()
        for token in query_terms(text):
            if token and token not in seen:
                seen.add(token)
                values.append('"' + token.replace('"', '""') + '"')
        return " OR ".join(values)

    @staticmethod
    def _memory_tokens(doc: Memory) -> set[str]:
        values = set(doc.token_list)
        for phrase in doc.model_terms:
            values.update(terms(phrase))
        values.update(terms(" ".join(structure_index_terms(doc.structure))))
        return values

    def _entity_candidates(self, user_id: str, entities: set[str], limit: int) -> set[str]:
        if not entities:
            return set()
        placeholders = ",".join("?" for _ in entities)
        rows = self._connection().execute(
            f"SELECT DISTINCT e.memory_id FROM memory_entities e JOIN memories m ON m.memory_id=e.memory_id "
            f"WHERE e.user_id=? AND e.entity IN ({placeholders}) ORDER BY m.created_at DESC LIMIT ?",
            [user_id, *sorted(entities), limit],
        ).fetchall()
        return {str(row["memory_id"]) for row in rows}

    @staticmethod
    def _fact_query_scope(query: str, query_entities: set[str]) -> tuple[set[str], set[str]]:
        predicates: set[str] = set()
        mappings = {
            "phone": r"\b(phone|telephone|mobile|contact number)\b|电话|手机号",
            "email": r"\b(e-?mail)\b|邮箱|邮件地址",
            "name": r"\b(name|called)\b|名字|姓名",
            "address": r"\b(address)\b|住址|地址",
            "location": r"\b(live|lives|living|reside|residence|moved)\b|住在|居住|搬到",
            "employer": r"\b(employer|company|work|works|working)\b|公司|雇主|工作",
            "occupation": r"\b(job|occupation|profession|professional role)\b|职业|职位",
            "preference": r"\b(prefer|prefers|preference|like|likes|drink|drinks)\b|喜欢|偏好|喝",
            "uses": r"\b(database|use|uses|used)\b|数据库|使用",
        }
        for predicate, pattern in mappings.items():
            if re.search(pattern, query, re.IGNORECASE):
                predicates.add(predicate)
        subjects = set(query_entities)
        if re.search(r"\b(i|my|me|mine)\b|我的|我现在|我住|我喜欢|我偏好", query, re.IGNORECASE):
            subjects.add("__user__")
        if re.search(r"\b(assistant|your|you)\b|助手", query, re.IGNORECASE):
            subjects.add("__assistant__")
        return predicates, subjects

    @staticmethod
    def _fact_matches(fact: dict[str, Any], predicates: set[str], subjects: set[str]) -> bool:
        return any(fact["predicate"] == key or fact["predicate"].startswith(key + ":") for key in predicates) and (not subjects or fact["subject"] in subjects)

    def _fact_candidates(self, user_id: str, predicates: set[str], subjects: set[str], limit: int) -> set[str]:
        if not predicates or "facts" not in self.features:
            return set()
        clauses = " OR ".join("(f.predicate=? OR f.predicate LIKE ?)" for _ in predicates)
        parameters: list[Any] = [user_id]
        for key in sorted(predicates):
            parameters.extend((key, key + ":%"))
        subject_clause = ""
        if subjects:
            subject_clause = " AND f.subject IN (" + ",".join("?" for _ in subjects) + ")"
            parameters.extend(sorted(subjects))
        parameters.append(limit)
        rows = self._connection().execute(
            "SELECT DISTINCT f.memory_id FROM memory_facts f JOIN memories m ON m.memory_id=f.memory_id "
            f"WHERE f.user_id=? AND ({clauses}){subject_clause} ORDER BY m.created_at DESC LIMIT ?", parameters,
        ).fetchall()
        return {row["memory_id"] for row in rows}

    def _expand_graph(self, user_id: str, seeds: set[str], max_hops: int = 2) -> dict[str, int]:
        depths = {memory_id: 0 for memory_id in seeds}
        frontier = set(seeds)
        for depth in range(1, max_hops + 1):
            if not frontier:
                break
            conn = self._connection()
            endpoints: set[str] = set()
            for memory_chunk in _chunks(frontier, 400):
                placeholders = ",".join("?" for _ in memory_chunk)
                relation_rows = conn.execute(
                    f"SELECT subject,object,previous FROM memory_relations WHERE user_id=? AND memory_id IN ({placeholders})",
                    [user_id, *memory_chunk],
                ).fetchall()
                entity_rows = conn.execute(
                    f"SELECT entity FROM memory_entities WHERE user_id=? AND memory_id IN ({placeholders})",
                    [user_id, *memory_chunk],
                ).fetchall()
                endpoints.update(value for row in relation_rows for value in (row["subject"], row["object"], row["previous"]) if value)
                endpoints.update(row["entity"] for row in entity_rows if row["entity"])
            if not endpoints:
                break
            scope_size = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id=?", (user_id,)).fetchone()[0]
            # Common entities are poor bridges (e.g. every note names Alice).
            # Never fan out across the whole scope through such a hub.
            informative: set[str] = set()
            for endpoint_chunk in _chunks(endpoints, 250):
                placeholders = ",".join("?" for _ in endpoint_chunk)
                degrees = conn.execute(
                    f"SELECT entity,COUNT(*) AS n FROM memory_entities WHERE user_id=? AND entity IN ({placeholders}) GROUP BY entity",
                    [user_id, *endpoint_chunk],
                ).fetchall()
                informative.update(row["entity"] for row in degrees if row["n"] <= max(8, int(scope_size * 0.12)))
            endpoints.intersection_update(informative)
            if not endpoints:
                break
            next_frontier: set[str] = set()
            for endpoint_chunk in _chunks(endpoints, 250):
                endpoint_placeholders = ",".join("?" for _ in endpoint_chunk)
                relation_rows = conn.execute(
                    f"SELECT DISTINCT memory_id FROM memory_relations WHERE user_id=? AND (subject IN ({endpoint_placeholders}) OR object IN ({endpoint_placeholders}) OR previous IN ({endpoint_placeholders})) LIMIT {self._GRAPH_CANDIDATE_LIMIT}",
                    [user_id, *endpoint_chunk, *endpoint_chunk, *endpoint_chunk],
                ).fetchall()
                entity_rows = conn.execute(
                    f"SELECT DISTINCT memory_id FROM memory_entities WHERE user_id=? AND entity IN ({endpoint_placeholders}) LIMIT {self._GRAPH_CANDIDATE_LIMIT}",
                    [user_id, *endpoint_chunk],
                ).fetchall()
                next_frontier.update(str(row["memory_id"]) for row in [*relation_rows, *entity_rows] if str(row["memory_id"]) not in depths)
                if len(next_frontier) >= self._GRAPH_CANDIDATE_LIMIT:
                    next_frontier = set(sorted(next_frontier)[:self._GRAPH_CANDIDATE_LIMIT])
                    break
            for memory_id in next_frontier:
                depths[memory_id] = depth
            frontier = next_frontier
        return depths

    def search(self, *, user_id: str, query: str, top_k: int = 100, session_id: str | None = None,
               options: list[str] | None = None, include_source: bool = False) -> list[dict[str, str]]:
        if not isinstance(user_id, str) or not user_id.strip() or not isinstance(query, str) or not query.strip():
            raise ValueError("user_id and query are required")
        top_k = max(1, min(int(top_k), 100))
        conn = self._connection()
        query_structure = extract_structure(query)
        query_entities = set(query_structure.get("entities", [])) if "entity" in self.features else set()
        fact_predicates, fact_subjects = self._fact_query_scope(query, query_entities)
        requested_role = None
        if re.search(r"\b(?:my own (?:statement|words)|i (?:said|stated|told)|what did i say)\b|我自己说|我说过|我的原话", query, re.IGNORECASE):
            requested_role = "user"
        elif re.search(r"\b(?:assistant (?:said|stated|told)|(?:what|which).*assistant.*(?:say|statement))\b|助手说|助手的原话", query, re.IGNORECASE):
            requested_role = "assistant"
        broad_query = "coverage" in self.features and bool(re.search(
            r"\b(summarize|summarise|summary|overview|everything|all events|entire history|overall|across sessions)\b|总结|概述|所有经历|整个历史|全部事件", query, re.IGNORECASE,
        ))
        seed_ids: set[str] = set()
        fts_rank: dict[str, int] = {}
        candidate_limit = min(2000, max(64, top_k * 8))
        if self._fts_enabled:
            candidate_query = " ".join([query, *((options or []) if "options" in self.features else [])])
            match_query = self._fts_query(candidate_query)
            if match_query:
                rows = conn.execute(
                    "SELECT m.memory_id FROM memories_fts f JOIN memories m ON m.memory_id=f.memory_id WHERE f.user_id=? AND memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                    (user_id, match_query, candidate_limit),
                ).fetchall()
                for rank, row in enumerate(rows):
                    memory_id = str(row["memory_id"])
                    seed_ids.add(memory_id)
                    fts_rank[memory_id] = rank
        else:
            documents = self._load_user(user_id)
            seed_ids.update(doc.memory_id for doc in documents if self._memory_tokens(doc).intersection(query_terms(query)))
        entity_ids = self._entity_candidates(user_id, query_entities, candidate_limit)
        seed_ids.update(entity_ids)
        seed_ids.update(self._fact_candidates(user_id, fact_predicates, fact_subjects, candidate_limit))
        query_event_time = query_structure.get("event", {}).get("event_time")
        query_order_hint = query_structure.get("event", {}).get("order_hint", 0)
        relative_anchor = bool(re.search(r"\b(before|after|earlier|later)\b|之前|之后|先前|后来", query, re.IGNORECASE))
        # Search has no query timestamp. Use the latest supplied source time in
        # this user scope as an explicit proxy for relative calendar queries;
        # never substitute deployment/server time. This remains an assumption.
        source_anchor = conn.execute("SELECT MAX(timestamp_ms) FROM memories WHERE user_id=?", (user_id,)).fetchone()[0]
        time_window = query_time_window(query, source_anchor) if "temporal" in self.features else None
        if time_window:
            window_start, window_end = time_window
            start_ms = int(datetime.fromisoformat(window_start).replace(tzinfo=timezone.utc).timestamp() * 1000)
            end_ms = int(datetime.fromisoformat(window_end).replace(tzinfo=timezone.utc).timestamp() * 1000)
            rows = conn.execute(
                "SELECT memory_id FROM memories WHERE user_id=? AND "
                "((event_time>=? AND event_time<?) OR (timestamp_ms>=? AND timestamp_ms<?) OR "
                "EXISTS(SELECT 1 FROM json_each(memories.structure_json,'$.event.time_windows') w "
                "WHERE json_extract(w.value,'$[0]')<? AND json_extract(w.value,'$[1]')>?)) "
                "ORDER BY created_at DESC LIMIT ?", (user_id, window_start, window_end, start_ms, end_ms, window_end, window_start, candidate_limit),
            ).fetchall()
            seed_ids.update(row["memory_id"] for row in rows)
        if broad_query:
            # Round-robin coverage is only for global summaries. Ordinary fact
            # queries retain pure relevance order and no per-session quota.
            rows = conn.execute(
                "SELECT memory_id FROM (SELECT memory_id,ROW_NUMBER() OVER "
                "(PARTITION BY session_id ORDER BY sequence_no) AS episode_rank FROM memories WHERE user_id=?) "
                "ORDER BY episode_rank,memory_id LIMIT ?", (user_id, candidate_limit),
            ).fetchall()
            seed_ids.update(row["memory_id"] for row in rows)
        if "temporal" in self.features and query_order_hint > 0 and not query_event_time and not relative_anchor:
            recent_rows = conn.execute(
                "SELECT memory_id FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, min(top_k, 100)),
            ).fetchall()
            seed_ids.update(str(row["memory_id"]) for row in recent_rows)
        if query_event_time and "temporal" in self.features:
            date_parts = [int(part) for part in query_event_time.split("-")]
            source_time = datetime(date_parts[0], date_parts[1] if len(date_parts) > 1 else 1,
                                   date_parts[2] if len(date_parts) > 2 else 1, tzinfo=timezone.utc)
            if len(query_event_time) == 4:
                end_time = source_time.replace(year=source_time.year + 1)
            elif len(query_event_time) == 7:
                end_time = source_time.replace(year=source_time.year + (source_time.month == 12), month=source_time.month % 12 + 1)
            else:
                end_time = source_time + timedelta(days=1)
            if query_order_hint < 0:
                rows = conn.execute(
                    "SELECT memory_id FROM memories WHERE user_id=? AND "
                    "(event_time<? OR timestamp_ms<?) ORDER BY COALESCE(timestamp_ms,0) DESC LIMIT ?",
                    (user_id, query_event_time, int(source_time.timestamp() * 1000), candidate_limit),
                ).fetchall()
            elif query_order_hint > 0:
                rows = conn.execute(
                    "SELECT memory_id FROM memories WHERE user_id=? AND "
                    "(event_time>=? OR timestamp_ms>=?) ORDER BY COALESCE(timestamp_ms,0) ASC LIMIT ?",
                    (user_id, end_time.date().isoformat(), int(end_time.timestamp() * 1000), candidate_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT memory_id FROM memories WHERE user_id=? AND (event_time LIKE ? OR "
                    "(timestamp_ms>=? AND timestamp_ms<?)) LIMIT ?",
                    (user_id, query_event_time + "%", int(source_time.timestamp() * 1000), int(end_time.timestamp() * 1000), candidate_limit),
                ).fetchall()
            seed_ids.update(str(row["memory_id"]) for row in rows)
        ordered_seeds = sorted(seed_ids, key=lambda memory_id: (fts_rank.get(memory_id, candidate_limit), memory_id))
        graph_seeds = set(ordered_seeds[:12])
        graph_depths = {memory_id: 0 for memory_id in seed_ids}
        if "graph" in self.features:
            graph_depths.update({key: depth for key, depth in self._expand_graph(user_id, graph_seeds, max_hops=2).items() if key not in seed_ids})
        temporal_anchor_id: str | None = None
        temporal_anchor_value: float | None = None
        temporal_anchor_source = False
        if "temporal" in self.features and relative_anchor and query_order_hint and not query_event_time and seed_ids:
            # The strongest lexical hit is the event named by a before/after
            # question. Pull bounded neighbors across sessions by source time,
            # falling back to ingestion order when source time is unavailable.
            ordered_seeds = sorted(seed_ids, key=lambda memory_id: (fts_rank.get(memory_id, candidate_limit), memory_id))
            anchor = self._load_ids(user_id, ordered_seeds[:1])[0]
            temporal_anchor_id = anchor.memory_id
            temporal_anchor_source = anchor.timestamp_ms is not None
            temporal_anchor_value = float(anchor.timestamp_ms if temporal_anchor_source else anchor.created_at)
            column = "timestamp_ms" if temporal_anchor_source else "created_at"
            operator = "<" if query_order_hint < 0 else ">"
            direction = "DESC" if query_order_hint < 0 else "ASC"
            near_rows = conn.execute(
                f"SELECT memory_id FROM memories WHERE user_id=? AND {column}{operator}? "
                f"ORDER BY {column} {direction} LIMIT ?",
                (user_id, temporal_anchor_value, min(8, candidate_limit)),
            ).fetchall()
            for row in near_rows:
                graph_depths.setdefault(str(row["memory_id"]), 1)
        if seed_ids and "adjacency" in self.features:
            for seed_chunk in _chunks(ordered_seeds[:12], 400):
                placeholders = ",".join("?" for _ in seed_chunk)
                neighbor_rows = conn.execute(
                    "SELECT DISTINCT n.memory_id FROM memories s JOIN memories n "
                    "ON n.user_id=s.user_id AND n.session_id=s.session_id "
                    "AND n.sequence_no BETWEEN s.sequence_no-1 AND s.sequence_no+1 "
                    f"WHERE s.user_id=? AND s.memory_id IN ({placeholders}) LIMIT ?",
                    [user_id, *seed_chunk, candidate_limit],
                ).fetchall()
                for row in neighbor_rows:
                    graph_depths.setdefault(str(row["memory_id"]), 1)
        if not graph_depths and re.match(r"\s*(?:(?:what|which|who|where|when|why|how)\b|什么|哪个|谁|哪里|何时|为什么|如何)", query, re.IGNORECASE):
            # A bounded, source-only recall path for paraphrases outside the
            # small deterministic alias list. Unknown keyword probes still return [].
            recent_rows = conn.execute(
                "SELECT memory_id FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, min(top_k, 100)),
            ).fetchall()
            graph_depths = {str(row["memory_id"]): 0 for row in recent_rows}
        if not graph_depths:
            return []
        documents = self._load_ids(user_id, graph_depths.keys())
        q_terms = query_terms(query)
        q_set = set(q_terms)
        phrase = normalize(query)
        doc_token_sets = {doc.memory_id: self._memory_tokens(doc) for doc in documents}
        document_count = max(1, len(documents))
        document_frequency = {token: sum(token in token_set for token_set in doc_token_sets.values()) for token in q_set}
        query_temporal_key = _time_sort_key(query_event_time)
        temporal_question = "temporal" in self.features and bool(q_set.intersection({"when", "date", "time", "year", "before", "after", "earlier", "later", "latest", "current", "currently", "now", "先前", "之前", "之后", "最近", "现在", "当前"}))
        document_time_keys = {}
        document_time_windows = {}
        for doc in documents:
            keys = _structure_time_keys(doc.structure)
            windows = doc.structure.get("event", {}).get("time_windows", [])
            if not windows and not doc.structure.get("event", {}).get("temporal_expression") and doc.timestamp_ms is not None:
                source_date = datetime.fromtimestamp(doc.timestamp_ms / 1000, tz=timezone.utc)
                keys = [(source_date.year, source_date.month, source_date.day)]
                windows = [_time_bounds(keys[0])]
            document_time_keys[doc.memory_id] = keys
            document_time_windows[doc.memory_id] = windows
        dated_keys = [key for keys in document_time_keys.values() for key in keys]
        earliest_key = min(dated_keys, default=None)
        latest_key = max(dated_keys, default=None)
        temporal_words = {"before", "after", "earlier", "later", "latest", "current", "currently", "now", "most", "recent", "previous", "present", "subsequent", "date", "time", "year", "person", "author", "team", "location", "place", "reason", "because", "method", "process", "way", "先前", "之前", "之后", "最近", "现在", "当前"}
        topic_terms = q_set - temporal_words
        topic_docs = [doc for doc in documents if doc_token_sets[doc.memory_id].intersection(topic_terms)
                      and (not query_entities or query_entities.intersection(doc.structure.get("entities", [])))
                      and (not fact_predicates or any(self._fact_matches(f, fact_predicates, fact_subjects) for f in doc.structure.get("facts", [])) or not fact_subjects)]
        all_source_timed = bool(topic_docs) and all(doc.timestamp_ms is not None for doc in topic_docs)
        latest_topic_id = max(topic_docs, key=lambda doc: (doc.timestamp_ms if all_source_timed else doc.created_at, doc.memory_id)).memory_id if topic_docs else None
        earliest_topic_id = min(topic_docs, key=lambda doc: (doc.timestamp_ms if all_source_timed else doc.created_at, doc.memory_id)).memory_id if topic_docs else None
        scored: list[tuple[float, Memory]] = []
        fact_states: dict[str, list[dict[str, Any]]] = {}
        for id_chunk in _chunks(graph_depths, 350):
            placeholders = ",".join("?" for _ in id_chunk)
            for row in conn.execute(f"SELECT * FROM memory_facts WHERE user_id=? AND memory_id IN ({placeholders})", [user_id, *id_chunk]):
                fact_states.setdefault(row["memory_id"], []).append(dict(row))
        explicit_value_check = any(normalize(f["value"]) in normalize(query) for rows in fact_states.values() for f in rows if self._fact_matches(f, fact_predicates, fact_subjects))
        if fact_predicates and not explicit_value_check and query_order_hint >= 0 and not time_window:
            positives = [doc for doc in topic_docs if any(f["status"] == "active" and f["polarity"] for f in fact_states.get(doc.memory_id, []) if self._fact_matches(f, fact_predicates, fact_subjects))]
            self_reports = [doc for doc in positives if doc.role == "user" and any(f["subject"] == "__user__" and f["status"] == "active" and f["polarity"] for f in fact_states.get(doc.memory_id, []) if self._fact_matches(f, fact_predicates, fact_subjects))]
            if self_reports and requested_role != "assistant":
                positives = self_reports
            if positives:
                latest_topic_id = max(positives, key=lambda doc: (doc.timestamp_ms if all_source_timed else doc.created_at, doc.memory_id)).memory_id
        now = time.time()
        avg_len = max(1.0, sum(len(doc.token_list) for doc in documents) / len(documents))
        for doc in documents:
            doc_terms = doc_token_sets[doc.memory_id]
            expanded_doc_terms = list(doc.token_list)
            for phrase_item in doc.model_terms:
                expanded_doc_terms.extend(terms(phrase_item))
            if "facts" in self.features:
                expanded_doc_terms.extend(terms(" ".join(structure_index_terms(doc.structure))))
            counts: dict[str, int] = {}
            for token in expanded_doc_terms:
                counts[token] = counts.get(token, 0) + 1
            bm25 = 0.0
            for token in q_set:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                df = document_frequency[token]
                idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
                denominator = tf + 1.5 * (0.75 + 0.25 * len(doc.token_list) / avg_len)
                bm25 += idf * (tf * 2.5) / denominator
            normalized_content = normalize(doc.content)
            phrase_bonus = 3.0 if len(phrase) > 3 and phrase in normalized_content else 0.0
            overlap = len(q_set.intersection(doc_terms)) / max(1, len(q_set))
            relation_values = {value for relation in doc.structure.get("relations", []) for value in (relation.get("subject"), relation.get("object"), relation.get("previous")) if value}
            entity_overlap = len(query_entities.intersection(set(doc.structure.get("entities", [])))) / max(1, len(query_entities)) if "entity" in self.features else 0.0
            relation_overlap = len(query_entities.intersection(relation_values)) / max(1, len(query_entities)) if "entity" in self.features else 0.0
            graph_bonus = 1.5 / (graph_depths.get(doc.memory_id, 2) + 1) if "graph" in self.features and doc.memory_id not in fts_rank else 0.0
            doc_temporal_keys = document_time_keys.get(doc.memory_id, [])
            doc_periods = document_time_windows.get(doc.memory_id, [])
            temporal_bonus = 0.0
            if "temporal" in self.features and query_temporal_key and relative_anchor and doc_periods:
                query_start, query_end = _time_bounds(query_temporal_key)
                if query_order_hint < 0 and all(start >= query_start for start, _ in doc_periods):
                    continue
                if query_order_hint > 0 and all(end <= query_end for _, end in doc_periods):
                    continue
            if time_window:
                # An explicitly dated event takes precedence over when it was
                # reported. The source date is only a fallback for undated text.
                in_window = any(start < window_end and end > window_start for start, end in doc_periods)
                temporal_bonus += 6.0 if in_window else -2.0 if doc_periods else 0.0
            if "temporal" in self.features and query_temporal_key and doc_temporal_keys and not time_window:
                if query_order_hint < 0 and any(_time_bounds(key)[1] <= _time_bounds(query_temporal_key)[0] for key in doc_temporal_keys):
                    temporal_bonus += 2.0
                elif query_order_hint > 0 and any(_time_bounds(key)[0] >= _time_bounds(query_temporal_key)[1] for key in doc_temporal_keys):
                    temporal_bonus += 2.0
                elif query_temporal_key in doc_temporal_keys:
                    temporal_bonus += 1.5
                elif len(query_event_time or "") in (4, 7) and any(
                    key[:len(date_parts)] == query_temporal_key[:len(date_parts)] for key in doc_temporal_keys
                ):
                    temporal_bonus += 1.25
            elif temporal_question and doc_temporal_keys and not time_window:
                temporal_bonus += 0.85
                if query_temporal_key is None and query_order_hint > 0 and max(doc_temporal_keys) == latest_key:
                    temporal_bonus += 1.0
                elif query_temporal_key is None and query_order_hint < 0 and min(doc_temporal_keys) == earliest_key:
                    temporal_bonus += 1.0
            if "temporal" in self.features and query_temporal_key is None and query_order_hint:
                if temporal_anchor_id is not None and temporal_anchor_value is not None:
                    value = doc.timestamp_ms if temporal_anchor_source else doc.created_at
                    if value is not None and (value < temporal_anchor_value if query_order_hint < 0 else value > temporal_anchor_value):
                        temporal_bonus += 5.0
                    elif doc.memory_id == temporal_anchor_id:
                        temporal_bonus -= 1.5
                elif query_order_hint > 0 and doc.memory_id == latest_topic_id:
                    temporal_bonus += 5.0
                elif query_order_hint < 0 and doc.memory_id == earliest_topic_id:
                    temporal_bonus += 5.0
            session_bonus = 0.75 if session_id and doc.session_id == session_id else 0.0
            age_days = max(0.0, (now - doc.created_at) / 86400.0)
            recency_bonus = 0.35 * math.exp(-age_days / 90.0) if "temporal" in self.features else 0.0
            if "conflict" in self.features and query_order_hint > 0 and doc.status == "active":
                temporal_bonus += 0.75
            elif "conflict" in self.features and query_order_hint > 0 and doc.status == "superseded":
                temporal_bonus -= 1.25
            elif "conflict" in self.features and query_order_hint < 0 and doc.status == "superseded":
                temporal_bonus += 0.75
            fact_bonus = 0.0
            matching_facts = [f for f in fact_states.get(doc.memory_id, []) if self._fact_matches(f, fact_predicates, fact_subjects)] if "facts" in self.features else []
            if matching_facts:
                fact_bonus += 3.0
                if "conflict" in self.features and query_order_hint >= 0 and not time_window and not query_event_time and not relative_anchor:
                    active = [f for f in matching_facts if f["status"] == "active"] if doc.status == "active" else []
                    fact_bonus += (5.0 if explicit_value_check or any(f["polarity"] for f in active) else 1.0) if active else -4.0
                # Reduce the penalty of an important exact property buried in a
                # long message. Evidence remains the full original message.
                fact_bonus += max((len(q_set.intersection(terms(f["text"]))) / max(1, len(q_set)) for f in matching_facts), default=0.0)
            rank_bonus = 1.5 / (fts_rank[doc.memory_id] + 1) if doc.memory_id in fts_rank else 0.0
            source_bonus = (6.0 if doc.role == requested_role else -4.0) if requested_role else 0.0
            score = bm25 + phrase_bonus + overlap + 4.0 * entity_overlap + 1.25 * relation_overlap + graph_bonus + temporal_bonus + fact_bonus + session_bonus + recency_bonus + rank_bonus + source_bonus
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].created_at, pair[1].memory_id))
        selected = self._select_evidence(scored, top_k, query, query_entities, broad_query) if "coverage" in self.features else [doc for _, doc in scored[:top_k]]
        output: list[dict[str, str]] = []
        for doc in selected:
            content = doc.content
            if include_source:
                source = f"role={doc.role}; session={doc.session_id}; order={doc.sequence_no}"
                if doc.timestamp_ms is not None:
                    instant = datetime.fromtimestamp(doc.timestamp_ms / 1000, tz=timezone.utc).isoformat()
                    source += f"; source_time={instant}"
                source += f"; ingested_at={datetime.fromtimestamp(doc.created_at, tz=timezone.utc).isoformat()}"
                states = fact_states.get(doc.memory_id, [])
                if states:
                    source += "; fact_states=" + ",".join(sorted({f"{f['subject']}/{f['predicate']}:{f['status']}" for f in states}))
                if doc.status != "active":
                    source += f"; record_status={doc.status}; superseded_by={doc.superseded_by}"
                content = f"[{source}] {content}"
            output.append({"id": doc.memory_id, "content": content})
        return output

    @staticmethod
    def _select_evidence(scored: list[tuple[float, Memory]], top_k: int, query: str,
                         query_entities: set[str], broad: bool) -> list[Memory]:
        """Preserve relevance; pack bounded connected evidence for chain queries.

        Global summaries use episode coverage, not an invented LLM summary.
        Ordinary keyword/fact retrieval keeps its original relevance ordering.
        """
        if not scored:
            return []
        if broad:
            groups: dict[str, list[Memory]] = {}
            for _, doc in scored:
                if query_entities and not query_entities.intersection(doc.structure.get("entities", [])):
                    continue
                groups.setdefault(doc.session_id, []).append(doc)
            if not groups:
                return [doc for _, doc in scored[:top_k]]
            selected: list[Memory] = []
            depth = 0
            while len(selected) < top_k:
                layer = [docs[depth] for docs in groups.values() if depth < len(docs)]
                if not layer:
                    break
                selected.extend(layer[:top_k - len(selected)])
                depth += 1
            return selected
        chain_query = bool(re.search(r"\b(who|whose|why|how|cause|caused|reason|relationship|connected|chain|path|led to|depends)\b|\bproject\b.*\b(manages|managed|leads|owns)\b|为什么|因果|谁的|关系|原因|导致|路径", query, re.IGNORECASE))
        if not chain_query or not query_entities or top_k < 2:
            return [doc for _, doc in scored[:top_k]]
        docs = [doc for _, doc in scored]
        by_entity: dict[str, list[Memory]] = {}
        for doc in docs:
            for entity in doc.structure.get("entities", []):
                by_entity.setdefault(entity, []).append(doc)
        selected = [docs[0]]
        used = {docs[0].memory_id}
        frontier = [docs[0]]
        # Two hops, at most two original messages per hop. Never duplicate a
        # source ID or use the query/gold as a new stored fact.
        for _ in range(2):
            neighbors: list[Memory] = []
            for anchor in frontier:
                for entity in anchor.structure.get("entities", []):
                    if entity in query_entities or len(by_entity.get(entity, [])) > max(8, len(docs) * 0.12):
                        continue
                    neighbors.extend(doc for doc in by_entity.get(entity, []) if doc.memory_id not in used)
            neighbor_ids = {doc.memory_id for doc in neighbors}
            frontier = [doc for doc in docs if doc.memory_id in neighbor_ids][:2]
            for doc in frontier:
                if len(selected) == top_k:
                    return selected
                selected.append(doc)
                used.add(doc.memory_id)
        selected.extend(doc for doc in docs if doc.memory_id not in used)
        return selected[:top_k]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
