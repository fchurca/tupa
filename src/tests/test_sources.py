import io
import json
import socket
import ssl
import threading
import time
import unittest
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import tupa.sources.common as common
from tupa.model import AR_TZ, SourceSpec
from tupa.sources import DEFAULT_SOURCES, cammesa
from tupa.sources.common import (
    USER_AGENT,
    DemandThresholds,
    active_sockets,
    FetchTimeout,
    _is_private_target,
    begin_fetch,
    cancel_fetch,
    clear_cancellation,
    clear_fetch_token,
    fetch_text,
    legacy_tls_context,
    register_socket,
    set_fetch_token,
    thresholds_from,
    unregister_fetch,
)
from tupa.sources.enre import fetch_enre

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def response(name, headers=None):
    return FakeResponse((FIXTURES / name).read_bytes(), headers)


class SourceTests(unittest.TestCase):
    def fetch(self, source_name, fixture_name, *, thresholds=None, headers=None):
        spec = DEFAULT_SOURCES[source_name]
        with patch(
            "tupa.sources.common.urlopen",
            return_value=response(fixture_name, headers),
        ) as mock:
            member = fetch_enre(
                name=spec.member,
                empresa=spec.empresa,
                url=spec.url,
                timeout=4.0,
                thresholds=thresholds,
                tz=AR_TZ,
            )
        return member, mock

    def test_edenor_active_outage(self):
        headers = {"Last-Modified": "Sat, 21 Nov 2026 16:25:00 GMT"}
        member, mock = self.fetch("enre_edenor", "edenor_active.js", headers=headers)
        self.assertEqual(member.status, "operational")
        self.assertIn("138 usuarios sin suministro", member.comment)
        self.assertEqual(member.updated, "2026-11-21T13:25:00-03:00")
        request = mock.call_args.args[0]
        self.assertEqual(request.full_url, DEFAULT_SOURCES["enre_edenor"].url)
        self.assertEqual(request.get_header("User-agent"), USER_AGENT)
        self.assertEqual(mock.call_args.kwargs["timeout"], 4.0)
        context = mock.call_args.kwargs["context"]
        self.assertIs(context, legacy_tls_context())
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_enre_without_date_is_not_backdated(self):
        member, _ = self.fetch("enre_edenor", "edenor_active.js")
        self.assertIn("13:25", member.comment)
        self.assertIn("fecha no informada", member.comment)
        self.assertNotIn("13:25:00", member.updated)
        self.assertTrue(member.updated.endswith("-03:00"))

        member, _ = self.fetch(
            "enre_edenor", "edenor_active.js", headers={"Last-Modified": "invalid"}
        )
        self.assertIn("fecha no informada", member.comment)

    def test_custom_thresholds_are_used(self):
        custom = thresholds_from({"edenor": 138}, (0.5, 1.5, 2.0))
        member, _ = self.fetch(
            "enre_edenor", "edenor_active.js", thresholds=custom
        )
        self.assertEqual(member.status, "warn")

    def test_generic_fetch_uses_default_tls(self):
        with patch(
            "tupa.sources.common.urlopen", return_value=response("edenor_active.js")
        ) as mock:
            fetch_text("https://example.test/data", timeout=2.0)
        self.assertIsNone(mock.call_args.kwargs["context"])

    def test_cancel_fetch_closes_registered_socket(self):
        left, right = socket.socketpair()
        token = object()
        begin_fetch(token)
        try:
            register_socket(left, token)
            self.assertEqual(active_sockets(), 1)
            self.assertTrue(cancel_fetch(token))
            self.assertEqual(active_sockets(), 0)
            with self.assertRaises(OSError):
                left.send(b"x")
        finally:
            clear_cancellation(token)
            left.close()
            right.close()

    def test_fetch_rejects_oversized_response(self):
        oversized = b"x" * 1_000_001
        with patch("tupa.sources.common.urlopen", return_value=FakeResponse(oversized)):
            with self.assertRaisesRegex(ValueError, "response exceeds 1000000 bytes"):
                fetch_text("https://example.test/data", timeout=1.0)

    def test_edenor_severe_outages(self):
        high, _ = self.fetch("enre_edenor", "edenor_high.js")
        disaster, _ = self.fetch("enre_edenor", "edenor_disaster.js")
        self.assertEqual(high.status, "high")
        self.assertEqual(disaster.status, "disaster")

    def test_planned_outages_use_regular_thresholds(self):
        member, _ = self.fetch("enre_edesur", "edesur_planned.js")
        self.assertEqual(member.status, "warn")
        self.assertIn("2 cortes programados", member.comment)

    def test_na_value_is_unknown(self):
        member, _ = self.fetch("enre_edenor", "edenor_na.js")
        self.assertEqual(member.status, "unknown")
        self.assertIn("N/A", member.comment)

    def test_wrong_empresa_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected empresa"):
            self.fetch("enre_edenor", "edesur_planned.js")

    def test_malformed_data_is_rejected(self):
        with self.assertRaises(ValueError):
            self.fetch("enre_edesur", "broken.js")

    def test_cammesa_high_deficit(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_high.json")
        ) as mock:
            member = cammesa.fetch_demand(spec=spec, timeout=4.0, tz=AR_TZ)
        self.assertEqual(member.status, "high")
        self.assertIn("700 MW", member.comment)
        self.assertIn("30.0% bajo referencia", member.comment)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")
        self.assertEqual(mock.call_args.args[0].full_url, cammesa.url_for(1077))
        self.assertEqual(mock.call_args.kwargs["timeout"], 4.0)

    def test_cammesa_custom_thresholds(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        thresholds = DemandThresholds(0.25, 0.40, 0.50)
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_high.json")
        ):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=thresholds, tz=AR_TZ)
        self.assertEqual(member.status, "warn")

    def test_cammesa_without_comparable_data_is_unknown(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_no_data.json")
        ):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)
        self.assertEqual(member.status, "unknown")
        self.assertIn("sin datos comparables", member.comment)

    def test_cammesa_comment_matches_displayed_values(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        payload = [
            {
                "fecha": "2026-11-21T13:50:00.000-0300",
                "demHoy": 700,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            },
            {
                "fecha": "2026-11-21T13:55:00.000-0300",
                "demHoy": 700,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            },
            {
                "fecha": "2026-11-21T14:00:00.000-0300",
                "demHoy": 1000,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            },
        ]
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)
        self.assertIn("700 MW; 30.0% bajo referencia 1000 MW", member.comment)
        self.assertEqual(member.updated, "2026-11-21T13:55:00-03:00")

    def test_cammesa_invalid_fecha_is_rejected(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        payload = [
            {
                "fecha": "not-a-date",
                "demHoy": 700,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }
        ]
        with (
            patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)),
            self.assertRaisesRegex(ValueError, "fecha no válida"),
        ):
            cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)

    def test_cammesa_naive_fecha_uses_local_offset(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        payload = [
            {
                "fecha": "2026-11-21T14:00:00",
                "demHoy": 700,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }
        ]
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")

    def test_cammesa_zero_demand_is_disaster(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        payload = [
            {
                "fecha": "2026-11-21T14:00:00-0300",
                "demHoy": 0,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }
        ]
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)
        self.assertEqual(member.status, "disaster")
        self.assertIn("0 MW; 100.0% bajo referencia", member.comment)

    def test_cammesa_orders_rows_before_selecting_window(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        def row(minute, deficit):
            return {
                "fecha": f"2026-11-21T13:{minute}:00-0300",
                "demHoy": 1000 * (1 - deficit),
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }

        payload = [row(5, 0.0), row(50, 0.5), row(55, 0.5), row(0, 0.5), row(45, 0.5)]
        payload[0]["fecha"] = "2026-11-21T14:05:00-0300"
        payload[3]["fecha"] = "2026-11-21T14:00:00-0300"
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, tz=AR_TZ)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")
        self.assertEqual(member.status, "disaster")

    def test_enre_time_is_art_even_with_utc_output(self):
        headers = {"Last-Modified": "Sat, 21 Nov 2026 16:25:00 GMT"}
        spec = DEFAULT_SOURCES["enre_edenor"]
        with patch(
            "tupa.sources.common.urlopen",
            return_value=response("edenor_active.js", headers),
        ):
            member = fetch_enre(
                name=spec.member,
                empresa=spec.empresa,
                url=spec.url,
                timeout=4.0,
                tz=timezone.utc,
            )
        self.assertEqual(member.updated, "2026-11-21T16:25:00+00:00")

    def test_fetch_enforces_deadline_during_body(self):
        class SlowBody(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "20")
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    return

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowBody)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            start = time.monotonic()
            with self.assertRaises(FetchTimeout):
                fetch_text(
                    f"http://127.0.0.1:{server.server_port}/",
                    timeout=5.0,
                    deadline=start + 0.3,
                )
            self.assertLess(time.monotonic() - start, 2.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_fetch_cancelled_before_start(self):
        token = object()
        begin_fetch(token)
        cancel_fetch(token)
        set_fetch_token(token)
        try:
            with self.assertRaises(FetchTimeout):
                fetch_text("http://127.0.0.1:1/", timeout=1.0)
        finally:
            clear_fetch_token()
            clear_cancellation(token)
            unregister_fetch(token)

    def test_private_redirect_targets_are_rejected(self):
        self.assertTrue(_is_private_target("127.0.0.1"))
        self.assertTrue(_is_private_target("::1"))
        self.assertTrue(_is_private_target("10.0.0.1"))
        self.assertTrue(_is_private_target("169.254.169.254"))
        self.assertFalse(_is_private_target("8.8.8.8"))

    def test_special_address_ranges_are_restricted(self):
        for host in ("100.64.0.1", "100.100.100.200", "224.0.0.1", "0.0.0.0"):
            with self.subTest(host=host):
                self.assertTrue(_is_private_target(host))
        with patch(
            "tupa.sources.common.socket.getaddrinfo",
            side_effect=socket.gaierror("resolver down"),
        ):
            self.assertIsNone(common._target_privacy("unresolvable.example"))
            self.assertTrue(_is_private_target("unresolvable.example"))

    def test_connect_rejects_restricted_addresses(self):
        previous = getattr(common._fetch_local, "allow_private", False)
        try:
            common._fetch_local.allow_private = False
            with self.assertRaises(socket.gaierror) as caught:
                common._connect_with_deadline(("127.0.0.1", 80), 1.0, None)
            self.assertIn("not allowed", str(caught.exception))
        finally:
            common._fetch_local.allow_private = previous

    def test_check_cancelled_closes_socket(self):
        token = object()
        begin_fetch(token)
        cancel_fetch(token)
        set_fetch_token(token)
        left, right = socket.socketpair()
        try:
            with self.assertRaises(FetchTimeout):
                common._check_cancelled(left)
            with self.assertRaises(OSError):
                left.send(b"x")
        finally:
            clear_fetch_token()
            clear_cancellation(token)
            left.close()
            right.close()

    def test_redirect_handler_fails_closed(self):
        request = Request("https://public.example/")
        handler = common._RestrictedRedirectHandler(allow_private=False)
        with patch("tupa.sources.common._target_privacy", return_value=True):
            with self.assertRaisesRegex(HTTPError, "unsafe redirect target"):
                handler.redirect_request(
                    request, io.BytesIO(b""), 302, "Found", {}, "http://127.0.0.1/"
                )
        with patch("tupa.sources.common._target_privacy", return_value=None):
            with self.assertRaisesRegex(HTTPError, "unsafe redirect target"):
                handler.redirect_request(
                    request, io.BytesIO(b""), 302, "Found", {}, "http://127.0.0.1/"
                )
        permissive = common._RestrictedRedirectHandler(allow_private=True)
        with patch("tupa.sources.common._target_privacy", return_value=True):
            redirected = permissive.redirect_request(
                request, io.BytesIO(b""), 302, "Found", {}, "http://127.0.0.1/"
            )
        self.assertEqual(redirected.full_url, "http://127.0.0.1/")

    def test_non_http_schemes_are_not_opened(self):
        for url in ("file:///etc/passwd", "ftp://example.test/file"):
            with self.subTest(url=url), self.assertRaises(URLError):
                fetch_text(url, timeout=1.0)

    def test_fetch_unwraps_timeout_from_urlerror(self):
        with patch(
            "tupa.sources.common.urlopen",
            side_effect=URLError(FetchTimeout("cancelled during connect")),
        ):
            with self.assertRaises(FetchTimeout):
                fetch_text(
                    "https://example.test/", timeout=1.0, deadline=time.monotonic() + 1
                )

    def test_fetch_timeout_when_body_stalls(self):
        class NoBody(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "20")
                self.end_headers()
                time.sleep(1)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), NoBody)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            start = time.monotonic()
            with self.assertRaises(FetchTimeout):
                fetch_text(
                    f"http://127.0.0.1:{server.server_port}/",
                    timeout=5.0,
                    deadline=start + 0.2,
                )
            self.assertLess(time.monotonic() - start, 2.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
