"""Local, evidence-level diagnostic on user-supplied public or synthetic JSONL.

This is not Answer/Eval pipeline and must not be used on private AML data.
Each line contains {"writes": [official Add bodies], "queries": [{"query": ...,
"user_id": ..., "top_k": ..., "gold_substrings": [...]}]}.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_core import MemoryStore  # noqa: E402


VARIANTS = {
    "v1": None,
    "full": MemoryStore.DEFAULT_FEATURES,
    "lexical": frozenset(),
    "minus_temporal": MemoryStore.DEFAULT_FEATURES - {"temporal"},
    "minus_entity_graph": MemoryStore.DEFAULT_FEATURES - {"entity", "graph"},
    "minus_adjacent": MemoryStore.DEFAULT_FEATURES - {"adjacency"},
    "minus_conflict": MemoryStore.DEFAULT_FEATURES - {"conflict"},
    "minus_options": MemoryStore.DEFAULT_FEATURES - {"options"},
}


def load_v1_store(core_path: Path):
    """Load an independently checked-out V1 memory_core.py without its broken app import."""
    if not core_path.is_file():
        raise FileNotFoundError(core_path)
    if core_path.resolve() == (Path(__file__).resolve().parents[1] / "memory_core.py").resolve():
        raise ValueError("--v1-core must point to a separate pinned V1 checkout, not this V2 core")
    spec = importlib.util.spec_from_file_location("chronicle_v1_core_for_diagnostic", core_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load V1 core from {core_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoryStore


def v1_chunk_text(messages: list[dict]) -> str:
    """Preserve all source turns when adapting Cycle 2 Add to V1's single content."""
    return "\n".join(
        f"[role={message['role']}"
        + (f"; timestamp_ms={message['timestamp']}" if "timestamp" in message else "")
        + f"] {message['content']}"
        for message in messages
    )


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run(path: Path, variant: str = "full", v1_core: Path | None = None) -> dict:
    if variant == "v1" and v1_core is None:
        raise ValueError("--v1-core must name the exact upstream V1 memory_core.py")
    add_times: list[float] = []
    search_times: list[float] = []
    recall = {1: [], 5: [], 10: []}
    reciprocal_ranks: list[float] = []
    add_calls = 0
    search_calls = 0
    memory_bytes = 0
    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "diagnostic.sqlite3"
        store = load_v1_store(v1_core)(str(db_path)) if variant == "v1" else MemoryStore(str(db_path), features=VARIANTS[variant])
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                case = json.loads(line)
                for write in case.get("writes", []):
                    start = time.perf_counter()
                    if variant == "v1":
                        store.add(
                            request_id=write["request_id"], user_id=write["user_id"],
                            session_id=write["session_id"], content=v1_chunk_text(write["messages"]),
                        )
                    else:
                        store.add_messages(**write)
                    add_times.append(time.perf_counter() - start)
                    add_calls += 1
                for question in case.get("queries", []):
                    start = time.perf_counter()
                    if variant == "v1":
                        # V1 has no options parameter. Give it the same choice
                        # text as query context, without any gold answer.
                        query_text = " ".join([question["query"], *(question.get("options") or [])])
                        results = store.search(
                            user_id=question["user_id"], query=query_text,
                            top_k=question.get("top_k", 100),
                        )
                    else:
                        results = store.search(
                            user_id=question["user_id"], query=question["query"],
                            top_k=question.get("top_k", 100), options=question.get("options"),
                            include_source=True,
                        )
                    search_times.append(time.perf_counter() - start)
                    search_calls += 1
                    gold = question.get("gold_substrings")
                    if gold is None:
                        continue
                    if not isinstance(gold, list) or not gold or any(not isinstance(item, str) or not item for item in gold):
                        raise ValueError(f"line {line_number}: gold_substrings must be a non-empty string array")
                    ranks = [
                        next((rank for rank, item in enumerate(results, 1) if needle.casefold() in item["content"].casefold()), None)
                        for needle in gold
                    ]
                    for k in recall:
                        recall[k].append(sum(rank is not None and rank <= k for rank in ranks) / len(ranks))
                    reciprocal_ranks.append(1.0 / min((rank for rank in ranks if rank is not None), default=float("inf")))
        store._connection().execute("PRAGMA wal_checkpoint(PASSIVE)")
        memory_bytes = sum(file.stat().st_size for file in db_path.parent.glob("diagnostic.sqlite3*") if file.is_file())
        if variant == "v1":
            store._connection().close()
        else:
            store.close()
    return {
        "kind": "local_evidence_diagnostic_not_official_AML_score",
        "variant": variant,
        "baseline_note": "V1 raw storage core with a single-chunk Add adapter; unavailable model_adapter is not called" if variant == "v1" else None,
        "add_calls": add_calls,
        "search_calls": search_calls,
        "recall_at_1": statistics.mean(recall[1]) if recall[1] else None,
        "recall_at_5": statistics.mean(recall[5]) if recall[5] else None,
        "recall_at_10": statistics.mean(recall[10]) if recall[10] else None,
        "mrr": statistics.mean(reciprocal_ranks) if reciprocal_ranks else None,
        "add_p50_ms": percentile(add_times, 0.5) * 1000 if add_times else None,
        "add_p95_ms": percentile(add_times, 0.95) * 1000 if add_times else None,
        "search_p50_ms": percentile(search_times, 0.5) * 1000 if search_times else None,
        "search_p95_ms": percentile(search_times, 0.95) * 1000 if search_times else None,
        "memory_bytes": memory_bytes,
        "llm_calls": 0,
        "embedding_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="full")
    parser.add_argument("--v1-core", type=Path, help="Path to the pinned original V1 memory_core.py, required for --variant v1")
    arguments = parser.parse_args()
    result = run(arguments.input, arguments.variant, arguments.v1_core)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
