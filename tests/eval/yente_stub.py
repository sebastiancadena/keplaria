"""Deterministic local stand-in for yente, used only by the domain evals.

yente lives at 10.10.0.2:8000 on a VPC with no route from this machine
(see tests/integration/test_graph.py), so the eval harness points
YENTE_BASE_URL at this stub instead. Responses are keyed on the supplier
name the graph queries, giving each eval case a deterministic screening
outcome without any network dependency:

- the sanctioned name returns one confirmed match (score 1.0, match=true);
- the decoy name returns one sub-threshold candidate (0.52, match=false);
- the outage name returns HTTP 500, exercising the fail-closed path;
- every other name returns zero candidates.

All entities are fictional eval fixtures. Run:

    python tests/eval/yente_stub.py   # listens on YENTE_STUB_PORT (8452)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("YENTE_STUB_PORT", "8452"))

SANCTIONED = "Suministros Petro Andes SAS"
DECOY = "Comercial Andes Pacifico SAS"
OUTAGE = "Transportes Niebla Roja SAS"

RESPONSES: dict[str, list[dict]] = {
    SANCTIONED: [
        {
            "id": "eval-sanc-001",
            "caption": SANCTIONED,
            "score": 1.0,
            "match": True,
            "properties": {"topics": ["sanction"]},
        }
    ],
    DECOY: [
        {
            "id": "eval-decoy-001",
            "caption": "Comercial Andes Pacifica S.A.",
            "score": 0.52,
            "match": False,
            "properties": {"topics": ["sanction"]},
        }
    ],
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length))
            name = body["queries"]["q"]["properties"]["name"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            self.send_error(400, "malformed match query")
            return

        if name == OUTAGE:
            self.send_error(500, "simulated screening outage")
            return

        payload = json.dumps(
            {"responses": {"q": {"results": RESPONSES.get(name, [])}}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):  # quiet
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
