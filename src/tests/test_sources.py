import io
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch

from tupa.model import SourceSpec
from tupa.sources import cammesa, edenor, edesur
from tupa.sources.common import (
    USER_AGENT,
    DemandThresholds,
    fetch_text,
    legacy_tls_context,
    thresholds_from,
)

FIXTURES = Path(__file__).parent / "fixtures"


def response(name):
    return io.BytesIO((FIXTURES / name).read_bytes())


class SourceTests(unittest.TestCase):
    def fetch(self, source, fixture_name):
        with patch(
            "tupa.sources.common.urlopen", return_value=response(fixture_name)
        ) as mock:
            member = source.fetch(timeout=4.0)
        return member, mock

    def test_edenor_active_outage(self):
        member, mock = self.fetch(edenor, "edenor_active.js")
        self.assertEqual(member.status, "operational")
        self.assertIn("138 usuarios sin suministro", member.comment)
        self.assertTrue(member.updated.endswith("-03:00"))
        self.assertTrue(member.updated.startswith("20"))
        self.assertIn("13:25:00", member.updated)
        request = mock.call_args.args[0]
        self.assertEqual(request.full_url, edenor.URL)
        self.assertEqual(request.get_header("User-agent"), USER_AGENT)
        self.assertEqual(mock.call_args.kwargs["timeout"], 4.0)
        context = mock.call_args.kwargs["context"]
        self.assertIs(context, legacy_tls_context())
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_custom_thresholds_are_used(self):
        custom = thresholds_from({"edenor": 138}, (0.5, 1.5, 2.0))
        with patch(
            "tupa.sources.common.urlopen", return_value=response("edenor_active.js")
        ):
            member = edenor.fetch(timeout=1.0, thresholds=custom)
        self.assertEqual(member.status, "warn")

    def test_generic_fetch_uses_default_tls(self):
        with patch(
            "tupa.sources.common.urlopen", return_value=response("edenor_active.js")
        ) as mock:
            fetch_text("https://example.test/data", timeout=2.0)
        self.assertIsNone(mock.call_args.kwargs["context"])

    def test_edenor_severe_outages(self):
        high, _ = self.fetch(edenor, "edenor_high.js")
        disaster, _ = self.fetch(edenor, "edenor_disaster.js")
        self.assertEqual(high.status, "high")
        self.assertEqual(disaster.status, "disaster")

    def test_planned_outages_use_regular_thresholds(self):
        member, _ = self.fetch(edesur, "edesur_planned.js")
        self.assertEqual(member.status, "warn")
        self.assertIn("2 cortes programados", member.comment)

    def test_na_value_is_unknown(self):
        member, _ = self.fetch(edenor, "edenor_na.js")
        self.assertEqual(member.status, "unknown")
        self.assertIn("N/A", member.comment)

    def test_wrong_empresa_is_rejected(self):
        with patch(
            "tupa.sources.common.urlopen", return_value=response("edesur_planned.js")
        ):
            with self.assertRaisesRegex(ValueError, "unexpected empresa"):
                edenor.fetch(timeout=1.0)

    def test_malformed_data_is_rejected(self):
        with patch("tupa.sources.common.urlopen", return_value=response("broken.js")):
            with self.assertRaises(ValueError):
                edesur.fetch(timeout=1.0)

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
