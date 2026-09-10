"""The HTTP client, exercised against a local stand-in for a chat-completions endpoint."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.llm import MAX_RETRIES, LLMConfig, LLMError, OpenAICompatibleClient


class StubEndpoint:
    """Serves /chat/completions; records the request; returns a scripted body/status."""

    def __init__(self, status=200, body=None, responses=None):
        default_body = body if body is not None else {"choices": [{"message": {"content": "hello"}}]}
        # A list of (status, body) served in order; the last one repeats.
        self.responses = list(responses) if responses else [(status, default_body)]
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                stub.requests.append(json.loads(self.rfile.read(length)))
                stub.headers.append(dict(self.headers))
                status, body = stub.responses[min(len(stub.requests) - 1, len(stub.responses) - 1)]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *_):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"


class Config(unittest.TestCase):
    def test_missing_key_is_a_config_error(self):
        with self.assertRaises(LLMError):
            LLMConfig.from_env({})

    def test_env_values_are_read_and_defaults_applied(self):
        config = LLMConfig.from_env({"LLM_API_KEY": "k", "LLM_BASE_URL": "http://x/v1/", "LLM_MAX_TOKENS": "512"})
        self.assertEqual(config.base_url, "http://x/v1")
        self.assertEqual(config.max_tokens, 512)
        self.assertEqual(config.model, "gemini-3.6-flash")

    def test_bad_numeric_setting(self):
        with self.assertRaises(LLMError):
            LLMConfig.from_env({"LLM_API_KEY": "k", "LLM_MAX_TOKENS": "lots"})


class Client(unittest.TestCase):
    def test_sends_system_and_user_messages_and_returns_content(self):
        with StubEndpoint() as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="secret", base_url=stub.base_url, model="m"))
            self.assertEqual(client.complete("SYS", "USER"), "hello")
        request = stub.requests[0]
        self.assertEqual(request["model"], "m")
        self.assertEqual(request["messages"], [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}])
        self.assertEqual(stub.headers[0]["Authorization"], "Bearer secret")

    def test_http_error_is_reported_with_status(self):
        with StubEndpoint(status=401, body={"error": "bad key"}) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url))
            with self.assertRaises(LLMError) as ctx:
                client.complete("s", "u")
        self.assertIn("401", str(ctx.exception))

    def test_rate_limit_is_retried_then_succeeds(self):
        ok = {"choices": [{"message": {"content": "hello"}}]}
        with StubEndpoint(responses=[(429, {"error": "slow down"}), (503, {"error": "busy"}), (200, ok)]) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url, retry_delay_seconds=0))
            self.assertEqual(client.complete("s", "u"), "hello")
        self.assertEqual(len(stub.requests), 3)

    def test_rate_limit_gives_up_after_max_retries(self):
        with StubEndpoint(status=429, body={"error": "slow down"}) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url, retry_delay_seconds=0))
            with self.assertRaises(LLMError) as ctx:
                client.complete("s", "u")
        self.assertEqual(len(stub.requests), MAX_RETRIES + 1)
        self.assertIn("429", str(ctx.exception))

    def test_client_errors_are_not_retried(self):
        with StubEndpoint(status=400, body={"error": "bad request"}) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url, retry_delay_seconds=0))
            with self.assertRaises(LLMError):
                client.complete("s", "u")
        self.assertEqual(len(stub.requests), 1)

    def test_empty_content_is_a_clear_error(self):
        body = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        with StubEndpoint(body=body) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url))
            with self.assertRaises(LLMError) as ctx:
                client.complete("s", "u")
        self.assertIn("LLM_MAX_TOKENS", str(ctx.exception))
        self.assertIn("length", str(ctx.exception))

    def test_unexpected_shape_is_an_error(self):
        with StubEndpoint(body={"unexpected": True}) as stub:
            client = OpenAICompatibleClient(LLMConfig(api_key="x", base_url=stub.base_url))
            with self.assertRaises(LLMError):
                client.complete("s", "u")
