#!/bin/sh
set -eu

# AgentKit waits for the configured Tool port before marking the Session ready.
python -u - <<'PY' &
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy = self.path in {"/", "/health"}
        self.send_response(200 if healthy else 404)
        self.end_headers()
        self.wfile.write(b"ok" if healthy else b"not found")

    def log_message(self, *_args):
        pass

HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), HealthHandler).serve_forever()
PY

exec python /app/examples/16_self_host_sandbox/main.py --managed-agent-work-item
