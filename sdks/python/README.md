# Python SDKs

Installable Python packages for platform apps (Edwin, Agent Studio, etc.). One directory per package under this folder.

## Packages

| Directory | Documentation |
| --- | --- |
| [`langfuse-client/`](langfuse-client/README.md) | Langfuse tracing helpers |
| [`llm-provider/`](llm-provider/README.md) | LLM adapter (OpenAI-compatible APIs) |
| [`m365/m365-client/`](m365/m365-client/README.md) | Microsoft 365 auth (on-behalf-of + app-only) and Graph client foundation |
| [`m365/outlook-client/`](m365/outlook-client/README.md) | Outlook mail, calendar and contacts, on `m365-client` |
| [`m365/sharepoint-client/`](m365/sharepoint-client/README.md) | SharePoint sites, libraries, files and lists, on `m365-client` |
| [`m365/langchain-tools/`](m365/langchain-tools/README.md) | Outlook + SharePoint as LangChain agent tools, on the workload SDKs |

The three Microsoft 365 packages are grouped under [`m365/`](m365/README.md); the GenAI packages sit directly under this folder.

## Local development

From the repository root:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
pip install -e ./sdks/python/m365/m365-client -e ./sdks/python/m365/outlook-client -e ./sdks/python/m365/sharepoint-client -e ./sdks/python/m365/langchain-tools
pytest sdks/python/langfuse-client/tests sdks/python/llm-provider/tests -q
```

## Using the SDKs in your application

Install published wheels from **JFrog Artifactory PyPI** (not editable paths from this repo):

- **Guide:** [docs/installation.md](docs/installation.md) (MkDocs) — `pip` / `requirements.txt` / Docker / CI
- **Template:** [pip.jfrog.example](pip.jfrog.example)

Quick example:

```bash
pip install "llm-provider==0.4.0" \
  --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
  --extra-index-url "https://pypi.org/simple"
```

Runtime setup (env vars, Langfuse, Docker): [docs/deployment.md](docs/deployment.md).

## Documentation

Full guide (install, streaming, caching, Langfuse, deployment): build locally from `sdks/python/` with `mkdocs serve`, or read the published site: <https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/> (deployed by `docs-site.yml` on merge to `main`).

### Auto version bumps (release-please)

On every push to **`main`**, [release-please](https://github.com/googleapis/release-please) opens or updates a **release PR** when SDK paths change. Use **conventional commits** (`feat:`, `fix:`, …) on `main`.

Merging the release PR bumps **TypeScript `package.json` and Python `pyproject.toml`** together (same component version) and creates tags like **`langfuse-client-v0.5.1`**, which trigger PyPI + npm publish workflows.

Config: [`release-please-config.json`](../../release-please-config.json), workflow [`.github/workflows/release-please.yml`](../../.github/workflows/release-please.yml).

## CI & release

| Workflow | Purpose |
| --- | --- |
| [`python-sdks-ci.yml`](../../.github/workflows/python-sdks-ci.yml) | Lint + tests on `sdks/python/` |
| [`python-sdks-release.yml`](../../.github/workflows/python-sdks-release.yml) | Tag → GitHub Release + Artifactory PyPI (`jfrog` env: `JFROG_PYPI_URL`, …) |
| [`docs-site.yml`](../../.github/workflows/docs-site.yml) | MkDocs → GitHub Pages (platform docs + Python + TypeScript sub-sites) |

### GitHub environment `jfrog`

| Secret | Purpose |
|--------|---------|
| `JFROG_USERNAME` | PyPI publish (`twine`) |
| `JFROG_TOKEN` | PyPI + npm publish |
| `JFROG_PYPI_URL` | Twine repo URL (no `/simple`, no creds in URL) |
| `JFROG_NPM_URL` | npm registry URL (TypeScript releases) |
