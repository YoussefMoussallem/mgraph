## What this PR does

<!-- One paragraph. What changed and why. -->

## Type of change

- [ ] New service
- [ ] New SDK
- [ ] API contract change
- [ ] Database migration / RLS change
- [ ] Documentation update only
- [ ] Bug fix
- [ ] Refactor (no behaviour change)

---

## Docs checklist

- [ ] The matching `docs/services/SS-XX-<name>.md` is created or updated in this PR
- [ ] The matching `services/<name>/README.md` is created or updated in this PR
- [ ] If an SDK is added or changed — `sdks/README.md` and `sdks/<lang>/<name>/README.md` are updated
- [ ] Root `README.md` service catalog and/or SDK section is updated (if a service or SDK was added)
- [ ] `docs/README.md` index is updated (if a new doc file was added)
- [ ] No `TODO` / `TBD` / placeholder text left without a linked issue

---

## Code quality checklist

### General

- [ ] All I/O is non-blocking (`async`/`await`) — no synchronous calls inside async handlers
- [ ] No secrets, tokens, or environment-specific values hardcoded in code
- [ ] No new dependency added without a note in the service or SDK README explaining why
- [ ] All function signatures are fully type-annotated

### Python (if applicable)

- [ ] Independent async calls use `asyncio.gather`, not sequential `await`
- [ ] Route handlers are thin — business logic is in a service layer
- [ ] RLS GUCs are stamped on every DB connection before any query
- [ ] Pydantic models used for all request and response bodies (no `dict` / `Any`)
- [ ] No bare `except:` — specific exception types caught and logged

### TypeScript (if applicable)

- [ ] No `any` — `unknown` with narrowing or a typed model used instead
- [ ] All exported types are declared and exported from the package
- [ ] Request timeout set — no open-ended network calls
- [ ] Typed errors thrown on non-2xx responses

### SQL / RLS (if applicable)

- [ ] Migration is idempotent (`IF NOT EXISTS`, `EXCEPTION WHEN duplicate_object`)
- [ ] New shareable table carries the resource envelope: `id`, `owner_id`, `resource_type`, `visibility`
- [ ] RLS policy uses `FORCE ROW LEVEL SECURITY` on the table
- [ ] App role is non-superuser with `NOBYPASSRLS`
- [ ] Child tables inherit parent grants via `EXISTS`, not a copied ACL column
- [ ] Rollback plan documented in the PR description for any destructive migration

---

## API contract checklist (if endpoints changed)

- [ ] Path uses a resource noun and correct HTTP verb
- [ ] Endpoint is versioned (`/v1/...`)
- [ ] List endpoint has pagination with a default page size ≤ 50
- [ ] Error responses follow `{ "code": "...", "detail": "..." }` envelope
- [ ] Breaking change? → new version created (`/v2/...`), old version not removed in this PR

---

## Reviewer notes

<!-- Anything the reviewer should know: migration order, feature flags, rollback steps, known limitations. -->
