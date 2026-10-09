# Chronicle-Memory V2

**Source-Preserving Temporal Evidence Retrieval for Long-Term Agent Memory**

Chronicle-Memory V2 implements the participant side of the current contract: synchronous Add and evidence-only Search. The competition platform performs Answer and Eval. V2 is a targeted continuation of [Chronicle-Memory V1](https://github.com/simple-boy/Chronicle-Memory), audited at commit `00d0862560aba0213ca122ef62a1bba37cf5e431`.

**Textual Smoke upgrade (2026-10-09):**  This upgrade starts from submitted V2 commit `26bd3aca10e4d6cf4262c5079f2e77e6c7ff9c94`. New official Smoke/Full results are **unknown**. 

Local verification in WSL Python 3.11.0: 106 API/scenario/parser/protocol tests and 13 retained V1 tests passed; pinned-V2 database migration passed. On 120 fixed public synthetic development cases, Recall@5 improved from 88.89% to 100%; all 88 defined first-source checks passed. 

## Compared with submitted V2

| Code limitation | V2 change | Files |
|---|---|---|
| Ordinary phone/residence/employer updates were not resolved by property | Exact-span atomic facts and subject/property state history | `fact_index.py`, `memory_core.py` |
| Latest ranking could promote an unrelated new topic | Require entity/property relevance; prioritize active scalar facts | `memory_core.py` |
| Question aliases were discarded before expansion | Expand interrogatives before filtering; retain attribute terms | `memory_core.py` |
| Calendar parsing missed English day/month dates and intervals | Calendar parser and half-open query windows; no server-clock inference | `temporal.py`, `memory_core.py` |
| Source stay ranges matched only endpoint dates | Store exact calendar windows; retrieve and rank by overlap | `temporal.py`, `memory_core.py` |
| Assistant claims could supersede user self-reports | Separate self-property histories by source role; prefer direct user statements for current user attributes | `memory_core.py` |
| Individual hit ranking could scatter a chain or miss early sessions | Bounded rare-entity evidence selection; summary episode coverage | `memory_core.py` |

These are engineering adaptations, not claims of new paper contributions. [LongMemEval, HippoRAG 2 and APEX-MEM](docs/SMOKE_UPGRADE_SOURCES.md) inform the changes. Raw messages remain the returned evidence. This build makes no LLM or embedding API calls.

## Overview

The system stores source messages with their original text, role, session, order, and supplied timestamp, except for narrowly recognized user instructions to forget an exact complete fact. It uses V1's SQLite FTS5, entity and relation indexes, and bounded graph expansion. V2 adds the Cycle 2 ordered `messages[]` contract, atomic idempotent writes, source-time indexing, small lexical expansions, adjacent-message recall, bounded cross-session temporal candidates, conservative explicit supersession, and query-conditioned ranking of current versus historical evidence.

Search returns ranked source evidence only. It does not write an answer into memory or generate a final response.

## Method

### Memory Writing

1. Validate the Cycle 2 Add body and preserve the exact `request_id`, `user_id`, and `session_id` values.
2. Persist each ordered source message as a raw record unless it is the narrowly recognized exact-fact forget instruction in step 6. Store its role, source timestamp if provided, message index, and session sequence number.
3. Derive event time, entities, relations and exact-span atomic facts. Relative write dates require a supplied source timestamp.
4. Build FTS5, temporal, entity/relation and `memory_facts` indexes in the same transaction. Fact keys retrieve detailed raw values rather than replacing them with summaries.
5. Single-valued properties compete within the same user/subject/property. Keep atomic state history; distinguish user and assistant first-person subjects. For properties about the user, keep source-role histories separate so an assistant claim cannot revoke a self-report. Multi-valued preferences stay independent unless an exact value is negated. Explicit `from X to Y` can inherit a property only from a unique prior subject/old-value match. A multi-attribute message can retain active and historical facts together.
6. Direct user `forget`/`delete`/`remove` instructions delete only complete, exactly matching source facts and their FTS/entity/relation/fact indexes; instructions do not become evidence. This does not implement semantic or topic-level forgetting.
7. Commit with SQLite WAL and `synchronous=FULL` before returning HTTP 200. A repeated request with the same payload returns success without duplicating messages.

There is **no lossy importance filter, automatic LLM summary, materialized episodic/semantic/profile layer, or vector embedding** in the default implementation. Those require measured benefit and a clear model-eligibility ruling before adding cost and complexity. `session_id` provides an episode grouping without replacing raw evidence.

### Memory Retrieval

1. Read the original question and optional multiple-choice `options`; never use a gold answer.
2. Retrieve FTS5, property, entity and calendar/source-time candidates. Calendar candidates include overlap with explicit source event windows, including the interior of a stay. Expand up to 12 seed messages by at most two informative entity hops (128 additions per hop) and bounded adjacent messages. Current queries may include recent same-user candidates.
3. Rank by lexical, phrase, entity, relation and temporal relevance; match atomic subject/property before prioritizing active state. Current user attributes prefer direct self-reports; explicit speaker questions favor that source role. Date/historical questions keep history accessible.
4. Ordinary queries preserve relevance order. Chain questions may pack at most two connected messages per hop near the strongest hit; broad summaries use round-robin source-session coverage. Raw `content` carries role, session, order, optional `source_time`, `ingested_at` and fact-status labels. Labels do not replace original evidence.
5. Return `{"data": [{"id": "...", "content": "..."}]}` in rank order. An empty result is `{"data": []}`.

The internal [JSON Schema](config/memory.schema.json) records the implemented fields. `MemoryStore` is SQLite; the lexical `MemoryIndex` is FTS5; the `TemporalIndex` uses `event_time_key` and `timestamp_ms`; the `EntityIndex` uses indexed entity/relation tables. There is no `VectorIndex` in this version. No public update/delete endpoint is specified by Cycle 2; internal status updates and administrative retention deletion are separate operations.

**Known limits:** regex extraction covers selected common properties, not arbitrary semantic facts. UTC is used because Add has no timezone. Search has no question timestamp: V2 uses the latest supplied timestamp in the same user scope as an explicit proxy for relative query windows, never server time. This assumption may be wrong for a question anchored elsewhere; no timestamp means unknown time. Mixed/missing source times use ingestion order for scalar history. Unnamed/pronominal links can still defeat graph retrieval. Global summary evidence is selected, not automatically summarized. Exact-fact forgetting is not semantic forgetting.

## Architecture

The top lane shows Add, source-preserving writes, state changes, and SQLite indexes. The lower lane shows parallel lexical, entity, temporal, neighbor, and bounded fallback retrieval, followed by ranking and evidence output. The dashed AML block is external to this service. The Mia/tea/coffee text is an illustrative case for the implemented narrow supersession rule. 

(docs/chronicle-memory-v2-paper-figure.png)

The current diagram is [architecture.mmd](docs/architecture.mmd): Add → grounded indexing/property history → SQLite raw/fact/entity/time indexes → query candidates → ranking/evidence selection → source evidence → external AML Answer/Eval. The previous PNG is a historical V2 illustration.

## Installation

Python 3.11+ and SQLite with FTS5 and JSON functions are required (JSON functions are built in by default in SQLite 3.38+). This implementation uses only the Python standard library; [requirements.txt](requirements.txt) has no package dependencies.

```bash
git clone https://github.com/simple-boy/Chronicle-Memory.git
cd Chronicle-Memory
python -B -m unittest discover -s tests -v
python -B -m unittest discover -s test -v
```

The first command runs V2 contract/scenario tests; the second keeps V1 storage regressions. `-B` avoids bytecode cache files. Docker is optional for local checks:

```text
docker build -t chronicle-memory-v2:local .
docker run --rm -p 8000:8000 --env-file .env.private -v chronicle-memory-data:/app/data chronicle-memory-v2:local
```

Create `.env.private` outside source control with `MEMORY_API_KEY=<private random value>`. The named volume persists the SQLite database across container restarts. The Dockerfile copies `scripts/purge_expired.py` into the image, so a host scheduler can run it against the mounted database. Docker was **not available in the development environment**, so the image and script-in-container path have not been verified here.

## API

The `/add` and `/search` paths are this deployment's URLs; the platform lets participants configure endpoint URLs. `GET /health` needs no authentication. For Add/Search set `MEMORY_API_KEY` and use `Authorization: Bearer ...`, `Authorization: Token ...`, or `X-Api-Key: ...`.

### `POST /add`

```json
{
  "request_id": "demo-1",
  "messages": [
    {"role": "user", "timestamp": 1782432000000, "content": "Yesterday I went to the supermarket."},
    {"role": "assistant", "content": "You bought apples."}
  ],
  "user_id": "demo-user",
  "session_id": "demo-session"
}
```

HTTP 200 follows a durable, immediately searchable write:

```json
{"success": true, "request_id": "demo-1", "user_id": "demo-user", "session_id": "demo-session"}
```

The endpoint rejects non-text content arrays because this build targets the **Textual** track. It does not implement the separate image-containing Multimodal contract.

### `POST /search`

```json
{"query": "Where did I go yesterday?", "user_id": "demo-user", "top_k": 100}
```

`options` may be supplied as a top-level array of strings for choice questions. Search is scoped only by the exact `user_id`; `session_id` is not a filter. Returned item count never exceeds `top_k` (formal Top K is 100). A result contains a stable `id` and non-empty source `content`; no final answer is generated.

Validation errors use HTTP 422, invalid credentials HTTP 401, an inconsistent retry HTTP 409, and unexpected failures HTTP 500. The configurable `MAX_REQUEST_BYTES` default is 32 MiB; oversized input is rejected explicitly, never silently truncated. Official [API details](https://agentmemories.ai/api-guide) take precedence over this README.

## Running

For a local smoke test:

```bash
export MEMORY_DB_PATH='data/memories.sqlite3'
read -rsp 'Memory System Key: ' MEMORY_API_KEY; printf '\n'
export MEMORY_API_KEY
python -B app.py
```

In a second terminal send the JSON bodies above to `http://127.0.0.1:8000/add` and `/search`. `tests/test_api.py` starts the real HTTP server in a subprocess and checks the same contract automatically. For formal evaluation, deploy this container or service behind a stable public HTTPS endpoint, configure a persistent volume and private Memory System Key, then bind those URLs in the AML Evaluation Access Request. A local loopback URL cannot be submitted.

Operational data retention: do not log benchmark payloads. Run `python -B scripts/purge_expired.py --db <database path> --days 29 --apply` at least daily after evaluation use; it deletes entire inactive `user_id` scopes and their indexes. The script is dry-run without `--apply`. On `--apply`, it takes a write lock **before** selecting expired scopes, enables SQLite `secure_delete`, then performs `VACUUM` and WAL truncation after deletion. A busy WAL checkpoint needs a maintenance-window retry. Backups, snapshots, logs, exports, and third-party copies still require separate deletion. Verify the deployment schedule and all copies meet the official **delete within 30 days after the run** requirement.

## Evaluation

`evaluation/diagnostic.py` accepts only user-supplied public or synthetic JSONL with official Add-shaped `writes`, `queries`, and optional `gold_substrings` for evidence-level diagnostics:

```bash
python -B evaluation/diagnostic.py --input evaluation/example.jsonl --variant full
python -B evaluation/diagnostic.py --input evaluation/example.jsonl --variant lexical
python -B evaluation/diagnostic.py --input evaluation/example.jsonl --variant v1 --v1-core ../Chronicle-Memory/memory_core.py
```

For `--variant v1`, clone V1 separately and pin commit `00d0862560aba0213ca122ef62a1bba37cf5e431`; that older adapter is not the Smoke-upgrade baseline. The upgrade comparison uses submitted **V2** commit `26bd3aca10e4d6cf4262c5079f2e77e6c7ff9c94` and `evaluation/smoke_upgrade_diagnostic.py`; see [the measured report and WSL guide](docs/SMOKE_UPGRADE.md).

The script reports Recall@1/5/10, MRR, Add/Search p50/p95, memory bytes, and model call counts. These are **local evidence diagnostics, not official AML metrics**. The bundled toy file is a functionality check, not a performance benchmark. Public AML pipelines provide only partial Answer/Eval components and no complete corpus, Add/Search orchestrator, private labels, or Full score reproduction. Official scores require the platform's Smoke and Full runs.

## Competition Submission

Publish source modules, Dockerfile, tests, synthetic diagnostics, schemas, docs and MIT license at a fixed commit. Exclude `.env*`, SQLite/WAL files, private keys and private benchmark data. Redeploy that commit, register a new version (suggested `v0.3.0`) and run Textual Smoke. The Full eligibility of model-free Add still needs organizer clarification; see [submission.md](submission.md).

## Acknowledgement

This is a continuation of the maintainer's Chronicle-Memory. Original technical sources are credited in [SMOKE_UPGRADE_SOURCES.md](docs/SMOKE_UPGRADE_SOURCES.md). No external paper scores are claimed as Chronicle results. MIT authorization was confirmed by the maintainer.

