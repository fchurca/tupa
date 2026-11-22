from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import Config
from .core import collect_from_config
from .output import render, render_html
from .version import get_version

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "/": "text/html; charset=utf-8",
    "/api": "application/json; charset=utf-8",
}


class Snapshot:
    def __init__(self, ttl: float, collector: Callable[[], dict]):
        self.ttl = ttl
        self.collector = collector
        self._lock = threading.Lock()
        self._expires = 0.0
        self._status: dict | None = None

    def get(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._status is not None and now < self._expires:
                return self._status
            try:
                status = self.collector()
            except Exception:
                log.exception("collection failed")
                if self._status is not None:
                    self._expires = now + min(self.ttl, 10.0)
                    return self._status
                raise
            self._status = status
            self._expires = now + self.ttl
            return self._status


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"TUPA/{get_version()}"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        content_type = CONTENT_TYPES.get(path)
        if content_type is None:
            self.send_error(404, "Not Found")
            return
        try:
            status = self.server.snapshot.get()
        except Exception:
            self.send_error(503, "Status unavailable")
            return
        body = render(status, indent=None) if path == "/api" else render_html(status)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"max-age={math.ceil(self.server.snapshot.ttl)}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args) -> None:
        log.info("%s %s", self.address_string(), format % args)


def run_server(config: Config) -> None:
    snapshot = Snapshot(config.ttl, lambda: collect_from_config(config))
    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.daemon_threads = True
    server.snapshot = snapshot
    log.info("serving on http://%s:%s (ttl=%ss)", config.host, config.port, config.ttl)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
