"""Async, provider-agnostic LLM adapter built on the OpenAI Python SDK.

One module per endpoint the adapter drives — mirroring
:mod:`llm_provider.mappers`:

* :mod:`.core` — the :class:`LLMAdapter` facade: client construction, the
  ``stream()`` routing decision, ``complete()``, ``list_models()``.
* :mod:`.responses` — streaming + ``generate()`` over ``responses.create``.
* :mod:`.chat_completions` — streaming + ``generate_chat_completion()``
  over ``chat.completions.create`` (the caching path).
* :mod:`.images` — ``generate_image()`` over ``images.generate``.
* :mod:`.common` — shared policy (system-prompt normalisation,
  ``cache_control`` model families), usage extraction, and SDK-exception
  translation.

Import :class:`LLMAdapter` from here; the module layout is an internal
detail::

    from llm_provider.adapter import LLMAdapter
"""

from llm_provider.adapter.core import LLMAdapter

__all__ = ["LLMAdapter"]
