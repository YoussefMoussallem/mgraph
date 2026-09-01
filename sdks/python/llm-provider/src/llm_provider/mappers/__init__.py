"""Translate our normalised schemas into provider wire formats.

One module per endpoint dialect:

* :mod:`.responses` — the Responses API (``responses.create``): ``input``
  items and flat tool definitions.
* :mod:`.chat_completions` — Chat Completions (``chat.completions.create``):
  ``messages`` (including the system message) and nested tool definitions.
  Only this dialect carries ``cache_control`` breakpoints — it's the one
  path where LiteLLM forwards them to the backend.

Both dialects own a ``build_tools``, so import the modules, not the names::

    from llm_provider.mappers import chat_completions, responses

    responses.build_input(request.messages)
    chat_completions.build_messages(request.messages, cache_ttl="5m")

Kept separate from the adapter so the wire surface — and any quirks
introduced by SDK upgrades — lives in one place. If we ever swap providers,
this package is where most of the change happens.
"""

from llm_provider.mappers import chat_completions, responses

__all__ = ["chat_completions", "responses"]
