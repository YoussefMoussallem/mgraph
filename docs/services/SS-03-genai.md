# SS-03 — GenAI proxy

**Status:** contract defined · implementation in progress

## Responsibility

Centralized LLM and embedding gateway. Handles provider routing, rate limiting, metering, and prompt-cache policies so individual services do not embed provider SDKs directly.

## Capabilities

- Chat completions (multi-provider)
- Embeddings (batch and single)
- Model inventory and capability discovery

## Consumers

- SS-05 Knowledge Base (embedding during ingestion)
- Platform apps (agent workflows, copilots)

## SDK

Python **and** TypeScript client packages for this service (`llm-provider`, `langfuse-client`) live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks). The package catalog, install instructions, and versioning policy are owned by [`sdks/README.md`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/README.md) (cross-language feature parity: [`sdks/PARITY.md`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/PARITY.md)).

Published SDK documentation: [Python](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/) · [TypeScript](https://pwc-me-adv-strategyand.github.io/infra-platform-services/typescript/)

## Implementation

See [`services/genai/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/genai).

## Full specification

Referenced in [architecture standard §9](../architecture/foundation-services-and-sharing-framework.md#9-ss-05--knowledge-base--rag-service-standalone-multi-tenant-scalable) (KB ingestion pipeline).
