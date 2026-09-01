"""Custom exceptions for LLM provider errors.

Callers catch against this hierarchy instead of the OpenAI SDK's own
exception types so the rest of the codebase stays decoupled from the
underlying client. Every error carries the HTTP status so logs and
user-facing messages can differentiate transient issues (rate limits,
timeouts) from permanent ones (bad key, bad input).

Hierarchy::

    ProviderError
    +-- ProviderAuthError           (401/403 -- bad or missing API key)
    +-- ProviderRateLimitError      (429 -- quota exceeded)
    +-- ProviderNotFoundError       (404 -- invalid model name)
    +-- ProviderInvalidRequestError (400 -- malformed input)
    +-- ProviderConnectionError     (network / timeout)
    +-- ProviderServerError         (5xx -- provider-side failure)
"""


class ProviderError(Exception):
    """Base exception for all LLM provider errors.

    Holds the HTTP status so downstream handlers can decide whether to
    retry, surface the error to the user, or page oncall.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class ProviderAuthError(ProviderError):
    """Invalid or missing API key (401/403).

    Usually a config error — do not retry; alert operators.
    """


class ProviderRateLimitError(ProviderError):
    """Rate limit or quota exceeded (429).

    Typically safe to retry after backoff.
    """


class ProviderNotFoundError(ProviderError):
    """Model or resource not found (404).

    Usually the model name is wrong or has been retired — do not retry.
    """


class ProviderInvalidRequestError(ProviderError):
    """Bad request — malformed input or unsupported parameters (400).

    Retrying without changing the input will keep failing; surface to the
    caller so the request can be fixed.
    """


class ProviderConnectionError(ProviderError):
    """Network connectivity or timeout error.

    Transient by nature — retry with backoff is usually appropriate.
    """


class ProviderServerError(ProviderError):
    """Provider-side server error (5xx).

    Likely transient; retry with backoff, but alert if it persists.
    """


def classify_status_error(
    status_code: int,
    message: str,
) -> ProviderError:
    """Map an HTTP status code to the appropriate :class:`ProviderError` subclass.

    The adapter uses this to translate :class:`openai.APIStatusError` into
    our own hierarchy at the boundary, so application code never has to
    import openai exceptions or branch on integer status codes directly.
    """
    kwargs = {"status_code": status_code}
    if status_code in (401, 403):
        return ProviderAuthError(message, **kwargs)
    if status_code == 429:
        return ProviderRateLimitError(message, **kwargs)
    if status_code == 404:
        return ProviderNotFoundError(message, **kwargs)
    if status_code == 400:
        return ProviderInvalidRequestError(message, **kwargs)
    if status_code >= 500:
        return ProviderServerError(message, **kwargs)
    return ProviderError(message, **kwargs)
