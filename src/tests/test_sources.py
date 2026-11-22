import io
import json
import multiprocessing
import socket
import ssl
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tupa.model import AR_TZ, SourceSpec
from tupa.sources import DEFAULT_SOURCES, cammesa
from tupa.sources.common import (
    USER_AGENT,
    DemandThresholds,
    _dns_local,
    active_sockets,
    cancel_thread,
    clear_cancellation,
    dns_deadline,
    fetch_text,
    legacy_tls_context,
    register_socket,
    thresholds_from,
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

    def test_dns_lookup_is_bounded_by_deadline(self):
        before = len(multiprocessing.active_children())
        start = time.monotonic()
        with self.assertRaises(socket.gaierror) as caught:
            with dns_deadline(time.monotonic() + 0.001):
                socket.getaddrinfo("dns-timeout.invalid", 80)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertIn("timed out", str(caught.exception))
        self.assertEqual(len(multiprocessing.active_children()), before)

    def test_dns_bypasses_child_for_numeric_hosts_and_no_deadline(self):
        with patch(
            "tupa.sources.common.multiprocessing.get_context",
            side_effect=AssertionError("child spawned"),
        ):
            numeric = socket.getaddrinfo("127.0.0.1", 80, type=socket.SOCK_STREAM)
            plain = socket.getaddrinfo("localhost", 80, type=socket.SOCK_STREAM)
        self.assertTrue(numeric)
        self.assertTrue(plain)

    def test_dns_deadline_restores_previous_value(self):
        outer = time.monotonic() + 10
        with dns_deadline(outer):
            with dns_deadline(outer + 1):
                self.assertEqual(_dns_local.deadline, outer + 1)
            self.assertEqual(_dns_local.deadline, outer)
        self.assertIsNone(getattr(_dns_local, "deadline", None))

    def test_cancel_thread_closes_registered_socket(self):
        left, right = socket.socketpair()
        try:
            register_socket(left)
            self.assertEqual(active_sockets(), 1)
            self.assertTrue(cancel_thread(threading.get_ident()))
            self.assertEqual(active_sockets(), 0)
            with self.assertRaises(OSError):
                left.send(b"x")
        finally:
            clear_cancellation()
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
