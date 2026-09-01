# SS-12 — File / Blob Storage

**Status:** contract defined · implementation in progress

## Responsibility

Access-gated storage broker. Issues short-lived, blob-scoped SAS tokens only after an `access.check` against the Universal Sharing Framework. No storage keys in clients.

## Design principles

- Blobs bound to a resource via object-key convention: `{platform}/{resourceType}/{resourceId}/...`
- Metadata owned locally; bytes in blob storage (one account per platform)
- Cascade: child file parts inherit parent resource grants

## Key endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/storage/upload-sas` | Issue scoped upload SAS |
| POST | `/v1/storage/download-sas` | Issue scoped download SAS |
| GET | `/v1/storage/objects/{key}/metadata` | Object metadata |
| DELETE | `/v1/storage/objects/{key}` | Delete object (owner/manage) |

## SDK

No client SDK is published for this service yet. When one ships, it will live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks) and be documented on the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/).

## Implementation

See [`services/storage/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/storage).

## Full specification

[Architecture standard §8](../architecture/foundation-services-and-sharing-framework.md#8-ss-12--fileblob-storage-access-gated)
