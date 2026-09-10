"""Minimal LLM client: one POST to an OpenAI-compatible chat-completions endpoint.

Gemini (used for the README examples), OpenAI, Groq and others serve this
format, so the provider is just a base URL and a model name in `.env`.
The rest of the application only relies on `complete(system, user)`, so
tests substitute a fake object with the same method.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-3.6-flash"
# Reasoning models spend hidden "thinking" tokens from this budget; a small
# limit produced empty captions in testing.
DEFAULT_MAX_TOKENS = 2048
TEMPERATURE = 0.4
TIMEOUT_SECONDS = 60
# Free tiers answer with 429 a few times a minute; a short bounded retry
# is enough to get through a handful of example runs.
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """The model call failed or is not configured."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class TextCompleter(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    retry_delay_seconds: float = RETRY_DELAY_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LLMConfig":
        env = os.environ if env is None else env
        api_key = env.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise LLMError("LLM_API_KEY is not set. Copy .env.example to .env and fill in a key.")
        try:
            max_tokens = int(env.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        except ValueError as exc:
            raise LLMError(f"LLM_MAX_TOKENS must be an integer: {exc}") from exc
        return cls(
            api_key=api_key,
            base_url=env.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=env.get("LLM_MODEL", DEFAULT_MODEL),
            max_tokens=max_tokens,
        )


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.config.model,
            "temperature": TEMPERATURE,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._post(payload)
            except LLMError as exc:
                if not exc.retryable:
                    raise
                if attempt == MAX_RETRIES:
                    raise LLMError(f"{exc} (gave up after {attempt + 1} tries)") from exc
                time.sleep(self.config.retry_delay_seconds * (attempt + 1))
        raise AssertionError("unreachable")

    def _post(self, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(
                f"LLM request failed with HTTP {exc.code} from {self.config.base_url}: {detail}",
                retryable=exc.code in RETRYABLE_HTTP_CODES,
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach {self.config.base_url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM endpoint returned non-JSON body: {exc}") from exc

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from LLM endpoint: {json.dumps(body)[:500]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(
                f"LLM returned empty content (finish_reason={choice.get('finish_reason')!r}). "
                f"A reasoning model may have spent the whole token budget on thinking; "
                f"raise LLM_MAX_TOKENS (currently {self.config.max_tokens})."
            )
        return content
