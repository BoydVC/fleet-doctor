#!/usr/bin/env python3
"""Fleet Doctor live server (stdlib). GET / -> live report (re-analyzed, cached 60 s); /api/report -> JSON; /health."""
import json
import os
import sys
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fleet_doctor  # noqa: E402

PORT = int(os.environ.get("PORT", 4259))
_cache = {"t": 0, "rep": None}


def report():
    if not _cache["rep"] or time.time() - _cache["t"] > 60:
        _cache["rep"], _cache["t"] = fleet_doctor.build(), time.time()
    return _cache["rep"]


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        path, view = u.path, (parse_qs(u.query).get("view") or [None])[0]
        try:
            if path in ("/", "/index.html"):
                self.send(200, fleet_doctor.render(report(), live=True, view=view), "text/html; charset=utf-8")
            elif path == "/api/report":
                self.send(200, json.dumps(report(), default=str), "application/json")
            elif path == "/health":
                self.send(200, json.dumps({"ok": True, "port": PORT}), "application/json")
            else:
                self.send(404, "not found", "text/plain")
        except Exception as ex:  # keep serving
            self.send(500, f"error: {ex}", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Fleet Doctor on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
