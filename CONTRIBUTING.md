# Contributing guidelines

Rules for humans and AI agents contributing to this repo. Read before opening a PR.

---

## 1. Repo structure rules

| Area | Rule |
| --- | --- |
| `docs/` | Architecture standards, per-service reference, and docs-site source pages only. No runtime code. |
| `services/` | One independently deployable microservice per folder. No shared runtime code between service folders. |
| `sdks/` | Client libraries and their docs only. One folder per SDK under the language directory, grouped under a family folder when several packages share one foundation (`sdks/python/m365/`). |
| `scaffolds/` | Application starter templates, copied (not installed) to begin a new app. One folder per scaffold. |
| Root `README.md` | Summary and entry point. Must be updated when any service, SDK, or scaffold is added. |

**Convention: if it is not documented in the right README, it is not done.**

---

## 2. Docs rules (apply everywhere)

- Every folder must have a `README.md`. No exceptions.
- Every service must have a matching doc under `docs/services/`.
- When you change an API contract, endpoint signature, or data model — update the `docs/services/` doc in the same PR, not a follow-up.
- When you change architecture principles — update `docs/architecture/` first, then align the affected service docs.
- Write docs for the next engineer, not for yourself. Assume they have not read the architecture doc.
- Do not leave placeholder text (`TODO`, `TBD`, `coming soon`) without a linked issue or PR.
- The docs site is built with **strict MkDocs** (`mkdocs.yml` at the repo root, plus `sdks/*/mkdocs.yml`) and deployed by `.github/workflows/docs-site.yml` on every merge to `main`. On published pages (everything under `docs/`, the scaffold READMEs, and this file), links to repo files **outside** the docs tree must be full repo URLs (`https://github.com/pwc-me-adv-strategyand/infra-platform-services/...`) — strict mode fails the build on relative links that leave the tree.
- The Scaffolds and Contributing pages on the docs site are single-sourced: `docs/scaffolds/*.md` and `docs/contributing.md` include the scaffold READMEs and `CONTRIBUTING.md` at build time. Edit the source file, never a copy.

---

## 3. Python service rules

Applies to all code under `services/*/`.

### Async

- **All I/O must be non-blocking.** Use `async def` and `await` for every database call, HTTP call, cache read, and file operation.
- Never use synchronous I/O inside an async route or task. `requests`, `psycopg2` (synchronous), `open()` on large files — none of these inside async handlers.
- Use `asyncio.gather` for concurrent independent calls. Do not await them sequentially when they can run in parallel.
- Background work (ingestion pipelines, retries) goes on a durable queue (not `asyncio.create_task` on a fire-and-forget call).

```python
# correct
results = await asyncio.gather(
    load_user(user_id),
    load_groups(user_id),
)

# wrong — sequential waits on independent calls
user = await load_user(user_id)
groups = await load_groups(user_id)
```

### FastAPI patterns

- Every route has a Pydantic request and response model — no `dict` or `Any` in signatures.
- Dependency injection for auth, DB session, and sharing client — never instantiate these inside route handlers.
- Stamp RLS GUCs before any DB query in every route. This is not optional.
- Return `404` before `403` when a resource does not exist to the caller (do not confirm existence to unauthorised callers).

### Error handling

- Raise `HTTPException` with a specific status code and a human-readable `detail` string.
- Never let an unhandled exception reach the client. Add a global exception handler for unexpected errors that returns `500` without leaking stack traces.
- Log the full exception with context at `ERROR` level before returning the response.

### Code style

- Type-annotate every function signature (arguments and return type).
- No mutable default arguments.
- No bare `except:` — catch specific exception types.
- Keep route handlers thin: validate input, call a service function, return output. Business logic lives in a service layer, not in the route.

---

## 4. TypeScript SDK rules

Applies to all code under `sdks/typescript/`.

### Async

- All methods that perform I/O must return `Promise<T>` and be `async`.
- Use `Promise.all` for concurrent independent operations.
- Always set a request timeout. Do not make open-ended network calls.
- Cancel in-flight requests via `AbortController` when appropriate (e.g. component unmount, pagination cancellation).

### Typing

- No `any`. Use `unknown` and narrow it, or model the type correctly.
- Export all types that callers need. Do not force consumers to re-declare types.
- Mark optional fields explicitly with `?`. Do not use `T | undefined` where `?` is cleaner.

### Module structure

- One file per logical concept. Do not create monolithic `index.ts` files that export everything.
- Export barrel (`index.ts`) only at the package root and at sub-package roots. Keep internals unexported.
- Keep side effects out of module scope. Do not run code on import.

### Error handling

- Throw typed errors, not plain strings.
- On non-2xx HTTP responses, include the status code and a message in the thrown error.
- Do not swallow errors silently with empty `catch` blocks.

---

## 5. SQL and RLS rules

Applies to any migration or RLS policy added under `services/*/`.

- Every migration must be idempotent (`CREATE TABLE IF NOT EXISTS`, `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object ...`).
- Never alter an existing column type or drop a column in a migration without a documented rollback plan.
- Every new shareable table must carry the resource envelope: `id`, `owner_id`, `resource_type`, `visibility`.
- RLS policies must use `FORCE ROW LEVEL SECURITY` on the table and run under a non-superuser role with `NOBYPASSRLS`. No exceptions.
- Never place authorization logic in application `WHERE` clauses as the only gate. RLS is the hard backstop — application checks are the friendly first gate.
- GUCs (`app.current_user_id`, `app.current_group_ids`) must be stamped on every connection before any query. Validate they are non-empty at the start of each request.
- Child tables must inherit parent grants via an `EXISTS` sub-select in RLS, never via a copied ACL column.

---

## 6. API design rules

Applies to all REST APIs across `services/*/`.

- Use resource nouns in paths, HTTP verbs for actions: `POST /v1/knowledge-bases`, not `POST /v1/create-knowledge-base`.
- Version all endpoints from day one: `/v1/...`.
- Return `201` on resource creation with the created object in the body.
- Return `204` on deletion with no body.
- Return `202` when an action is accepted but not yet complete (e.g. async ingestion).
- Pagination is mandatory on all list endpoints. Default page size ≤ 50. Use cursor-based pagination for large result sets.
- Errors follow a consistent envelope: `{ "code": "string", "detail": "human message" }`.
- Breaking changes to existing endpoints require a new version (`/v2/...`), not an in-place change.

---

## 7. Rules for AI agents adding code

If you are an AI agent contributing to this repo, follow these rules before writing a single line of code.

### Before you start

1. Read the relevant `docs/services/` file for the service you are changing.
2. Read the service's `services/<name>/README.md`.
3. Read the root `CONTRIBUTING.md` (this file) fully.
4. Confirm which section of the repo your change belongs to before writing anything.

### What you must do

- Follow every rule in sections 3–6 that applies to the language you are writing.
- Update the matching `README.md` and `docs/services/` file in the same change — never defer docs to a follow-up.
- If you add a new service, update the **Service catalog** in the root `README.md`.
- If you add a new SDK, update the **SDKs** section in the root `README.md` and `sdks/README.md`.
- If you add a new scaffold, update the **Scaffolds** section in the root `README.md` and the catalog in `scaffolds/README.md`.
- Write async-first for all I/O — no blocking calls inside async handlers.
- Never copy-paste from one service to another without first extracting the shared logic into the SDK.

### What you must not do

- Do not add code without a corresponding doc update in the same change.
- Do not place authorization logic only in application code — RLS must be present.
- Do not use synchronous I/O inside async handlers.
- Do not leave `TODO` comments without linking them to a tracked issue.
- Do not hardcode secrets, tokens, or environment-specific values in code.
- Do not add dependencies without documenting why in the service or SDK README.
- Do not create new tables or change schemas without a migration file.
- Do not break the existing API contract without bumping the version.
