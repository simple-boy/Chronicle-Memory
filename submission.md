# Agent Memory Challenge 2026 Submission Draft

This file is a draft for the official evaluation request. Replace the contact placeholders in the web form. Do not put an Eval Key or Memory System Key in this repository.

## Basic information

- System name: Chronicle Memory
- Version: 0.1.0
- Evaluation type: Textual Memory
- Participant division: Academic Methods
- Submission route: Submit code for platform deployment
- Repository: `<public GitHub URL after publishing this directory>`
- Contact: `<name and email in the private form only>`

## Short description

Chronicle Memory is an evidence-only textual memory system for long-context, temporal, personalized, and multi-hop retrieval. It uses explicit per-user isolation, synchronous durable writes, BM25-style retrieval, phrase and temporal matching, recency weighting, session-aware ranking, and source-session diversity. Search returns ranked memory evidence only; it does not generate answers. The platform performs Answer and Eval under the unified benchmark contract.

## Technical contribution

The method combines a transparent lexical retriever with temporal and conversational signals that are useful for memory evaluation. Exact phrase matching protects named facts, year matching helps temporal questions, recency weighting handles evolving memories, and session diversity avoids returning many near-duplicate chunks from one source conversation. An optional `gpt-4o-mini` adapter extracts retrieval cues and expands queries, but cannot produce or alter returned evidence.

## Reproducibility

```text
docker build -t chronicle-memory:0.1.0 .
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=<provided privately at deployment> \
  -e OPENAI_MODEL=gpt-4o-mini \
  chronicle-memory:0.1.0
```

API entrypoints:

- `GET /health`
- `POST /add`
- `POST /search`

The full request and response contract, local tests, isolation behavior, and smoke commands are documented in `README.md`.

## Attribution and changes

This submission is an original implementation by the participant. No external repository, paper implementation, or benchmark answer is bundled. The design uses standard BM25-style relevance scoring and SQLite persistence, implemented directly in this repository. The only external model integration is the documented OpenAI API call to `gpt-4o-mini`; its use is limited to retrieval-side annotations and query expansion.

## Integrity statement

The system does not hard-code benchmark answers, read gold labels, share memories across `user_id` values, inject prompts into benchmark content, or generate final answers in Search. The evaluated version will be frozen at the declared tag.

## Public display

Approved for public display: system name, version, method description, repository URL, Docker instructions, and evaluation results. Keep personal contact information and all evaluation credentials private.
