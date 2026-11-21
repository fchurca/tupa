from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import Config
from .core import collect
from .output import render

log = logging.getLogger(__name__)

PATHS = frozenset({"/", "/status", "/status.json"})


class Snapshot:
    def __init__(self, ttl: float, collector: Callable[[], dict]):
        self.ttl = ttl
        self.collector = collector
        self._lock = threading.Lock()
        self._expires = 0.0
        self._payload: bytes | None = None

    def get(self) -> bytes:
        with self._lock:
            now = time.monotonic()
            if self._payload is not None and now < self._expires:
                return self._payload
            try:
                status = self.collector()
            except Exception:
                log.exception("collection failed")
                if self._payload is not None:
                    self._expires = now + min(self.ttl, 10.0)
                    return self._payload
                raise
            self._payload = render(status, indent=None)
            self._expires = now + self.ttl
            return self._payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TUPA/0.1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path not in PATHS:
            self.send_error(404, "Not Found")
            return
        try:
            body = self.server.snapshot.get()
        except Exception:
            self.send_error(503, "Status unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    snapshot = Snapshot(
        config.ttl,
        lambda: collect(
            config.sources or None,
            timeout=config.timeout,
            thresholds=config.thresholds,
        ),
    )
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
