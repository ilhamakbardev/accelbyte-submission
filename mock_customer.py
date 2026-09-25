"""Controllable mock customer endpoint for demos.

Usage:
    python mock_customer.py --port 9000 --fail-first 2 --fail-status 500

Behavior:
- POST /hook logs headers + body, returns <fail-status> for the first
  N requests, then 200. GET /counts shows hits.
- Slow mode: --delay 3 delays each response by N seconds (tests timeouts).
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HITS = 0


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        global HITS
        HITS += 1
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        if self.server.delay:  # type: ignore[attr-defined]
            time.sleep(self.server.delay)  # type: ignore[attr-defined]
        print(f"[mock] hit={HITS} event={self.headers.get('X-Event-Id')} "
              f"attempt={self.headers.get('X-Attempt-Number')} body={raw[:200]!r}", flush=True)
        if HITS <= self.server.fail_first:  # type: ignore[attr-defined]
            self._send(self.server.fail_status, {"ok": False})  # type: ignore[attr-defined]
        else:
            self._send(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"hits": HITS})

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--fail-first", type=int, default=0)
    p.add_argument("--fail-status", type=int, default=500)
    p.add_argument("--delay", type=float, default=0.0)
    a = p.parse_args()
    srv = HTTPServer(("0.0.0.0", a.port), Handler)
    srv.fail_first = a.fail_first  # type: ignore[attr-defined]
    srv.fail_status = a.fail_status  # type: ignore[attr-defined]
    srv.delay = a.delay  # type: ignore[attr-defined]
    print(f"mock customer on :{a.port} fail_first={a.fail_first} fail_status={a.fail_status} delay={a.delay}")
    srv.serve_forever()
