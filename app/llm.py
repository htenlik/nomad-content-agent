"""One call to an OpenAI-compatible chat-completions endpoint.

The README examples were generated with Gemini through its OpenAI-compatible
endpoint; any endpoint that speaks the same format works by changing
LLM_BASE_URL and LLM_MODEL in .env. Tests replace this class with a fake
that has the same `complete(system, user)` method.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-3.6-flash"
# Reasoning models spend hidden "thinking" tokens from this budget; a small
# limit produced empty captions in testing.
DEFAULT_MAX_TOKENS = 2048
TEMPERATURE = 0.4
MAX_RETRIES = 3  # free tiers answer with HTTP 429 a few times a minute
RETRY_DELAY_SECONDS = 15


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 max_tokens: int = DEFAULT_MAX_TOKENS, retry_delay: float = RETRY_DELAY_SECONDS):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.retry_delay = retry_delay

    @classmethod
    def from_env(cls) -> "LLM":
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise LLMError("LLM_API_KEY is not set. Copy .env.example to .env and fill in a key.")
        try:
            max_tokens = int(os.environ.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        except ValueError as exc:
            raise LLMError(f"LLM_MAX_TOKENS must be an integer: {exc}") from exc
        return cls(
            api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            max_tokens=max_tokens,
        )

    def complete(self, system: str, user: str) -> str:
        """Return the model's text. Retries a few times on 429/5xx, then gives up."""
        payload = {
            "model": self.model,
            "temperature": TEMPERATURE,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        for attempt in range(MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise LLMError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise LLMError(f"Could not reach {self.base_url}: {exc.reason}") from exc

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response from the LLM endpoint: {json.dumps(body)[:500]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(
                f"The model returned empty content (finish_reason={choice.get('finish_reason')!r}). "
                f"If it is a reasoning model, raise LLM_MAX_TOKENS (currently {self.max_tokens})."
            )
        return content
