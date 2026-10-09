"""Deterministic synthetic evidence retrieval diagnostic; not official Smoke.

Every case has a separate database and user. Only ``writes`` reach add_messages;
only query/user_id/options/top_k/include_source reach search. Gold annotations
are consumed after retrieval, never supplied to the store. Cases are designed
for engineering diagnosis, not a held-out estimate of competition performance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path(__file__).with_name("smoke_upgrade_cases.jsonl")
DEFAULT_BASELINE = ROOT / "artifacts" / "baseline" / "memory_core.py"
TOP_K = 10
FIXTURE_VERSION = "synthetic-v1-2026-10-09"
GROUPS = (
    "scalar_phone", "scalar_residence", "scalar_name", "source_speaker",
    "two_hop", "causal_chain", "current_scalar", "other_attribute",
    "multi_value", "assistant_authority", "historical", "relative_date",
    "english_date", "interval", "broad_summary",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_core(path: Path, label: str):
    """Independently import a core (including dataclass sys.modules support)."""
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    module_name = "chronicle_smoke_" + label + "_" + sha256(path)[:12]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Submitted V2 is self contained; the upgrade may import local helper modules.
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def timestamp(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def generate_cases(variants: int = 8) -> list[dict[str, Any]]:
    """Fixed public templates, independent identities, dates and lexical variants.

    These are deterministic template variations, not 120 independent natural
    examples. They never use official tasks or answers. Keep this function and
    the checked-in fixture fixed when inspecting upgrade retrieval outcomes.
    """
    if variants < 1:
        raise ValueError("variants must be positive")
    cases: list[dict[str, Any]] = []
    cities = ("Oslo", "Kyoto", "Dakar", "Lima", "Bergen", "Prague", "Bristol", "Turin")
    surnames = ("Avery", "Blake", "Casey", "Devon", "Ellis", "Finley", "Harper", "Jordan")
    for group in GROUPS:
        for variant in range(variants):
            case_id = f"{group}-{variant:02d}"
            user_id = f"synthetic-{case_id}"
            project = f"Project Atlas{variant:02d}"
            service = f"Service Beacon{variant:02d}"
            connector = f"Connector Cedar{variant:02d}"
            city = cities[variant % len(cities)]
            old_city = cities[(variant + 3) % len(cities)]
            name = surnames[variant % len(surnames)]
            phone = f"+1-202-555-{1000 + variant:04d}"
            old_phone = f"+1-202-555-{2000 + variant:04d}"
            topic = project if group in {"two_hop", "causal_chain", "relative_date", "english_date", "interval", "broad_summary"} else "personal records"
            writes: list[dict[str, Any]] = []

            def record(record_id: str, content: str, day: str = "2026-03-02", role: str = "user", session: str | None = None) -> str:
                writes.append({
                    "record_id": record_id,
                    "request_id": f"{case_id}-{record_id}", "user_id": user_id,
                    "session_id": session or f"{case_id}-session-{len(writes) % 4}",
                    "messages": [{"role": role, "timestamp": timestamp(day), "content": content}],
                })
                return record_id

            # Twenty ordinary distractors per independent case. Half share the
            # topic; they contain no synthetic marker that distinguishes gold.
            for index in range(20):
                if index % 4 == 0:
                    content = f"The {topic} meeting agenda lists refreshments and the room booking for team {index}."
                elif index % 4 == 1:
                    content = f"Colleague Person{index:02d} saved a phone number and residence in a private address book."
                elif index % 4 == 2:
                    content = f"The {topic} archive contains a draft diagram and a budget discussion from January."
                else:
                    content = f"I checked the library hours and the train timetable for trip {index}."
                record(f"noise-{index:02d}", content, day=f"2026-02-{index + 1:02d}")

            support: list[str] = []
            first: str | None = None
            query: str
            if group == "scalar_phone":
                support = [record("phone", f"My phone number is {phone}.")]
                query = "What is my phone number?"
                first = "phone"
            elif group == "scalar_residence":
                support = [record("residence", f"I live in {city}.")]
                query = "Where is my residence?"
                first = "residence"
            elif group == "scalar_name":
                support = [record("name", f"My name is {name}.")]
                query = "What is my name?"
                first = "name"
            elif group == "source_speaker":
                support = [record("own-phone", f"My phone number is {phone}.", session=f"{case_id}-verified")]
                record("reported-phone", f"Mira said her phone number is {old_phone}.", day="2026-03-03")
                record("other-phone", f"Colleague Nolan's phone number is +1-202-555-9000.", day="2026-03-04")
                query = "What is my own phone number, as reported by me?"
                first = "own-phone"
            elif group == "two_hop":
                support = [
                    record("project-service", f"{project} depends on {service}."),
                    record("service-storage", f"{service} uses PostgreSQL as its storage system.", day="2026-03-03"),
                ]
                query = f"Which storage system underlies {project}?"
            elif group == "causal_chain":
                support = [
                    record("project-delay", f"{project} was delayed because {service} stopped responding."),
                    record("service-failure", f"{service} stopped responding because {connector} failed.", day="2026-03-03"),
                    record("connector-cause", f"{connector} failed because its certificate had expired.", day="2026-03-04"),
                ]
                query = f"Why was {project} delayed? Give the complete cause chain."
            elif group == "current_scalar":
                record("old-phone", f"My phone number is {old_phone}.", day="2026-03-01")
                support = [record("current-phone", f"My phone number is now {phone}.", day="2026-04-02")]
                query = "What is my current phone number?"
                first = "current-phone"
            elif group == "other_attribute":
                support = [record("retained-phone", f"My phone number is {phone}.", day="2026-03-01")]
                record("new-residence", f"I now live in {city}.", day="2026-04-02")
                record("new-name", f"My name is now {name}.", day="2026-04-03")
                query = "What is my current phone number?"
                first = "retained-phone"
            elif group == "multi_value":
                support = [
                    record("like-one", "I like hiking and enjoy mountain trails.", day="2026-03-01"),
                    record("like-two", "I also like painting and enjoy watercolors.", day="2026-04-02"),
                ]
                query = "Which activities do I like? Include all stated preferences."
            elif group == "assistant_authority":
                support = [record("user-residence", f"I live in {city}.", day="2026-03-01", session=f"{case_id}-confirmed")]
                record("assistant-guess", f"You currently live in {old_city}.", day="2026-04-02", role="assistant")
                query = "Where do I currently live according to my own statement?"
                first = "user-residence"
            elif group == "historical":
                support = [record("former-residence", f"I lived in {old_city} in 2025.", day="2025-09-01")]
                record("current-residence", f"I now live in {city}.", day="2026-04-02")
                query = "Where did I live in 2025?"
                first = "former-residence"
            elif group == "relative_date":
                day = datetime(2026, 5, 10, tzinfo=timezone.utc) + timedelta(days=variant)
                prior = day - timedelta(days=1)
                support = [record("yesterday-visit", f"Yesterday I visited the {project} workshop in {city}.", day=day.date().isoformat())]
                record("today-visit", f"Today I visited the {project} office in {old_city}.", day=day.date().isoformat())
                query = f"Where did I visit for {project} on {prior.date().isoformat()}?"
                first = "yesterday-visit"
            elif group == "english_date":
                day_num = 10 + variant
                support = [record("dated-launch", f"{project} launched in {city} on June {day_num}, 2026.", day="2026-07-01")]
                record("other-launch", f"{project} launched a separate pilot in {old_city} on July {day_num}, 2026.", day="2026-07-02")
                query = f"Where did {project} launch on 2026-06-{day_num:02d}?"
                first = "dated-launch"
            elif group == "interval":
                start = 10 + variant
                support = [record("interval-visit", f"I visited the {project} workshop in {city} from June {start} to June {start + 2}, 2026.", day="2026-07-01")]
                record("outside-visit", f"I visited the {project} office in {old_city} on July 20, 2026.", day="2026-07-02")
                query = f"Where was I for {project} on 2026-06-{start + 1:02d}?"
                first = "interval-visit"
            elif group == "broad_summary":
                support = [
                    record("architecture", f"{project} uses PostgreSQL for durable storage."),
                    record("privacy-rule", f"{project} must keep customer data encrypted at rest.", day="2026-03-03"),
                    record("budget-rule", f"{project} has a monthly hosting limit of 80 dollars.", day="2026-03-04"),
                    record("review-decision", f"{project} requires a security review before release.", day="2026-03-05"),
                    record("release-decision", f"{project} will release its first version to internal testers only.", day="2026-03-06"),
                ]
                query = f"Summarize all stored decisions and constraints for {project}."
            else:
                raise AssertionError(group)

            # Move the leading half of distractors after factual evidence, so
            # support is neither always oldest nor always newest. Source times
            # and intra-source order remain immutable.
            writes = writes[10:] + writes[:10]
            by_record = {write["record_id"]: write for write in writes}
            question: dict[str, Any] = {
                "user_id": user_id, "query": query, "top_k": TOP_K,
                "gold_record_ids": support,
                "gold_substrings": [by_record[record_id]["messages"][0]["content"] for record_id in support],
            }
            if first:
                selected = by_record[first]
                question["gold_first_record_id"] = first
                question["expected_source"] = {
                    "role": selected["messages"][0]["role"],
                    "session": selected["session_id"],
                    "timestamp_ms": selected["messages"][0]["timestamp"],
                }
            cases.append({
                "case_id": case_id, "group": group, "variant_id": variant,
                "fixture_version": FIXTURE_VERSION, "data_origin": "public_synthetic_template",
                "noise_records": 20, "writes": writes, "queries": [question],
            })
    return cases


def write_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for case in cases), encoding="utf-8")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                case = json.loads(line)
                case.setdefault("case_id", f"line-{line_number}")
                case.setdefault("group", "supplied")
                cases.append(case)
    if not cases:
        raise ValueError("input contains no cases")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("case_id must be unique")
    for case in cases:
        record_ids = [write.get("record_id", f"write-{index}") for index, write in enumerate(case.get("writes", []))]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError(f"{case['case_id']}: duplicate record_id")
        for question in case.get("queries", []):
            if question.get("top_k", TOP_K) != TOP_K:
                raise ValueError("every query must have the fixed top_k=10 budget")
            gold = question.get("gold_record_ids", question.get("gold_substrings"))
            if not isinstance(gold, list) or not gold or any(not isinstance(item, str) or not item for item in gold):
                raise ValueError(f"{case['case_id']}: non-empty gold_record_ids or gold_substrings required")
            if "gold_record_ids" in question and not set(gold) <= set(record_ids):
                raise ValueError(f"{case['case_id']}: unknown gold_record_id")
            if "gold_first_record_id" in question and question["gold_first_record_id"] not in gold:
                raise ValueError(f"{case['case_id']}: gold_first_record_id must be a support record")
    return cases


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * fraction
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def evaluate_query(question: dict[str, Any], results: list[dict[str, Any]], id_to_record: dict[str, str]) -> dict[str, Any]:
    if len(results) > TOP_K:
        raise ValueError("store exceeded top_k=10 evidence budget")
    ranked_records = [id_to_record.get(row["id"]) for row in results]
    if "gold_record_ids" in question:
        gold = question["gold_record_ids"]
        ranks = [next((i for i, record in enumerate(ranked_records, 1) if record == expected), None) for expected in gold]
    else:
        gold = question["gold_substrings"]
        ranks = [next((i for i, row in enumerate(results, 1) if expected.casefold() in row["content"].casefold()), None) for expected in gold]
    metrics: dict[str, Any] = {}
    for k in (1, 5, 10):
        metrics[f"recall_at_{k}"] = sum(rank is not None and rank <= k for rank in ranks) / len(ranks)
    for k in (5, 10):
        metrics[f"all_support_at_{k}"] = float(all(rank is not None and rank <= k for rank in ranks))
    first_found = min((rank for rank in ranks if rank is not None), default=None)
    metrics["mrr"] = 1.0 / first_found if first_found is not None else 0.0
    expected_first = question.get("gold_first_record_id")
    metrics["expected_first_success"] = float(bool(ranked_records) and ranked_records[0] == expected_first) if expected_first else None
    source = question.get("expected_source")
    source_ok: bool | None = None
    if source:
        content = results[0]["content"] if results else ""
        source_ok = all(f"{key}={source[key]}" in content for key in ("role", "session"))
        if "timestamp_ms" in source:
            instant = datetime.fromtimestamp(source["timestamp_ms"] / 1000, tz=timezone.utc).isoformat()
            source_ok = source_ok and f"source_time={instant}" in content
    metrics["expected_first_attributed_success"] = float(bool(metrics["expected_first_success"]) and bool(source_ok)) if source else None
    return {**metrics, "gold_support_ranks": ranks, "retrieved_record_ids": ranked_records,
            "retrieved_count": len(results), "source_match_at_first": source_ok}


METRICS = ("recall_at_1", "recall_at_5", "recall_at_10", "mrr", "all_support_at_5", "all_support_at_10", "expected_first_success", "expected_first_attributed_success")


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {"query_count": len(rows)}
    for key in METRICS:
        values = [row[key] for row in rows if row[key] is not None]
        output[key] = statistics.mean(values) if values else None
        if key.startswith("expected_first"):
            output[key + "_query_count"] = len(values)
    return output


def run_store(cases: list[dict[str, Any]], core_path: Path, label: str, features: frozenset[str] | None = None) -> dict[str, Any]:
    run_started = time.perf_counter()
    core_hash = sha256(core_path)
    helpers = [file for file in (core_path.parent / "fact_index.py", core_path.parent / "temporal.py") if file.is_file()]
    helper_hashes = {file.name: sha256(file) for file in helpers}
    import_started = time.perf_counter()
    module = load_core(core_path, label)
    import_ms = (time.perf_counter() - import_started) * 1000
    add_times: list[float] = []
    search_times: list[float] = []
    initialization_times: list[float] = []
    memory_bytes: list[int] = []
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="chronicle-synthetic-") as temporary:
        for index, case in enumerate(cases):
            db_path = Path(temporary) / f"case-{index:04d}.sqlite3"
            started = time.perf_counter()
            kwargs = {"features": features} if features is not None else {}
            store = module.MemoryStore(str(db_path), **kwargs)
            initialization_times.append((time.perf_counter() - started) * 1000)
            id_to_record: dict[str, str] = {}
            try:
                for write_index, write in enumerate(case.get("writes", [])):
                    # Explicit allow-list: no labels/queries/gold can enter writes.
                    body = {key: write[key] for key in ("request_id", "user_id", "session_id", "messages")}
                    started = time.perf_counter()
                    returned_ids = store.add_messages(**body)
                    add_times.append((time.perf_counter() - started) * 1000)
                    for memory_id in returned_ids:
                        id_to_record[memory_id] = write.get("record_id", f"write-{write_index}")
                for query_index, question in enumerate(case.get("queries", [])):
                    started = time.perf_counter()
                    results = store.search(user_id=question["user_id"], query=question["query"],
                                           options=question.get("options"), top_k=TOP_K, include_source=True)
                    search_times.append((time.perf_counter() - started) * 1000)
                    row = evaluate_query(question, results, id_to_record)
                    rows.append({"case_id": case["case_id"], "group": case["group"], "query_index": query_index, **row})
                store._connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")
                memory_bytes.append(sum(file.stat().st_size for file in db_path.parent.glob(db_path.name + "*") if file.is_file()))
            finally:
                store.close()
    by_group = {group: aggregate([row for row in rows if row["group"] == group]) for group in sorted({row["group"] for row in rows})}
    if sha256(core_path) != core_hash or any(sha256(file) != helper_hashes[file.name] for file in helpers):
        raise RuntimeError("core/helper files changed during diagnostic; freeze code and rerun")
    return {
        "label": label, "core_path": str(core_path.resolve()), "core_sha256": core_hash, "helper_sha256": helper_hashes,
        "features": sorted(features) if features is not None else "core_defaults",
        "case_count": len(cases), "add_calls": len(add_times), "search_calls": len(search_times),
        "overall": aggregate(rows), "by_group": by_group,
        "timing_ms": {
            "module_import": import_ms, "run_total": (time.perf_counter() - run_started) * 1000,
            "initialization_p50": percentile(initialization_times, 0.5), "initialization_p95": percentile(initialization_times, 0.95),
            "add_p50": percentile(add_times, 0.5), "add_p95": percentile(add_times, 0.95),
            "search_p50": percentile(search_times, 0.5), "search_p95": percentile(search_times, 0.95),
            "search_total": sum(search_times), "add_total": sum(add_times),
        },
        "database_bytes": {"sum": sum(memory_bytes), "per_case_p50": percentile(memory_bytes, 0.5), "per_case_p95": percentile(memory_bytes, 0.95), "checkpoint": "WAL TRUNCATE; includes main/WAL/SHM present at sample"},
        "llm_calls": 0, "embedding_calls": 0, "per_query": rows,
    }


def run(path: Path, baseline_core: Path, upgrade_core: Path = ROOT / "memory_core.py", only: str = "both", ablations: bool = False) -> dict[str, Any]:
    input_hash = sha256(path)
    cases = load_cases(path)
    stores: dict[str, Any] = {}
    if only in {"both", "baseline"}:
        stores["submitted_v2_baseline"] = run_store(cases, baseline_core, "submitted_v2_baseline")
    if only in {"both", "upgrade"}:
        stores["smoke_upgrade"] = run_store(cases, upgrade_core, "smoke_upgrade")
        if ablations:
            module = load_core(upgrade_core, "ablation_features")
            for name, disabled in (("minus_facts", {"facts"}),
                                   ("minus_evidence", {"coverage", "graph", "adjacency"}),
                                   ("minus_graph", {"graph"}),
                                   ("minus_temporal", {"temporal"})):
                stores[name] = run_store(cases, upgrade_core, name, frozenset(module.MemoryStore.DEFAULT_FEATURES) - disabled)
    if sha256(path) != input_hash:
        raise RuntimeError("input changed during diagnostic; freeze fixture and rerun")
    fixture_versions = sorted({case["fixture_version"] for case in cases if case.get("fixture_version")})
    return {
        "kind": "local_synthetic_retrieval_diagnostic_not_official_smoke", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(path.resolve()), "sha256": input_hash, "case_count": len(cases), "groups": sorted({case["group"] for case in cases}), "fixture_version": fixture_versions[0] if len(fixture_versions) == 1 else None},
        "environment": {"python_version": sys.version, "python_executable": sys.executable,
                        "platform": platform.platform(), "sqlite_version": sqlite3.sqlite_version,
                        "temporary_directory_root": tempfile.gettempdir(),
                        "temporary_storage_note": "RAM-backed tmpfs; timings do not measure durable production storage" if Path(tempfile.gettempdir()).as_posix().startswith("/dev/shm") else "Filesystem temporary databases; durability settings unchanged"},
        "protocol": {"top_k": TOP_K, "case_isolation": "one database per case", "query_visibility": "query used only for retrieval; gold labels used only after retrieval", "randomness": "deterministic fixed templates; no stochastic model and no seed variance claimed", "latency": "one first search after case writes; no repeat/warm-query claims", "mrr": "reciprocal rank of the first support, truncated at retrieval budget", "source_check": "expected first record plus role/session/source timestamp annotation"},
        "official_smoke_score": None, "official_full_score": None,
        "user_reported_previous_score": {"value": 49.94, "source": "user_report", "independently_verified": False, "comparison_with_synthetic_metrics": "invalid"},
        "llm_calls": 0, "embedding_calls": 0, "api_cost_usd": 0,
        "limitations": ["Public synthetic development diagnostic; not an official benchmark or held-out natural-data estimate.", "Template variants share structure; case count is not statistical independence of natural tasks.", "Metrics evaluate raw evidence retrieval, not answer correctness or factual reasoning.", "One deterministic run reports latency distribution across cases, not seed uncertainty."],
        "stores": stores,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-core", type=Path, default=DEFAULT_BASELINE, help="Pinned submitted V2 core; never named V1")
    parser.add_argument("--upgrade-core", type=Path, default=ROOT / "memory_core.py")
    parser.add_argument("--only", choices=("both", "baseline", "upgrade"), default="both")
    parser.add_argument("--generate", action="store_true", help="Write deterministic fixture to --input and exit")
    parser.add_argument("--ablations", action="store_true", help="Also run feature-removal variants of upgrade")
    args = parser.parse_args()
    if args.generate:
        write_cases(args.input, generate_cases())
        print(json.dumps({"case_count": len(generate_cases()), "input": str(args.input), "sha256": sha256(args.input)}))
        return
    report = run(args.input, args.baseline_core, args.upgrade_core, args.only, args.ablations)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "case_count": report["input"]["case_count"], "input_sha256": report["input"]["sha256"], "stores": {key: value["overall"] for key, value in report["stores"].items()}}))
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
