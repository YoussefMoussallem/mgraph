# Installation

Requires **Python >= 3.11**.

| Package (PyPI name) | Import module | Typical use |
| --- | --- | --- |
| `langfuse-client` | `langfuse_client` | Langfuse tracing only |
| `llm-provider` | `llm_provider` | LLM adapter (depends on `langfuse-client`) |
| `m365-client` | `m365_client` | Microsoft 365 auth + Graph client (independent of the GenAI packages) |
| `outlook-client` | `outlook_client` | Outlook mail, calendar and contacts (depends on `m365-client`) |
| `sharepoint-client` | `sharepoint_client` | SharePoint sites, files and lists (depends on `m365-client`) |
| `m365-langchain-tools` | `m365_langchain_tools` | Outlook + SharePoint as LangChain agent tools (depends on the two workload SDKs) |

Published wheels live on the org **JFrog Artifactory PyPI** feed after release tags
(`langfuse-client-v*` / `llm-provider-v*` / `m365-client-v*` / `outlook-client-v*` / `sharepoint-client-v*` / `m365-langchain-tools-v*`) are built by
[`.github/workflows/python-sdks-release.yml`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/.github/workflows/python-sdks-release.yml).

---

## Install from JFrog in an application (recommended)

### 1. Get access

You need:

- **Index URL** (for `pip`):  
  `https://artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple`  
  (must include **`/simple`** — this is not the same URL as CI uses for `twine upload`.)
- **Credentials**: Artifactory username + identity token (same pattern as npm: `JFROG_USERNAME`, `JFROG_TOKEN`).

Ask DevSecOps / platform if your feed name differs. See also [`pip.jfrog.example`](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/python/pip.jfrog.example) in the repository.

### 2. One-off install (local shell)

Use the private index for SDK packages and **PyPI** for public dependencies (`openai`, `pydantic`, etc.):

```bash
export JFROG_USERNAME="your-user"
export JFROG_TOKEN="your-token"

pip install "langfuse-client==0.5.0" "llm-provider==0.4.0" \
  --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
  --extra-index-url "https://pypi.org/simple"
```

**LLM apps** usually need only `llm-provider` (it pulls `langfuse-client` automatically):

```bash
pip install "llm-provider==0.4.0" \
  --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
  --extra-index-url "https://pypi.org/simple"
```

**Tracing-only apps** install `langfuse-client` alone (no `openai` dependency).

Pin versions to match what your team has released; check Artifactory or GitHub releases for the latest.

### 3. `requirements.txt`

Put index options at the top, then pinned packages:

```text
--index-url https://artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple
--extra-index-url https://pypi.org/simple

llm-provider==0.4.0
```

Install with credentials in the environment (do not commit tokens into the file):

```bash
export PIP_INDEX_URL="https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple"
export PIP_EXTRA_INDEX_URL="https://pypi.org/simple"
pip install -r requirements.txt
```

Alternatively, use a single `requirements.txt` and pass `--index-url` / `--extra-index-url` on the `pip install` command line.

### 4. `pyproject.toml` (Poetry / pip / uv)

Declare normal dependencies; configure the index in tool settings or at install time.

**`pyproject.toml` dependencies:**

```toml
[project]
requires-python = ">=3.11"
dependencies = [
    "llm-provider==0.4.0",
]
```

**pip** (same as above):

```bash
pip install . \
  --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
  --extra-index-url "https://pypi.org/simple"
```

**uv** (example — set index URL and auth via env or `uv.toml` per your org standard):

```bash
export UV_INDEX_URL="https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple"
export UV_EXTRA_INDEX_URL="https://pypi.org/simple"
uv sync
```

### 5. Docker

Pass credentials as **build args** or **secrets** (not baked into image layers you push to a shared registry unless your policy allows it):

```dockerfile
ARG JFROG_USERNAME
ARG JFROG_TOKEN
ARG SDK_VERSION=0.4.0

RUN pip install --no-cache-dir "llm-provider==${SDK_VERSION}" \
  --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
  --extra-index-url "https://pypi.org/simple"
```

Build:

```bash
docker build \
  --build-arg JFROG_USERNAME="$JFROG_USERNAME" \
  --build-arg JFROG_TOKEN="$JFROG_TOKEN" \
  -t my-app .
```

If the build host cannot resolve internal hostnames, fix DNS/VPN the same way as for your LLM proxy (corporate network).

### 6. GitHub Actions (application repo)

Store `JFROG_USERNAME`, `JFROG_TOKEN`, and optionally the index URL in a GitHub **environment** or **secrets** (mirror the SDK repo’s `jfrog` environment pattern):

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"

- name: Install GenAI SDKs from Artifactory
  env:
    JFROG_USERNAME: ${{ secrets.JFROG_USERNAME }}
    JFROG_TOKEN: ${{ secrets.JFROG_TOKEN }}
  run: |
    pip install "llm-provider==0.4.0" \
      --index-url "https://${JFROG_USERNAME}:${JFROG_TOKEN}@artifacts-central.pwc.com/artifactory/api/pypi/pypidev-d4j0u-4qu-pyp-loc/simple" \
      --extra-index-url "https://pypi.org/simple"
```

### 7. Minimal app usage

After install, imports match the package layout (see [Deployment guide](deployment.md)):

```python
from llm_provider import LLMAdapter, ChatRequest, Message
from langfuse_client import init_client  # optional tracing
```

---

## Other install methods (SDK development)

### Editable install from a clone

From the **infra-platform-services** repository root — order matters because `llm-provider` depends on `langfuse-client`:

```bash
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
```

The Microsoft 365 packages share no dependencies with the GenAI packages. `outlook-client` and `sharepoint-client` depend on `m365-client`, so install it in the same command:

```bash
pip install -e ./sdks/python/m365/m365-client \
  -e ./sdks/python/m365/outlook-client -e ./sdks/python/m365/sharepoint-client \
  -e ./sdks/python/m365/langchain-tools
```

### Install from GitHub (no Artifactory)

Use when you cannot reach the feed yet. Put **both** packages in **one** `pip install` so pip does not look for `langfuse-client` on public PyPI:

```bash
pip install \
  "langfuse-client @ git+https://github.com/pwc-me-adv-strategyand/infra-platform-services.git#subdirectory=sdks/python/langfuse-client" \
  "llm-provider @ git+https://github.com/pwc-me-adv-strategyand/infra-platform-services.git#subdirectory=sdks/python/llm-provider"
```

---

## Development & docs

```bash
pip install -e ./sdks/python/langfuse-client -e "./sdks/python/llm-provider[dev]"
pytest sdks/python/langfuse-client/tests sdks/python/llm-provider/tests -q
```

To work on this documentation (from `sdks/python/`):

```bash
cd sdks/python
pip install -r docs/requirements.txt
mkdocs serve   # http://127.0.0.1:8000
```
