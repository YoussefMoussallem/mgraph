# genai-sdk — TypeScript

Shared GenAI foundation for TypeScript/Node applications: a provider-agnostic
LLM adapter and Langfuse tracing helpers. The TypeScript twin of
the sibling [Python SDK](https://pwc-me-adv-strategyand.github.io/infra-platform-services/python/) —
same behavioral contracts, idiomatic TypeScript (see
[PARITY.md](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/PARITY.md) for what is guaranteed identical and
[DESIGN.md](https://github.com/pwc-me-adv-strategyand/infra-platform-services/blob/main/sdks/typescript/DESIGN.md) for architecture notes).

## Packages

| Package | Import | What it gives you |
|---|---|---|
| **llm-provider** | `@genai-sdk/llm-provider` | Async LLM client built on the OpenAI Node SDK: streaming over the Responses *and* Chat Completions APIs, tool use, vision, image generation, typed prompt caching, normalized usage accounting, and a provider-agnostic error hierarchy. |
| **langfuse-client** | `@genai-sdk/langfuse-client` | Langfuse initialisation, lifecycle, and tracing helpers. Tracing is a silent no-op until initialised, so apps run identically with or without observability credentials. |

The packages are independently installable — apps that only want tracing
never pull in the LLM adapter (see [Installation](installation.md)).

## Design in one paragraph

Application code never touches OpenAI SDK types. Callers build a
[`ChatRequest`](llm-provider/index.md) from normalized `Message` objects,
optionally structure the system prompt as
[`SystemBlock`s](llm-provider/caching.md) to enable prompt caching, and
consume a small, stable set of
[stream events](llm-provider/streaming.md) via `for await`. The adapter
decides which wire API each request takes, translates provider errors into a
[typed hierarchy](llm-provider/errors.md), and wraps every call in a Langfuse
observation when [tracing is on](langfuse-client/index.md). It is designed
for (and the Python SDK is production-tested behind) a LiteLLM proxy fronting
Bedrock Claude, Azure OpenAI, and Gemini/Vertex — but works against any
OpenAI-compatible endpoint.

## Where to start

- [Using the SDK in your application](using-in-an-app.md) — **start here for app teams**
- [Installation](installation.md)
- [Test application (Docker / local harness)](app-testing.md)
- [llm-provider getting started](llm-provider/index.md)
- [langfuse-client getting started](langfuse-client/index.md)
- [Corporate proxy / private CA](langfuse-client/corporate-network.md) — the Node trust story
