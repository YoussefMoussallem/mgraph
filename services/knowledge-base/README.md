# SS-05 Knowledge Base / RAG

**Docs:** [SS-05-knowledge-base.md](../../docs/services/SS-05-knowledge-base.md)

## Status

Scaffold — service implementation to be added.

## Planned stack

- FastAPI
- PostgreSQL + pgvector (multi-tenant KB DB)
- Durable queue + worker pool for async ingestion
- SS-03 for embeddings, SS-12 for blob storage, Sharing Framework for ACL
