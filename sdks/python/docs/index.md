# AI Labs Python SDKs

Shared foundations for Python applications: a provider-agnostic LLM adapter,
Langfuse tracing helpers, a Microsoft 365 Graph foundation, and typed Outlook
and SharePoint clients on top of it — each extracted from production use and
hardened for standalone consumption.

## Packages

| Package | Import | What it gives you |
|---|---|---|
| **llm-provider** | `llm_provider` | Async LLM client built on the OpenAI Python SDK: streaming over the Responses *and* Chat Completions APIs, tool use, vision, image generation, typed prompt caching, normalized usage accounting, and a provider-agnostic exception hierarchy. |
| **langfuse-client** | `langfuse_client` | Langfuse initialisation, lifecycle, and tracing helpers. Tracing is a silent no-op until initialised, so apps run identically with or without observability credentials. |
| **m365-client** | `m365_client` | Microsoft 365 authentication (on-behalf-of and app-only), bounded credential caching, a configured and retry-hardened Graph client, and a typed error taxonomy. Ships **no** workload code — that lives in the two packages below. |
| **outlook-client** | `outlook_client` | Outlook on `m365-client`: mail (read, search, send, reply, forward, drafts, attachments, move, delete), calendar, contacts and the signed-in user's profile, paged and mapped into typed models. |
| **sharepoint-client** | `sharepoint_client` | SharePoint on `m365-client`: sites, document libraries, folders, files (list, search, read, upload, move, delete) and lists. Delegated only by design. |
| **m365-langchain-tools** | `m365_langchain_tools` | The two workloads as 24 LangChain agent tools (13 reads, 11 opt-out writes) for LLM hosts, with identity bound by the host — never by the model. |

The packages are independently installable — apps that only want tracing
never pull in the LLM adapter, and the Microsoft 365 packages share nothing
with the GenAI packages (see [Installation](installation.md)).

## Design in one paragraph

Application code never touches OpenAI SDK types. Callers build a
[`ChatRequest`](llm-provider/index.md) from normalized `Message` objects,
optionally structure the system prompt as
[`SystemBlock`s](llm-provider/caching.md) to enable prompt caching, and
consume a small, stable set of
[stream events](llm-provider/streaming.md). The adapter decides which wire
API each request takes, translates provider errors into a
[typed hierarchy](llm-provider/errors.md), and wraps every call in a Langfuse
observation when [tracing is on](langfuse-client/index.md). It is designed
for (and production-tested behind) a LiteLLM proxy fronting Bedrock Claude,
Azure OpenAI, and Gemini/Vertex — but works against any OpenAI-compatible
endpoint.

## Where to start

- [Installation](installation.md)
- [llm-provider getting started](llm-provider/index.md)
- [langfuse-client getting started](langfuse-client/index.md)
- [m365-client getting started](m365-client/index.md)
- [outlook-client](outlook-client/index.md) and [sharepoint-client](sharepoint-client/index.md)
- [m365-langchain-tools](m365-langchain-tools/index.md) — the workloads as agent tools
- [Deployment guide](deployment.md) — local Docker vs hosted, certificates, env-driven config
