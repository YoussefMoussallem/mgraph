# SS-01 — Identity & SSO

**Status:** contract defined · implementation in progress

## Responsibility

Single authentication and session authority. Wraps the IdP (Microsoft Entra), issues a **platform JWT** trusted by every service by signature only, and owns session lifecycle (refresh rotation and revocation).

## Platform JWT contract

```json
{
  "sub": "azure_oid-or-user-uuid",
  "app_id": "edwin",
  "email": "jane@corp.com",
  "name": "Jane Doe",
  "sid": "server-session-id",
  "type": "access",
  "iat": 1730000000,
  "exp": 1730003600
}
```

- **No `groups` claim.** Groups are resolved per-request from SS-02.
- Services trust `sub` as the stable principal id.

## Key endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/auth/login` | Redirect to IdP |
| GET | `/auth/callback` | OAuth callback, issue tokens |
| POST | `/auth/refresh` | Rotate access token |
| POST | `/auth/logout` | Revoke session |
| GET | `/.well-known/jwks.json` | JWKS for token verification |

## What every other service must do

1. Validate the platform JWT signature (RS256 / JWKS).
2. Extract `sub` — never trust client-supplied user ids.
3. Load group membership from SS-02 and stamp RLS GUCs before DB queries.

## SDK

No client SDK is published for this service yet. When one ships, it will live under [`sdks/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/sdks) and be documented on the [docs site](https://pwc-me-adv-strategyand.github.io/infra-platform-services/).

## Implementation

See [`services/identity/`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/tree/main/services/identity).

## Full specification

[Architecture standard §3](../architecture/foundation-services-and-sharing-framework.md#3-ss-01--identity--sso-service)
