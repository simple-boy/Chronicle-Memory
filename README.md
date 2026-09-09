# Chronicle Memory

Chronicle Memory is an evidence-only textual memory system for the Agent Memory Challenge 2026. It is designed for the Academic Methods board and exposes the required synchronous `Add` and `Search` endpoints. Version `0.2.0` adds deterministic temporal-event records, entity/relation indexes, and bounded multi-hop candidate expansion.

## Method

The system stores each source chunk in SQLite under an explicit `user_id` and `session_id` boundary. Search ranks only stored evidence with a transparent hybrid scorer:

1. BM25-style lexical relevance with document-frequency weighting.
2. Exact phrase and token-overlap bonuses for fact and multi-hop questions.
3. A per-memory event record plus ordered `events[]` records for dates, event types, temporal expressions, and clause order.
4. Entity and relation indexes, including `previous` values for changes such as `SQLite -> Postgres`.
5. At most two graph hops from lexical/entity seeds, while keeping every candidate inside the requesting `user_id`.
6. Event-time and relative-order bonuses, session sequence numbers, recency weighting, and source-session diversity.
7. Optional `gpt-4o-mini` annotations/query expansion, used only to improve retrieval terms. The model never writes the final answer and Search never fabricates evidence.

This is an original implementation for this submission. It does not copy a third-party repository or benchmark answer. The platform remains responsible for Answer, Eval, and leaderboard publication.

## Run locally

Requires Python 3.11+.

```powershell
python app.py
```

The service listens on `http://127.0.0.1:8000` by default. Data is stored in `data/memories.sqlite3`.

To enable the official model path, provide a key outside the repository:

```powershell
$env:OPENAI_API_KEY = "..."
$env:OPENAI_MODEL = "gpt-4o-mini"
python app.py
```

Without a key, the deterministic retrieval path remains available for local contract tests. For a formal platform reproduction, configure `OPENAI_API_KEY` and keep `OPENAI_MODEL=gpt-4o-mini`.

Performance behavior: Add persists the source evidence first and returns immediately. Optional model annotations are then computed by a small background worker pool and update the search index when ready. Search uses the local FTS5 index first; it calls `gpt-4o-mini` for query expansion only after a local miss. Repeated annotation and expansion requests are cached in process memory.

## Docker

```powershell
docker build -t chronicle-memory:0.2.0 .
docker run --rm -p 8000:8000 -e OPENAI_API_KEY=YOUR_KEY chronicle-memory:0.2.0
```

Do not commit API keys. The service accepts `X-Api-Key`, `Authorization: Bearer ...`, or `Authorization: Token ...` when `MEMORY_API_KEY` is configured. If no key is configured, the service is unauthenticated for local smoke testing.

## API contract

Health is an unauthenticated `GET /health`.

Add is synchronous and returns only after persistence:

```bash
curl -X POST http://127.0.0.1:8000/add \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-1","user_id":"demo-user","session_id":"demo-session","content":"The launch is on 2026-08-07 in Shanghai."}'
```

Example response:

```json
{"success":true,"request_id":"demo-1","user_id":"demo-user","session_id":"demo-session","memory_ids":["mem_..."]}
```

Search returns relevance-ordered memory evidence and never a generated answer:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"When and where is the launch?","user_id":"demo-user","top_k":100}'
```

Example response:

```json
{"data":[{"id":"mem_...","content":"The launch is on 2026-08-07 in Shanghai."}]}
```

The formal contract uses `top_k <= 100`. `session_id` on Search is accepted as an optional local ranking hint, but `user_id` is the only isolation boundary.

## Verification

```powershell
python -m unittest discover -s tests -v
```

The structure is internal retrieval metadata. The public Search response remains evidence-only:

```json
{"data":[{"id":"mem_...","content":"The Atlas project migrated from SQLite to Postgres in March 2026."}]}
```

For example, after storing `Alice manages Atlas` and a separate Atlas migration memory, a query such as `What database did Alice's project use later?` seeds the Alice/Atlas memory, expands through the shared `Atlas` entity, and ranks the migration memory because it contains `Postgres` and the later event.

Manual smoke sequence:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/add -ContentType 'application/json' -Body (@{request_id='smoke-1';user_id='smoke-user';session_id='smoke-session';content='Ada reviewed the retrieval design in Oxford in 2026.'} | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/search -ContentType 'application/json' -Body (@{query='Where did Ada review the design?';user_id='smoke-user';top_k=100} | ConvertTo-Json)
```

## Submission checklist

- Track: Textual Memory.
- Division: Academic Methods.
- Route: public GitHub repository, platform-deployed Docker code.
- Version: `0.2.0`.
- Entrypoint: `python app.py` or the Docker command above.
- Endpoints: `GET /health`, `POST /add`, `POST /search`.
- Model declaration: `gpt-4o-mini` is the optional production annotation/query-expansion model; temporal parsing, entity/relation indexing, graph expansion, scoring, and the evidence boundary are implemented locally.
- Credentials: supply through the evaluation form or deployment environment only.
- Fixed-version rule: tag the repository before requesting formal Full evaluation and do not change the evaluated version afterward.

See [SUBMISSION.md](SUBMISSION.md) for a ready-to-paste application description.
