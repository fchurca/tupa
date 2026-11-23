import ssl
import unittest
from pathlib import Path

from tupa.sources.common import (
    DEFAULT_CUSTOMERS,
    DEFAULT_LEVELS,
    Thresholds,
    decode_js_data,
    legacy_tls_context,
    parse_count,
    thresholds_for,
    thresholds_from,
)

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_THRESHOLDS = thresholds_from(DEFAULT_CUSTOMERS, DEFAULT_LEVELS)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class CommonTests(unittest.TestCase):
    def test_decodes_real_enre_shape(self):
        data = decode_js_data(fixture("edenor_active.js"))
        self.assertEqual(data["empresa"], "EDENOR")
        self.assertEqual(data["ultimaActualizacion"], "13:25")
        self.assertEqual(data["totalUsuariosSinSuministro"], "138")
        self.assertEqual(len(data["cortesServicioMedia"]), 1)
        self.assertEqual(data["cortesServicioMedia"][0]["partido"], "PILAR")

    def test_decodes_quotes_escapes_and_unicode(self):
        data = decode_js_data(fixture("edesur_planned.js"))
        row = data["cortesProgramados"][0]
        self.assertEqual(row["localidad"], "L'GLEW")
        self.assertEqual(data["cortesProgramados"][1]["partido"], "CA\u00d1UELAS")
        self.assertEqual(len(data["cortesProgramados"]), 2)

    def test_rejects_unicode_escapes(self):
        with self.assertRaisesRegex(ValueError, "unsupported escape"):
            decode_js_data('var data = {"x": "a\\u00f1o"}')

    def test_trailing_comma_and_missing_assignment(self):
        self.assertEqual(decode_js_data("var data = { a: 1, }"), {"a": 1})
        with self.assertRaises(ValueError):
            decode_js_data("nothing here")

    def test_parses_js_escapes_and_numbers(self):
        data = decode_js_data(
            r'var data = {"nl": "one\ntwo", "exp": 1e3, "frac": .5}'
        )
        self.assertEqual(data, {"nl": "one\ntwo", "exp": 1000.0, "frac": 0.5})

    def test_legacy_context_keeps_verification(self):
        context = legacy_tls_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertIs(legacy_tls_context(), context)

    def test_parse_count(self):
        self.assertEqual(parse_count("3.505.956"), 3505956)
        self.assertEqual(parse_count("1.500"), 1500)
        self.assertEqual(parse_count(138), 138)
        self.assertEqual(parse_count(138.5), 138)
        self.assertIsNone(parse_count("1,234"))
        self.assertIsNone(parse_count("1.5"))
        self.assertIsNone(parse_count(-1))
        self.assertIsNone(parse_count(float("nan")))
        self.assertIsNone(parse_count(True))
        self.assertIsNone(parse_count("N/A"))
        self.assertIsNone(parse_count(None))

    def test_missing_threshold_baseline_is_reported(self):
        with self.assertRaisesRegex(ValueError, "no thresholds configured"):
            thresholds_for("unknown_member", {})

    def test_thresholds_per_source(self):
        expected = {
            "edenor": (340, 17000, 170000),
            "edesur": (270, 13500, 135000),
            "edelap": (40, 2000, 20000),
        }
        for name, values in expected.items():
            with self.subTest(source=name):
                thresholds = thresholds_for(name, DEFAULT_THRESHOLDS)
                self.assertEqual(
                    (thresholds.warn, thresholds.high, thresholds.disaster), values
                )
                self.assertEqual(thresholds.classify(values[0] - 1), "operational")
                self.assertEqual(thresholds.classify(values[0]), "warn")
                self.assertEqual(thresholds.classify(values[1] - 1), "warn")
                self.assertEqual(thresholds.classify(values[1]), "high")
                self.assertEqual(thresholds.classify(values[2] - 1), "high")
                self.assertEqual(thresholds.classify(values[2]), "disaster")
        self.assertEqual(set(DEFAULT_THRESHOLDS), set(expected))

    def test_thresholds_name_is_case_insensitive(self):
        self.assertEqual(
            thresholds_for("Edenor", DEFAULT_THRESHOLDS),
            thresholds_for("edenor", DEFAULT_THRESHOLDS),
        )

    def test_thresholds_from_custom_values(self):
        custom = thresholds_from({"x": 1000}, (0.01, 0.1, 0.5))
        self.assertEqual(
            (
                custom["x"].warn,
                custom["x"].high,
                custom["x"].disaster,
            ),
            (10, 100, 500),
        )
        self.assertEqual(thresholds_for("X", custom), custom["x"])

    def test_thresholds_round_up(self):
        custom = thresholds_from({"x": 3}, (0.5, 0.6, 0.7))
        self.assertEqual(
            (
                custom["x"].warn,
                custom["x"].high,
                custom["x"].disaster,
            ),
            (2, 2, 3),
        )

    def test_demand_thresholds(self):
        thresholds = Thresholds(0.10, 0.25, 0.50)
        self.assertEqual(thresholds.classify(0.09), "operational")
        self.assertEqual(thresholds.classify(0.10), "warn")
        self.assertEqual(thresholds.classify(0.25), "high")
        self.assertEqual(thresholds.classify(0.50), "disaster")
