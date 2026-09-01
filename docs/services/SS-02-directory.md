# SS-02 — User Directory

**Status:** contract defined · implementation in progress

## Responsibility

Canonical principal namespace: users (`azure_oid`) and groups (Entra group GUIDs). Resolves group membership per-request so JWTs stay small.

## Principal abstraction

Every consumer uses the same `Principal` type:

- `type`: `"user"` | `"group"`
- `id`: stable GUID / OID
- `displayName`: human-readable label

## Key endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/users/me` | Current user profile |
| GET | `/v1/users/{id}` | User lookup |
| GET | `/v1/groups/{id}` | Group metadata |
| GET | `/v1/users/me/groups` | Caller group membership |
| GET | `/v1/principals/search` | Search users and groups |

## Group sync

On login, SS-02 syncs the user's group graph into `user_group` (read-mirrored into app DBs for RLS).

## SDK

No client SDK is published for this service yet. When one ships, it will live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks) and be documented on the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/).

## Implementation

See [`services/directory/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/directory).

## Full specification

[Architecture standard §4](../architecture/foundation-services-and-sharing-framework.md#4-ss-02--user-directory-service)
