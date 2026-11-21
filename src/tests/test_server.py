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
        values = iter(["first", "second"])
        collector = lambda: next(values)
        snapshot = Snapshot(60.0, collector)
        with patch("tupa.server.time.monotonic", side_effect=[0.0, 10.0, 61.0, 62.0]):
            self.assertIn(b"first", snapshot.get())
            self.assertIn(b"first", snapshot.get())
            self.assertIn(b"second", snapshot.get())
            self.assertIn(b"second", snapshot.get())

    def test_stale_on_error(self):
        values = iter([status("operational"), RuntimeError("down")])

        def collector():
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return value

        snapshot = Snapshot(60.0, collector)
        with patch("tupa.server.time.monotonic", side_effect=[0.0, 61.0, 62.0]):
            self.assertIn(b"operational", snapshot.get())
            self.assertIn(b"operational", snapshot.get())
            self.assertIn(b"operational", snapshot.get())


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

    def test_status_endpoints(self):
        for path in ("/", "/status", "/status.json"):
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as reply:
                self.assertEqual(reply.status, 200)
                self.assertEqual(
                    reply.headers["Content-Type"], "application/json; charset=utf-8"
                )
                self.assertEqual(
                    reply.headers["Cache-Control"], "max-age=60"
                )
                payload = json.loads(reply.read())
                self.assertEqual(payload["members"]["n"]["status"], "operational")

    def test_unknown_path_is_404(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/missing", timeout=3)
        self.assertEqual(caught.exception.code, 404)
