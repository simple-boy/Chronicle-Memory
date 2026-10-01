# Cycle 2 — Submission Draft

This is a draft for the application. Replace every angle-bracketed field from the final deployment and fixed Git commit. This document is not evidence of acceptance, official Smoke, Full evaluation, or a leaderboard score. Submit keys and personal contact details only through the organizer's private form.

## Application fields

| Field | Value to submit |
|---|---|
| System | Chronicle-Memory V2 |
| Track | Textual Memory |
| Division | Open-source Methods |
| Public repository | `<public HTTPS GitHub URL>` |
| Fixed version | `<full Git commit SHA>`; optional immutable tag `<tag>` |
| Health URL | `https://<public-host>/health` |
| Add URL | `https://<public-host>/add` |
| Search URL | `https://<public-host>/search` |
| Add/Search authentication | `<Bearer / Token / X-Api-Key and private key delivery method>` |
| Capacity | `<tested Add concurrency, Search concurrency, latency, rate limits, storage and run duration>` |
| Contact | `<private form only>` |

**Deployment route:** the participant hosts the frozen implementation at a publicly reachable HTTPS service. A repository link and Dockerfile document reproducibility; they do not replace a live Add/Search service, and the organizer does not deploy the submitted code for Cycle 2. The Health endpoint remains accessible without authentication. Do not place a Memory System Key or Eval/Leaderboard Key in a URL, commit, container image, log, screenshot, or this draft.

## Form-ready method description

Chronicle-Memory V2 extends [Chronicle-Memory V1](https://github.com/simple-boy/Chronicle-Memory) for the Cycle 2 Add/Search contract. It stores ordered text messages with source user, session, role, optional event timestamp, and stable evidence ID in SQLite. The current implementation extracts lightweight event time and entity/relation cues, maintains FTS5 and relational indexes, and ranks lexical, temporal, bounded entity-link, adjacent-turn, and cross-session before/after candidates. Latest/current queries can include recent same-user candidates. Candidates are sorted by computed relevance without a per-session quota. Returned `content` contains source provenance and an `ingested_at` ingestion timestamp; `ingested_at` is not a separate Search JSON field or a substitute for a missing source time. Explicit change expressions can mark earlier matching facts as superseded while retaining historical evidence. A direct user instruction to forget/delete/remove a **complete, exactly matching fact** can remove that fact and its indexes; this is not general semantic forgetting. Search returns ranked source memories, not generated answers. The organizer performs Answer and Eval with its fixed pipeline.

The checked-in service uses Python's standard library and a deterministic retrieval path; **the present code does not make an LLM or embedding API call**. Any later model integration must be implemented, documented, tested, and frozen in the submitted commit. The organizer's FAQ, Docs, and Full checklist phrase Open-source model requirements differently; we will request written clarification on whether this model-free configuration is eligible before formal Full evaluation. No claim of a measured performance gain is made here.

**Clarification text for the private organizer channel (not yet sent):** “For Cycle 2 Textual Memory / Open-source Methods, is an Add/Search implementation that uses SQLite FTS5 and deterministic parsing, with zero LLM and zero embedding calls, eligible for Smoke and Full? The competition FAQ names `text-embedding-v4` and `gpt-4o-mini` for the academic group, whereas the Docs leave internal indexing unspecified and the Full checklist expects `gpt-4o-mini` in Add. If a model call is mandatory, which operation and model must use it? Is authenticated Bearer/Token/X-Api-Key required for Full, or may this division use no authentication?”

## API declaration

- `GET /health`: unauthenticated health probe.
- `POST /add`: accepts `request_id`, ordered nonempty `messages` (`role`, nonempty text `content`, optional Unix-millisecond `timestamp`), `user_id`, and `session_id`; responds with HTTP 200 and `success: true` plus the three echoed IDs after synchronous persistence and immediate searchability. Retries with the same `request_id` and payload are intended to be idempotent.
- `POST /search`: accepts `query`, `user_id`, `top_k`, and optional top-level `options` array. It returns a relevance-ordered `data` array with stable `id` and nonempty evidence `content`, bounded by `top_k`. An empty result is `{"data":[]}`. The platform's formal `top_k` is 100. Search does not require a `session_id` or emit a final answer.

The exact deployment URLs are configurable by the participant; `/add` and `/search` above name this implementation's current routes, not a mandatory URL path imposed by the organizer.

## Attribution and reproducibility

V2 is a continuation of the participant's [Chronicle-Memory V1](https://github.com/simple-boy/Chronicle-Memory). The final public repository should identify the V1 baseline and disclose each substantive V2 change, source and dependency licenses, exact commit, Python/Docker launch procedure, configuration, and capacity limits. The current implementation uses SQLite and FTS5 from Python's standard library. The Dockerfile includes the administrative `scripts/purge_expired.py` script, but its container build and deployed schedule remain unverified. The repository must not include evaluation corpus, gold labels, benchmark answers, or credentials.

Before filing this form, execute the local contract and regression tests, build and restart the Docker image with a persistent volume, test HTTPS Add→immediate Search and authentication from outside the host, and measure the declared Add/Search capacity. Then request the organizer's compatibility Smoke for this exact commit and configuration. The public [AML repository](https://github.com/AML-memory/agent-memory-leaderboard) does not provide the complete corpus or the online Add/Search orchestration, so local tests cannot stand in for official Smoke or Full.

## Data and integrity declaration

The service must isolate memories by exact `user_id`, preserve only evidence derived from Add messages except for narrowly matched explicit forget instructions, and avoid hard-coded benchmark answers, gold-label access, prompt injection, and final-answer generation within Search. Evaluation data and all derived copies are for the run only; do not train, fine-tune, analyze for other purposes, reconstruct, or distribute them. Arrange deletion of the database, indexes, snapshots, backups, request logs, and exported copies within **30 days after the run**, unless the organizer grants written permission otherwise. The included purge script selects expired whole-user scopes under a SQLite write lock, uses `secure_delete`, then performs `VACUUM` and WAL truncation; schedule `--days 29 --apply` at least daily after deployment. Verify the script on the persistent volume and separately remove backups and logs. Verify the deletion procedure and retention owner before Full.

## Submission state

The local implementation and rules audit exist. A local Git repository has been initialized, but it has no commit yet. The public repository URL, fixed commit SHA, built Docker image, live HTTPS endpoints, private key handoff, measured capacity, written model-rule clarification, organizer-issued Eval/Leaderboard Key, official Smoke, and Full result are **pending**. The working tree is changing, so its final regression result must be recorded before submission. See [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) for the gate-by-gate status. The official [Rules](https://agentmemories.ai/rules), [Docs](https://agentmemories.ai/docs), [API Guide](https://agentmemories.ai/api-guide), and [Evaluation](https://agentmemories.ai/evaluation) control the final filing.
