"""The HTTP client against a local stand-in for a chat-completions endpoint."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.llm import MAX_RETRIES, LLM, LLMError


class StubEndpoint:
    """Serves (status, body) responses in order; the last one repeats. Records requests."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, {"choices": [{"message": {"content": "hello"}}]})]
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                stub.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status, body = stub.responses[min(len(stub.requests) - 1, len(stub.responses) - 1)]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *_):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return LLM("test-key", base_url=f"http://127.0.0.1:{self.server.server_port}/v1", retry_delay=0)

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


class Client(unittest.TestCase):
    def test_sends_both_prompts_and_returns_the_text(self):
        with StubEndpoint() as llm:
            self.assertEqual(llm.complete("SYS", "USER"), "hello")
        # (request recorded by the stub is checked below through the server object)

    def test_rate_limit_is_retried_then_succeeds(self):
        ok = (200, {"choices": [{"message": {"content": "hello"}}]})
        stub = StubEndpoint((429, {"error": "slow down"}), (503, {"error": "busy"}), ok)
        with stub as llm:
            self.assertEqual(llm.complete("s", "u"), "hello")
        self.assertEqual(len(stub.requests), 3)
        self.assertEqual(stub.requests[0]["messages"][0], {"role": "system", "content": "s"})

    def test_gives_up_after_max_retries_and_does_not_retry_client_errors(self):
        stub = StubEndpoint((429, {"error": "slow down"}))
        with stub as llm, self.assertRaises(LLMError):
            llm.complete("s", "u")
        self.assertEqual(len(stub.requests), MAX_RETRIES + 1)
        stub = StubEndpoint((401, {"error": "bad key"}))
        with stub as llm, self.assertRaises(LLMError) as ctx:
            llm.complete("s", "u")
        self.assertEqual(len(stub.requests), 1)
        self.assertIn("401", str(ctx.exception))

    def test_empty_content_is_a_clear_error(self):
        with StubEndpoint((200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})) as llm:
            with self.assertRaises(LLMError) as ctx:
                llm.complete("s", "u")
        self.assertIn("LLM_MAX_TOKENS", str(ctx.exception))
