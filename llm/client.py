"""OpenAI client wrapper with retry/backoff and JSON response mode.

Step 3 implementation only: transport wrapper behavior.
No prompt templates or domain prompting are included here.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
import time
from typing import Any, Protocol


DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF_S = 2.0
DEFAULT_MAX_BACKOFF_S = 30.0


class RateLimitPause(RuntimeError):
    """Raised when 429 retries are exhausted and the run should be paused."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry behavior for OpenAI API calls."""

    max_retries: int = DEFAULT_MAX_RETRIES
    base_backoff_s: float = DEFAULT_BASE_BACKOFF_S
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S
    jitter: bool = True


@dataclass(frozen=True, slots=True)
class OpenAIClientConfig:
    """Configuration for the OpenAI wrapper."""

    model: str
    timeout_s: float = 60.0
    retry_policy: RetryPolicy = RetryPolicy()


class _CompletionsProtocol(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _ChatProtocol(Protocol):
    completions: _CompletionsProtocol


class _OpenAIProtocol(Protocol):
    chat: _ChatProtocol


class OpenAIJsonClient:
    """Thin wrapper around `client.chat.completions.create`.

    Behavior:
    - Enforces JSON response mode.
    - Retries transient failures with exponential backoff + optional jitter.
    - Converts exhausted 429 retries into a `RateLimitPause` signal.
    """

    def __init__(self, client: _OpenAIProtocol, config: OpenAIClientConfig) -> None:
        self._client = client
        self._config = config

    def create_json_completion(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> Any:
        """Execute a JSON-mode chat completion request.

        `messages` are accepted as input wiring only; caller owns prompt content.
        """

        selected_model = model or self._config.model
        attempts = 0

        while True:
            try:
                return self._client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    timeout=self._config.timeout_s,
                )
            except Exception as exc:  # API exceptions differ across SDK versions
                attempts += 1

                if self._is_429(exc):
                    if attempts > self._config.retry_policy.max_retries:
                        raise RateLimitPause(
                            "Rate limit retries exhausted; pause run (rate_limit_pause)."
                        ) from exc
                    self._sleep_backoff(attempts)
                    continue

                if self._is_retryable_non_429(exc):
                    if attempts > self._config.retry_policy.max_retries:
                        raise
                    self._sleep_backoff(attempts)
                    continue

                raise

    def _sleep_backoff(self, attempts: int) -> None:
        delay = min(
            self._config.retry_policy.base_backoff_s * (2 ** (attempts - 1)),
            self._config.retry_policy.max_backoff_s,
        )
        if self._config.retry_policy.jitter:
            delay *= random.uniform(0.5, 1.5)
        time.sleep(delay)

    @staticmethod
    def _is_429(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True

        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True

        return exc.__class__.__name__ in {"RateLimitError"}

    @staticmethod
    def _is_retryable_non_429(exc: Exception) -> bool:
        name = exc.__class__.__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            return True

        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and status_code >= 500:
            return True

        response = getattr(exc, "response", None)
        response_code = getattr(response, "status_code", None)
        return isinstance(response_code, int) and response_code >= 500
