"""Minimal LLM client.

One code path talks to any OpenAI-compatible chat-completions endpoint
(OpenAI, Anthropic's compatibility endpoint, Gemini's, Groq, Ollama, ...),
configured entirely through environment variables. This keeps the
dependency list empty and the provider swappable without touching code.

The rest of the application only relies on the `complete(system, user)`
method, so tests substitute a fake object with the same method.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.4
# Generous on purpose: reasoning models count their hidden thinking against
# this limit, and a caption that comes back empty is worse than a few
# unused tokens.
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 10.0
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


class LLMError(Exception):
    """The model call failed (network, HTTP error, unexpected response shape)."""


class LLMConfigError(LLMError):
    """The client is not configured (typically a missing API key)."""


class TextCompleter(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        api_key = env.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise LLMConfigError(
                "LLM_API_KEY is not set. Copy .env.example to .env and fill in a key, "
                "or export LLM_API_KEY in your shell."
            )
        try:
            return cls(
                api_key=api_key,
                base_url=env.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
                model=env.get("LLM_MODEL", DEFAULT_MODEL),
                temperature=float(env.get("LLM_TEMPERATURE", DEFAULT_TEMPERATURE)),
                max_tokens=int(env.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
                timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
                max_retries=int(env.get("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
            )
        except ValueError as exc:
            raise LLMConfigError(f"Invalid numeric LLM setting in environment: {exc}") from exc


class OpenAICompatibleClient:
    """POST /chat/completions and return the assistant's text.

    Rate-limit (429) and server (5xx) responses are retried a few times with
    a growing delay, honouring a Retry-After header when the server sends
    one. Free tiers make this necessary rather than nice-to-have.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._post(payload)
            except _RetryableHTTPError as exc:
                if attempt == self.config.max_retries:
                    raise LLMError(f"{exc} (gave up after {attempt + 1} tries)") from exc
                delay = exc.retry_after or self.config.retry_base_delay_seconds * (2**attempt)
                time.sleep(min(delay, 60.0))
        raise AssertionError("unreachable")

    def _post(self, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            message = f"LLM request failed with HTTP {exc.code} from {self.config.base_url}: {detail}"
            if exc.code in RETRYABLE_HTTP_CODES:
                raise _RetryableHTTPError(message, _retry_after_seconds(exc)) from exc
            raise LLMError(message) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach {self.config.base_url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM endpoint returned non-JSON body: {exc}") from exc

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from LLM endpoint: {json.dumps(body)[:500]}") from exc
        if content is None or not isinstance(content, str) or not content.strip():
            finish = choice.get("finish_reason") if isinstance(choice, dict) else None
            raise LLMError(
                f"LLM returned empty content (finish_reason={finish!r}). If this is a reasoning model, "
                f"its thinking may have used the whole token budget: raise LLM_MAX_TOKENS "
                f"(currently {self.config.max_tokens})."
            )
        return content


class _RetryableHTTPError(Exception):
    def __init__(self, message: str, retry_after: float | None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None
