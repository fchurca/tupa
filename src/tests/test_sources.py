import io
import json
import ssl
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from tupa.model import ART_TZ, make_member
from tupa.sources import DEFAULT_SOURCES, cammesa, fetch_source, oceba
from tupa.sources.common import (
    DEFAULT_CUSTOMERS,
    DEFAULT_DEMAND_LEVELS,
    DEFAULT_LEVELS,
    USER_AGENT,
    Thresholds,
    fetch_text,
    legacy_tls_context,
    thresholds_from,
)
from tupa.sources.enre import fetch_enre

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_THRESHOLDS = thresholds_from(DEFAULT_CUSTOMERS, DEFAULT_LEVELS)
DEMAND_THRESHOLDS = Thresholds(*DEFAULT_DEMAND_LEVELS)


class FakeResponse(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def response(name, headers=None):
    return FakeResponse((FIXTURES / name).read_bytes(), headers)


class SourceTests(unittest.TestCase):
    def fetch(self, source_name, fixture_name, *, thresholds=DEFAULT_THRESHOLDS, headers=None):
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
                tz=ART_TZ,
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

    def test_fetch_rejects_oversized_response(self):
        oversized = b"x" * 1_000_001
        with patch("tupa.sources.common.urlopen", return_value=FakeResponse(oversized)):
            with self.assertRaisesRegex(ValueError, "response exceeds 1000000 bytes"):
                fetch_text("https://example.test/data", timeout=1.0)

    def test_fetch_decodes_chunked_response(self):
        body = (
            b'[{"fecha":"2026-01-01T00:00:00-0300","demHoy":1,'
            b'"demAyer":2,"demSemanaAnt":3}]'
        )

        class Chunked(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for start in range(0, len(body), 16):
                    piece = body[start : start + 16]
                    self.wfile.write(f"{len(piece):X}\r\n".encode("ascii"))
                    self.wfile.write(piece)
                    self.wfile.write(b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Chunked)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            text = fetch_text(
                f"http://127.0.0.1:{server.server_port}/", timeout=2.0
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(text, body.decode())

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
        spec = DEFAULT_SOURCES["cammesa_edenor"]
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_high.json")
        ) as mock:
            member = cammesa.fetch_demand(spec=spec, timeout=4.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
        self.assertEqual(member.status, "high")
        self.assertIn("700 MW", member.comment)
        self.assertIn("30.0% bajo referencia", member.comment)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")
        self.assertEqual(mock.call_args.args[0].full_url, cammesa.url_for(1077))
        self.assertEqual(mock.call_args.kwargs["timeout"], 4.0)

    def test_cammesa_edelap_uses_region_1943(self):
        spec = DEFAULT_SOURCES["cammesa_edelap"]
        self.assertEqual(spec.member, "edelap")
        self.assertEqual(spec.kind, "cammesa_demand")
        self.assertEqual(spec.region, 1943)
        self.assertEqual(spec.label, "CAMMESA Edelap (1943)")
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_high.json")
        ) as mock:
            member = fetch_source(
                spec,
                timeout=4.0,
                thresholds=DEFAULT_THRESHOLDS,
                demand_thresholds=DEMAND_THRESHOLDS,
                tz=ART_TZ,
            )
        self.assertEqual(member.status, "high")
        self.assertIn("CAMMESA región 1943", member.comment)
        self.assertEqual(mock.call_args.args[0].full_url, cammesa.url_for(1943))

    def test_cammesa_custom_thresholds(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
        thresholds = Thresholds(0.25, 0.40, 0.50)
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_high.json")
        ):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=thresholds, tz=ART_TZ)
        self.assertEqual(member.status, "warn")

    def test_cammesa_without_comparable_data_is_unknown(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_no_data.json")
        ):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
        self.assertEqual(member.status, "unknown")
        self.assertIn("sin datos comparables", member.comment)

    def test_cammesa_comment_matches_displayed_values(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
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
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
        self.assertIn("700 MW; 30.0% bajo referencia 1000 MW", member.comment)
        self.assertEqual(member.updated, "2026-11-21T13:55:00-03:00")

    def test_cammesa_invalid_fecha_is_rejected(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
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
            cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)

    def test_cammesa_naive_fecha_uses_local_offset(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
        payload = [
            {
                "fecha": "2026-11-21T14:00:00",
                "demHoy": 700,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }
        ]
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")

    def test_cammesa_zero_demand_is_disaster(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
        payload = [
            {
                "fecha": "2026-11-21T14:00:00-0300",
                "demHoy": 0,
                "demAyer": 1000,
                "demSemanaAnt": 1000,
            }
        ]
        with patch("tupa.sources.cammesa.fetch_text", return_value=json.dumps(payload)):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
        self.assertEqual(member.status, "disaster")
        self.assertIn("0 MW; 100.0% bajo referencia", member.comment)

    def test_cammesa_orders_rows_before_selecting_window(self):
        spec = DEFAULT_SOURCES["cammesa_edenor"]
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
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=DEMAND_THRESHOLDS, tz=ART_TZ)
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
                thresholds=DEFAULT_THRESHOLDS,
                tz=timezone.utc,
            )
        self.assertEqual(member.updated, "2026-11-21T16:25:00+00:00")

    def test_fetch_enforces_deadline_during_body(self):
        class SlowBody(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            writes_body = True

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "20")
                self.end_headers()
                try:
                    if self.writes_body:
                        for _ in range(20):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            time.sleep(0.05)
                    else:
                        time.sleep(1)
                except OSError:
                    return

            def log_message(self, *args):
                return

        class NoBody(SlowBody):
            writes_body = False

        def check(handler):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
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

        for handler in (SlowBody, NoBody):
            with self.subTest(handler=handler.__name__):
                check(handler)

    def test_edelap_from_oceba_fixture(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 23, 13, 0, tzinfo=tz)

        spec = DEFAULT_SOURCES["oceba_edelap"]
        with (
            patch(
                "tupa.sources.common.urlopen",
                return_value=response("oceba_edelap.html"),
            ) as mock,
            patch("tupa.sources.oceba.datetime", FixedDatetime),
        ):
            member = oceba.fetch_oceba(
                spec=spec,
                timeout=4.0,
                thresholds=DEFAULT_THRESHOLDS,
                tz=ART_TZ,
            )
        self.assertEqual(member.status, "warn")
        self.assertIn("122 usuarios sin suministro", member.comment)
        self.assertIn("5 cortes informados", member.comment)
        self.assertIn("OCEBA ultimaActualizacion 12:10", member.comment)
        self.assertEqual(member.updated, "2026-11-23T12:10:00-03:00")
        self.assertEqual(mock.call_args.args[0].full_url, spec.url)
        self.assertEqual(mock.call_args.kwargs["timeout"], 4.0)

    def test_edelap_distributor_mismatch_is_rejected(self):
        text = response("oceba_edelap.html").read().decode().replace("EDELAP S.A.", "OTRA S.A.")
        with patch("tupa.sources.oceba.fetch_content", return_value=(text, {})):
            with self.assertRaisesRegex(ValueError, "unexpected distributor"):
                oceba.fetch_oceba(
                    spec=DEFAULT_SOURCES["oceba_edelap"],
                    timeout=1.0,
                    thresholds=DEFAULT_THRESHOLDS,
                    tz=ART_TZ,
                )

    def test_oceba_source_dispatch(self):
        spec = DEFAULT_SOURCES["oceba_edelap"]
        with patch("tupa.sources.fetch_oceba", return_value=make_member("operational")) as fetch:
            member = fetch_source(
                spec,
                timeout=1.0,
                thresholds=DEFAULT_THRESHOLDS,
                demand_thresholds=DEMAND_THRESHOLDS,
                tz=ART_TZ,
            )
        self.assertEqual(member.status, "operational")
        fetch.assert_called_once_with(
            spec=spec,
            timeout=1.0,
            thresholds=DEFAULT_THRESHOLDS,
            tz=ART_TZ,
            deadline=None,
        )

    def test_non_http_schemes_are_not_opened(self):
        for url in ("file:///etc/passwd", "ftp://example.test/file"):
            with self.subTest(url=url), self.assertRaises(URLError):
                fetch_text(url, timeout=1.0)
