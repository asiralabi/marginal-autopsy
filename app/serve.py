"""Serve the repository on 127.0.0.1 for the analyst tool.

Every response says Cache-Control: no-cache, so after an update the browser never
runs an old copy of the engine. Usage: python app/serve.py
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows reuse lets a second server share the port silently


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    server = Server(("127.0.0.1", 8765), partial(NoCacheHandler, directory=root))
    print("Analyst tool: http://127.0.0.1:8765/app/  (Ctrl+C to stop)")
    server.serve_forever()
