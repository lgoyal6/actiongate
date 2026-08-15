"""Minimal webhook receiver (stdlib http.server only).

Reads exactly Content-Length bytes, verifies the HMAC over those bytes, and only
then parses.  Deliberately small: the interesting part is what it refuses.

  401  x-signature missing, malformed, or does not verify
  400  verified but the body is not JSON / not a meeting object
  413  body larger than MAX_BODY
  200  verified and processed; returns the gate decisions as JSON
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .pipeline import SignatureError, default_pipeline
from .signature import SIGNATURE_HEADER

MAX_BODY = 8 * 1024 * 1024  # 8 MiB; transcripts are text, this is generous


def make_handler(pipeline):
    class Handler(BaseHTTPRequestHandler):
        server_version = "actiongate/1.0"
        protocol_version = "HTTP/1.1"

        def _reply(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reply(400, {"error": "bad Content-Length"})
                return
            if length > MAX_BODY:
                self._reply(413, {"error": "body too large"})
                return

            raw = self.rfile.read(length)  # raw bytes; never re-serialized
            try:
                summary = pipeline.handle(raw, self.headers.get(SIGNATURE_HEADER))
            except SignatureError:
                self._reply(401, {"error": "invalid signature"})
                return
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                self._reply(400, {"error": f"bad payload: {exc}"})
                return
            self._reply(200, summary)

        def log_message(self, fmt, *args):  # keep the demo output readable
            pass

    return Handler


def serve(state_dir: Path, signing_secret: str, host: str = "127.0.0.1", port: int = 8787,
          threshold: float | None = None) -> None:
    pipeline = default_pipeline(state_dir, signing_secret, threshold)
    httpd = ThreadingHTTPServer((host, port), make_handler(pipeline))
    print(f"actiongate listening on http://{host}:{port}  (POST any path)")
    print(f"  auto-commit threshold : {pipeline.gate.auto_commit_at}")
    print(f"  audit log             : {pipeline.audit.path}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
