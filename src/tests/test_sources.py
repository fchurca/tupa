import io
import json
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

from tupa.model import SourceSpec
from tupa.sources import DEFAULT_SOURCES, cammesa
from tupa.sources.common import (
    USER_AGENT,
    DemandThresholds,
    fetch_text,
    legacy_tls_context,
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
            member = cammesa.fetch_demand(spec=spec, timeout=4.0)
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
            member = cammesa.fetch_demand(spec=spec, timeout=1.0, thresholds=thresholds)
        self.assertEqual(member.status, "warn")

    def test_cammesa_without_comparable_data_is_unknown(self):
        spec = SourceSpec("cammesa_edenor", "cammesa_demand", "edenor", region=1077)
        with patch(
            "tupa.sources.common.urlopen", return_value=response("cammesa_no_data.json")
        ):
            member = cammesa.fetch_demand(spec=spec, timeout=1.0)
        self.assertEqual(member.status, "unknown")
        self.assertIn("sin datos comparables", member.comment)

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
            cammesa.fetch_demand(spec=spec, timeout=1.0)

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
            member = cammesa.fetch_demand(spec=spec, timeout=1.0)
        self.assertEqual(member.updated, "2026-11-21T14:00:00-03:00")
