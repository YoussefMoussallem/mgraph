# Universal Sharing Framework

**Status:** contract defined · implementation in progress

## Responsibility

Polymorphic grant model (`access_grant`) plus reusable Postgres RLS functions. Any object type becomes shareable with users and groups without bespoke authorization code.

## Core concepts

- **Principal:** user or group (from SS-02)
- **Grant:** `(resource_type, resource_id, principal, permission)`
- **Visibility:** owner-only, org, or explicit grants
- **RLS GUCs:** `app.current_user_id`, `app.current_group_ids`

## REST API (stable across platforms)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/shares` | Create grant |
| GET | `/v1/shares` | List grants on a resource |
| PATCH | `/v1/shares/{id}` | Update grant |
| DELETE | `/v1/shares/{id}` | Revoke grant |
| GET | `/v1/access/check` | Single access check |
| POST | `/v1/access/check:batch` | Batch access check |
| GET | `/v1/shared-with-me` | Resources shared with caller |
| PATCH | `/v1/resources/{type}/{id}/visibility` | Change visibility |

## SDK

No client SDK is published for this service yet. When one ships, it will live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks) and be documented on the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/).

## Implementation

See [`services/sharing/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/sharing).

## Full specification

[Architecture standard §5–6](../architecture/foundation-services-and-sharing-framework.md#5-the-universal-sharing-framework-the-core)
