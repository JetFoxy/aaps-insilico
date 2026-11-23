"""Local HTTP stub for Nightscout API tests.

Each recorded request is `(path, params)` where `params` holds the parsed query
string values plus lower-cased request headers, so tests can assert on both.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True


class StubNightscout:

    def __init__(self, routes: dict[str, Callable[[dict], tuple[int, object]]]):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []
        self._server = _StubServer(("127.0.0.1", 0), self._make_handler())
        port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{port}"
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class Handler(BaseHTTPRequestHandler):

            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
                recorded = dict(query)
                for name, value in self.headers.items():
                    recorded.setdefault(name.lower(), value)
                stub.requests.append((parsed.path, recorded))
                route = stub.routes.get(parsed.path)
                status, body = route(query) if route else (404, {"error": "not found"})
                self._send(status, body)

            def _send(self, status: int, body: object) -> None:
                if isinstance(body, bytes):
                    payload = body
                elif isinstance(body, str):
                    payload = body.encode()
                else:
                    payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler

    def __enter__(self) -> StubNightscout:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5.0)
        self._server.server_close()
