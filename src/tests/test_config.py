import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from tupa.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.json = Path(self.tmp.name) / "tupa.json"

    def write_json(self, data):
        self.json.write_text(json.dumps(data), encoding="utf-8")
        return str(self.json)

    def clean_env(self, **values):
        return patch.dict(os.environ, values, clear=True)

    def quiet_error(self):
        return contextlib.redirect_stderr(io.StringIO())

    def test_defaults(self):
        with self.clean_env(), patch("sys.argv", ["tupa"]):
            config = load_config([])
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertEqual(config.member_sources["edenor"], ("cammesa_edenor", "enre_edenor"))
        self.assertEqual(set(config.sources), {"enre_edenor", "enre_edesur", "cammesa_edenor", "cammesa_edesur"})
        self.assertFalse(config.serve)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertEqual((config.timeout, config.ttl, config.indent), (10.0, 60.0, 2))
        self.assertFalse(config.strict)
        self.assertEqual(config.customers["edenor"], 3_400_000)
        self.assertEqual(config.levels, (0.0001, 0.005, 0.05))
        self.assertEqual(config.demand_levels, (0.15, 0.30, 0.50))
        self.assertEqual(config.thresholds["edenor"].warn, 340)
        self.assertEqual(config.thresholds["edesur"].high, 13_500)
        self.assertEqual(config.demand_thresholds.disaster, 0.50)

    def test_precedence_args_over_env_over_json(self):
        path = self.write_json({"selected_members": ["edenor"], "host": "json.example", "port": 9000})
        env = {
            "TUPA_MEMBERS": "edesur",
            "TUPA_HOST": "env.example",
            "TUPA_PORT": "9100",
        }
        argv = ["--config", path, "--host", "args.example", "--member", "edenor"]
        with self.clean_env(**env):
            config = load_config(argv)
        self.assertEqual(config.members, ("edenor",))
        self.assertEqual(config.host, "args.example")
        self.assertEqual(config.port, 9100)

    def test_members_env_overrides_json_and_sources_alias(self):
        path = self.write_json({"selected_members": ["edenor"]})
        with self.clean_env(TUPA_CONFIG=path, TUPA_SOURCES="edesur"):
            config = load_config([])
        self.assertEqual(config.members, ("edesur",))

        env = {"TUPA_CONFIG": path, "TUPA_MEMBERS": "edenor", "TUPA_SOURCES": "edesur"}
        with self.clean_env(**env):
            config = load_config([])
        self.assertEqual(config.members, ("edenor",))

    def test_env_over_json_and_serve_flag(self):
        path = self.write_json({"selected_members": ["edenor"], "host": "json.example", "serve": False})
        with self.clean_env(TUPA_CONFIG=path, TUPA_HOST="env.example", TUPA_SERVE="true"):
            config = load_config([])
        self.assertEqual(config.config_path, path)
        self.assertEqual(config.members, ("edenor",))
        self.assertEqual(config.host, "env.example")
        self.assertTrue(config.serve)

    def test_json_only(self):
        path = self.write_json({"selected_members": ["edenor", "edesur"], "ttl": 15})
        with self.clean_env():
            config = load_config(["--config", path])
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertEqual(config.ttl, 15.0)

    def test_json_members_and_sources(self):
        path = self.write_json(
            {
                "members": {"edenor": ["enre_edenor", "cammesa_custom"]},
                "sources": {
                    "cammesa_custom": {
                        "kind": "cammesa_demand",
                        "member": "edenor",
                        "region": 9999,
                    }
                },
            }
        )
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.member_sources["edenor"], ("enre_edenor", "cammesa_custom"))
        self.assertEqual(config.sources["cammesa_custom"].region, 9999)
        self.assertIn("id_region=9999", config.sources["cammesa_custom"].url)

    def test_customers_merge_with_defaults(self):
        with self.clean_env(), patch("sys.argv", ["tupa"]):
            config = load_config(["--customers", "edenor=3500000"])
        self.assertEqual(config.customers["edenor"], 3_500_000)
        self.assertEqual(config.customers["edesur"], 2_700_000)
        self.assertEqual(config.thresholds["edenor"].warn, 350)

    def test_customers_from_json(self):
        path = self.write_json({"customers": {"edelap": 500000, "edenor": 4000000}})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.customers["edelap"], 500_000)
        self.assertEqual(config.thresholds["edelap"].disaster, 25_000)

    def test_levels_from_json_and_env(self):
        path = self.write_json({"levels": [0.001, 0.01, 0.1]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.levels, (0.001, 0.01, 0.1))
        self.assertEqual(config.thresholds["edenor"].warn, 3_400)

        with self.clean_env(TUPA_LEVELS="0.0002,0.002,0.02"):
            config = load_config([])
        self.assertEqual(config.levels, (0.0002, 0.002, 0.02))
        self.assertEqual(config.thresholds["edelap"].high, 800)

    def test_demand_levels(self):
        path = self.write_json({"demand_levels": [0.05, 0.15, 0.30]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.demand_levels, (0.05, 0.15, 0.30))
        self.assertEqual(config.demand_thresholds.high, 0.15)
        with self.clean_env(TUPA_DEMAND_LEVELS="0.01,0.02,0.03"):
            config = load_config(["--demand-levels", "0.02,0.04,0.06"])
        self.assertEqual(config.demand_levels, (0.02, 0.04, 0.06))

    def test_invalid_thresholds_exits_two(self):
        for value in ("0.1,0.01,0.001", "0.1,0.2", "0,0.1,0.2", "0.1,0.2,2"):
            with (
                self.subTest(value=value),
                self.clean_env(),
                self.quiet_error(),
                self.assertRaises(SystemExit) as ctx,
            ):
                load_config(["--levels", value])
            self.assertEqual(ctx.exception.code, 2)
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--customers", "edenor=abc"])
        self.assertEqual(ctx.exception.code, 2)
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--demand-levels", "0.5,0.1,0.2"])
        self.assertEqual(ctx.exception.code, 2)

    def test_member_deduplication(self):
        with self.clean_env():
            config = load_config(["--member", "edenor", "edenor"])
        self.assertEqual(config.members, ("edenor",))

    def test_empty_selections_fall_back_to_all(self):
        path = self.write_json({"selected_members": []})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.members, ("edenor", "edesur"))

        with self.clean_env(TUPA_MEMBERS="", TUPA_SOURCES=""):
            config = load_config([])
        self.assertEqual(config.members, ("edenor", "edesur"))

        with self.clean_env(TUPA_MEMBERS="   "):
            config = load_config([])
        self.assertEqual(config.members, ("edenor", "edesur"))

        with self.clean_env(TUPA_MEMBERS="", TUPA_SOURCES="edenor"):
            config = load_config([])
        self.assertEqual(config.members, ("edenor",))

    def test_empty_member_sources_uses_all_sources_of_that_member(self):
        path = self.write_json({"members": {"edenor": []}})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertCountEqual(
            config.member_sources["edenor"], ("cammesa_edenor", "enre_edenor")
        )

    def test_enre_member_needs_customer_baseline(self):
        data = {
            "selected_members": ["transener"],
            "members": {"transener": ["enre_custom"]},
            "sources": {
                "enre_custom": {
                    "kind": "enre_outages",
                    "member": "transener",
                    "url": "https://example.test/data.js",
                    "empresa": "TRANSENER",
                }
            },
        }
        path = self.write_json(data)
        with self.clean_env(TUPA_CONFIG=path), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config([])
        self.assertEqual(ctx.exception.code, 2)

        data["customers"] = {"transener": 1000}
        path = self.write_json(data)
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.thresholds["transener"].warn, 1)

    def test_timezone_precedence_and_default(self):
        with self.clean_env(TZ="America/Argentina/Buenos_Aires"):
            config = load_config([])
        self.assertEqual(str(config.tz), "America/Argentina/Buenos_Aires")

        with self.clean_env(TUPA_TZ="-03:00"):
            config = load_config([])
        self.assertEqual(config.tz.utcoffset(None), timedelta(hours=-3))

        with self.clean_env(TUPA_TZ="UTC"), patch("sys.argv", ["tupa"]):
            config = load_config(["--tz", "+02:00"])
        self.assertEqual(config.tz.utcoffset(None), timedelta(hours=2))

        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--tz", "Mars/Olympus"])
        self.assertEqual(ctx.exception.code, 2)

    def test_string_boolean_strict_with_unknown_key(self):
        path = self.write_json({"strict": "false", "nope": 1})
        with self.clean_env(TUPA_CONFIG=path), self.assertLogs("tupa.config", level="WARNING"):
            config = load_config([])
        self.assertFalse(config.strict)
        self.assertEqual(config.ttl, 60.0)

    def test_source_url_must_be_http(self):
        path = self.write_json({"sources": {"enre_edenor": {"url": "javascript:alert(1)"}}})
        with self.clean_env(TUPA_CONFIG=path), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config([])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_member_exits_two(self):
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["nope"])
        self.assertEqual(ctx.exception.code, 2)

    def test_source_assigned_to_wrong_member_exits_two(self):
        path = self.write_json(
            {
                "members": {"edenor": ["cammesa_custom"]},
                "sources": {
                    "cammesa_custom": {
                        "kind": "cammesa_demand",
                        "member": "edesur",
                        "region": 1,
                    }
                },
            }
        )
        with self.clean_env(TUPA_CONFIG=path), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config([])
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_number_exits_two(self):
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--port", "70000"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_config_uses_defaults(self):
        with self.clean_env(), self.assertLogs("tupa.config", level="INFO") as logs:
            config = load_config(["--config", str(self.json)])
        self.assertEqual(config.config_path, str(self.json))
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertEqual(config.ttl, 60.0)
        self.assertFalse(config.strict)
        self.assertTrue(any("using defaults" in m for m in logs.output))

    def test_missing_config_from_env_uses_defaults(self):
        with self.clean_env(TUPA_CONFIG=str(self.json)), self.assertLogs("tupa.config", level="INFO"):
            config = load_config([])
        self.assertEqual(config.config_path, str(self.json))
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertEqual(config.ttl, 60.0)

    def test_invalid_json_warns_and_uses_defaults(self):
        self.json.write_text("not json", encoding="utf-8")
        with self.clean_env(), self.assertLogs("tupa.config", level="WARNING") as logs:
            config = load_config(["--config", str(self.json)])
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertTrue(any("using defaults" in m for m in logs.output))

    def test_invalid_json_strict_exits_two(self):
        self.json.write_text("not json", encoding="utf-8")
        with (
            self.clean_env(),
            self.quiet_error(),
            self.assertLogs("tupa.config", level="ERROR"),
            self.assertRaises(SystemExit) as ctx,
        ):
            load_config(["--config", str(self.json), "--strict"])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_json_key_warns_and_ignores(self):
        path = self.write_json({"nope": 1, "ttl": 15})
        with self.clean_env(), self.assertLogs("tupa.config", level="WARNING") as logs:
            config = load_config(["--config", path])
        self.assertEqual(config.ttl, 15.0)
        self.assertTrue(any("unknown config keys" in m for m in logs.output))

    def test_unknown_json_key_strict_exits_two(self):
        path = self.write_json({"nope": 1})
        with (
            self.clean_env(),
            self.quiet_error(),
            self.assertLogs("tupa.config", level="ERROR"),
            self.assertRaises(SystemExit) as ctx,
        ):
            load_config(["--config", path, "--strict"])
        self.assertEqual(ctx.exception.code, 2)

    def test_strict_from_env_and_json(self):
        self.json.write_text("not json", encoding="utf-8")
        with self.clean_env(TUPA_CONFIG=str(self.json), TUPA_STRICT="true"), self.quiet_error(), self.assertRaises(SystemExit):
            load_config([])
        path = self.write_json({"strict": True})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertTrue(config.strict)
        with self.clean_env(TUPA_STRICT="true"):
            config = load_config([])
        self.assertTrue(config.strict)
