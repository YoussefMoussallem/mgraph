# SS-05 — Knowledge Base / RAG

**Status:** contract defined · implementation in progress

## Responsibility

Standalone, multi-tenant retrieval service. Owns KB metadata, document ingestion, chunking, embedding, and hybrid search (semantic + BM25). Platforms call it over HTTP — never embed KB logic locally.

## Design principles

- **Blob-first, process-async:** uploads go to blob via scoped SAS; workers process off a durable queue
- **M2M + propagated user:** service-to-service calls carry machine token and end-user identity
- **Sharing via framework:** every KB operation gated by Universal Sharing Framework

## Key endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/knowledge-bases` | Create KB |
| GET | `/v1/knowledge-bases/{id}` | Get KB metadata |
| POST | `/v1/knowledge-bases/{id}/documents` | Upload / register document |
| GET | `/v1/knowledge-bases/{id}/documents` | List documents |
| POST | `/v1/knowledge-bases/{id}/query` | Retrieval (semantic / hybrid) |
| DELETE | `/v1/knowledge-bases/{id}/documents/{docId}` | Remove document |

## SDK

No client SDK is published for this service yet. When one ships, it will live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks) and be documented on the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/).

## Implementation

See [`services/knowledge-base/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/knowledge-base).

## Full specification

[Architecture standard §9](../architecture/foundation-services-and-sharing-framework.md#9-ss-05--knowledge-base--rag-service-standalone-multi-tenant-scalable)
