import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from tupa.server import Handler, Snapshot
from tupa.sources import DEFAULT_SOURCES


def status(affected):
    return {"updated": "2026-01-01T00:00:00-03:00", "members": {"n": {"status": affected}}}


class SnapshotTests(unittest.TestCase):
    def test_ttl_cache(self):
        values = iter([{"updated": "first"}, {"updated": "second"}])
        snapshot = Snapshot(60.0, lambda: next(values))
        clock = [0.0]
        with patch("tupa.server.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(snapshot.get()["updated"], "first")
            clock[0] = 10.0
            self.assertEqual(snapshot.get()["updated"], "first")
            clock[0] = 61.0
            self.assertEqual(snapshot.get()["updated"], "second")
            clock[0] = 62.0
            self.assertEqual(snapshot.get()["updated"], "second")

    def test_first_failure_propagates(self):
        def collector():
            raise RuntimeError("down")

        with patch("tupa.server.log"), self.assertRaises(RuntimeError):
            Snapshot(60.0, collector).get()

    def test_zero_ttl_disables_caching(self):
        values = iter([{"updated": "first"}, {"updated": "second"}])
        snapshot = Snapshot(0.0, lambda: next(values))
        self.assertEqual(snapshot.get()["updated"], "first")
        self.assertEqual(snapshot.get()["updated"], "second")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.snapshot = Snapshot(60.0, lambda: status("operational"))
        self.server.active_sources = [DEFAULT_SOURCES["cammesa_edenor"]]
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

    def request(self, path, method="GET"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path)
        reply = connection.getresponse()
        body = reply.read()
        headers = dict(reply.getheaders())
        connection.close()
        return reply.status, headers, body

    def test_html_root(self):
        code, headers, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "max-age=60")
        self.assertIn(b"<!doctype html>", body)
        self.assertIn(b'<html lang="es-AR">', body)
        self.assertIn(b"<h2>n</h2>", body)
        self.assertIn("Operativo".encode(), body)
        self.assertIn("verificá".encode(), body)
        self.assertIn(b'href="/api"', body)
        self.assertIn(b"CAMMESA Edenor (1077)", body)
        self.assertNotIn(b"ENRE Edesur", body)
        self.assertIn(b'<details class="sources">', body)
        self.assertIn(b"Content-Security-Policy", body)
        self.assertIn(b"default-src 'none'", body)
        self.assertNotIn(b"script-src", body)
        self.assertNotIn(b"<script", body)

    def test_api_endpoint(self):
        code, headers, body = self.request("/api")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "max-age=60")
        payload = json.loads(body)
        self.assertEqual(payload["members"]["n"]["status"], "operational")

    def test_head_has_no_body(self):
        _, _, get_body = self.request("/api")
        code, headers, body = self.request("/api", method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(get_body))

    def test_unknown_path_is_404(self):
        for path in ("/missing",):
            with self.subTest(path=path):
                code, _, _ = self.request(path)
                self.assertEqual(code, 404)
