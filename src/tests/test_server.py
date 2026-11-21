import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from tupa.server import Handler, Snapshot


def status(affected):
    return {"updated": "2026-01-01T00:00:00-03:00", "members": {"n": {"status": affected}}}


class SnapshotTests(unittest.TestCase):
    def test_ttl_cache(self):
        values = iter([{"updated": "first"}, {"updated": "second"}])
        snapshot = Snapshot(60.0, lambda: next(values))
        with patch("tupa.server.time.monotonic", side_effect=[0.0, 10.0, 61.0, 62.0]):
            self.assertEqual(snapshot.get()["updated"], "first")
            self.assertEqual(snapshot.get()["updated"], "first")
            self.assertEqual(snapshot.get()["updated"], "second")
            self.assertEqual(snapshot.get()["updated"], "second")

    def test_stale_on_error(self):
        values = iter([status("operational"), RuntimeError("down")])

        def collector():
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return value

        snapshot = Snapshot(60.0, collector)
        with patch("tupa.server.time.monotonic", side_effect=[0.0, 61.0, 62.0]):
            self.assertEqual(snapshot.get()["members"]["n"]["status"], "operational")
            self.assertEqual(snapshot.get()["members"]["n"]["status"], "operational")
            self.assertEqual(snapshot.get()["members"]["n"]["status"], "operational")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.snapshot = Snapshot(60.0, lambda: status("operational"))
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_html_root(self):
        with urlopen(self.base + "/", timeout=3) as reply:
            self.assertEqual(reply.status, 200)
            self.assertEqual(reply.headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(reply.headers["Cache-Control"], "max-age=60")
            body = reply.read()
            self.assertIn(b"<!doctype html>", body)
            self.assertIn(b'<html lang="es-AR">', body)
            self.assertIn(b"<h2>n</h2>", body)
            self.assertIn("Operativo".encode(), body)
            self.assertIn("verificá".encode(), body)
            self.assertIn(b'href="/api"', body)

    def test_api_endpoint(self):
        with urlopen(self.base + "/api", timeout=3) as reply:
            self.assertEqual(reply.status, 200)
            self.assertEqual(
                reply.headers["Content-Type"], "application/json; charset=utf-8"
            )
            self.assertEqual(reply.headers["Cache-Control"], "max-age=60")
            payload = json.loads(reply.read())
            self.assertEqual(payload["members"]["n"]["status"], "operational")

    def test_unknown_path_is_404(self):
        for path in ("/status", "/status.json", "/missing"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(self.base + path, timeout=3)
            self.assertEqual(caught.exception.code, 404)
