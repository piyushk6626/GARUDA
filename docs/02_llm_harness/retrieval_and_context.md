# Retrieval and Context Management

## Three retrieval modes
1. **Structured** (primary): typed data tools (see `data_query_tools.md`).
2. **Graph**: ontology traversal for relational questions ("which crews can fly the aircraft assigned to mission M?").
3. **Document RAG**: doctrine, SOPs, ROE-like guidance, aircraft manuals (all synthetic or public), via `search_doctrine`.

## Document RAG
- Store: Postgres `pgvector` table `doc_chunk(chunk_id, doc_id, text, embedding vector(3072), meta jsonb)`.
- Chunking: ~500 tokens, 80 overlap, heading-aware (markdown).
- Hybrid search: vector cosine + Postgres full-text (`tsvector`), merged with reciprocal rank fusion.
- Embedding model: `text-embedding-3-large` via the same adapter (local `bge-m3` later).
- Tool: `search_doctrine(query, k=5) -> list[Passage(text, doc, score)]`.

## Context compression
- Tool outputs converted to compact tables/JSON with only needed fields.
- Large results stored in `working_data` with a handle `ref:abc123`; LLM sees summary + handle and can ask follow-up with `fetch_slice(ref, offset, limit)`.
- Rolling conversation summary after N turns; plan state is kept structured outside the message history.
- Scenario clock and data freshness injected into the system prompt each turn.

## Grounding
Responses cite tool call IDs (`[T3]`). The Critic rejects answers containing numbers not found in tool outputs (simple regex check against the cache for IDs, counts, times).

## Caching
Redis cache keyed by `(tool, hash(args), scenario_minute)` with 30-60 s TTL.
