import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tupa.config import load_config

ALL_MEMBERS = ("edenor", "edesur", "edelap")
ALL_SOURCE_COUNT = 6


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.tmp.name)
        self.json = Path(self.tmp.name) / "tupa.json"

    def write_json(self, data):
        self.json.write_text(json.dumps(data), encoding="utf-8")
        return str(self.json)

    def clean_env(self, **values):
        return patch.dict(os.environ, values, clear=True)

    def quiet_error(self):
        return contextlib.redirect_stderr(io.StringIO())

    def assert_exit_two(self, argv, *, env=None):
        with (
            self.clean_env(**(env or {})),
            self.quiet_error(),
            self.assertRaises(SystemExit) as ctx,
        ):
            load_config(argv)
        self.assertEqual(ctx.exception.code, 2)

    def test_defaults(self):
        with self.clean_env():
            config = load_config([])
        self.assertEqual(config.members, ALL_MEMBERS)
        self.assertEqual(config.member_sources["edenor"], ("cammesa_edenor", "enre_edenor"))
        self.assertEqual(
            config.member_sources["edelap"], ("cammesa_edelap", "oceba_edelap")
        )
        self.assertEqual(
            set(config.sources),
            {
                "enre_edenor",
                "enre_edesur",
                "cammesa_edenor",
                "cammesa_edesur",
                "cammesa_edelap",
                "oceba_edelap",
            },
        )
        self.assertFalse(config.serve)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertEqual((config.timeout, config.ttl, config.indent), (20.0, 60.0, 2))
        self.assertEqual(str(config.tz), "America/Argentina/Buenos_Aires")
        self.assertEqual(config.thresholds["edenor"].warn, 340)
        self.assertEqual(config.thresholds["edesur"].high, 13_500)
        self.assertEqual(config.demand_thresholds.disaster, 0.50)

    def test_precedence_args_over_env_over_json(self):
        path = self.write_json({"selected_members": ["edenor"], "host": "json.example", "port": 9000})
        env = {"TUPA_MEMBERS": "edesur", "TUPA_HOST": "env.example", "TUPA_PORT": "9100"}
        argv = ["--config", path, "--host", "args.example", "--member", "edenor"]
        with self.clean_env(**env):
            config = load_config(argv)
        self.assertEqual(config.members, ("edenor",))
        self.assertEqual(config.host, "args.example")
        self.assertEqual(config.port, 9100)

    def test_members_env_overrides_json(self):
        path = self.write_json({"selected_members": ["edenor"]})
        with self.clean_env(TUPA_CONFIG=path, TUPA_MEMBERS="edesur"):
            config = load_config([])
        self.assertEqual(config.members, ("edesur",))

    def test_env_over_json_and_serve_flag(self):
        path = self.write_json({"selected_members": ["edenor"], "host": "json.example", "serve": False})
        with self.clean_env(TUPA_CONFIG=path, TUPA_HOST="env.example", TUPA_SERVE="true"):
            config = load_config([])
        self.assertEqual(config.members, ("edenor",))
        self.assertEqual(config.host, "env.example")
        self.assertTrue(config.serve)

    def test_serve_from_json(self):
        path = self.write_json({"serve": True})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertTrue(config.serve)

    def test_once_forces_serve_false(self):
        path = self.write_json({"serve": True})
        with self.clean_env(TUPA_CONFIG=path, TUPA_SERVE="true"):
            config = load_config(["--serve", "--once"])
        self.assertFalse(config.serve)

    def test_json_only(self):
        path = self.write_json({"selected_members": ["edenor", "edesur"], "ttl": 15})
        with self.clean_env():
            config = load_config(["--config", path])
        self.assertEqual(config.members, ("edenor", "edesur"))
        self.assertEqual(config.ttl, 15.0)

    def test_members_and_sources_are_enable_lists(self):
        path = self.write_json({"members": ["edenor"], "sources": ["enre_edenor"]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.members, ("edenor",))
        self.assertEqual(set(config.sources), {"enre_edenor"})
        self.assertEqual(config.member_sources, {"edenor": ("enre_edenor",)})
        self.assertEqual(
            config.sources["enre_edenor"].url,
            "https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
        )

    def test_disabling_source_removes_it_from_members(self):
        path = self.write_json({"sources": ["cammesa_edenor", "cammesa_edesur", "enre_edesur"]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.member_sources["edenor"], ("cammesa_edenor",))
        self.assertEqual(config.member_sources["edesur"], ("cammesa_edesur", "enre_edesur"))
        self.assertEqual(config.member_sources["edelap"], ())

    def test_custom_source_definitions_are_rejected(self):
        path = self.write_json(
            {
                "sources": {
                    "enre_custom": {
                        "kind": "enre_outages",
                        "member": "edenor",
                        "url": "https://example.test/data.js",
                        "empresa": "EDENOR",
                    }
                }
            }
        )
        self.assert_exit_two([], env={"TUPA_CONFIG": path})

    def test_unknown_enabled_member_or_source_exits_two(self):
        for key, value in (("members", ["nope"]), ("sources", ["nope"])):
            with self.subTest(key=key):
                self.assert_exit_two(["--config", self.write_json({key: value})])

    def test_selecting_disabled_member_exits_two(self):
        path = self.write_json({"members": ["edenor"], "selected_members": ["edesur"]})
        self.assert_exit_two([], env={"TUPA_CONFIG": path})

    def test_customers_merge_with_defaults(self):
        with self.clean_env():
            config = load_config(["--customers", "edenor=3500000"])
        self.assertEqual(config.thresholds["edenor"].warn, 350)
        self.assertEqual(config.thresholds["edesur"].warn, 270)

    def test_customers_from_json(self):
        path = self.write_json({"customers": {"edelap": 500000, "edenor": 4000000}})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.thresholds["edelap"].disaster, 25_000)
        self.assertEqual(config.thresholds["edenor"].disaster, 200_000)

    def test_levels_from_json_and_env(self):
        path = self.write_json({"levels": [0.001, 0.01, 0.1]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.thresholds["edenor"].warn, 3_400)

        with self.clean_env(TUPA_LEVELS="0.0002,0.002,0.02"):
            config = load_config([])
        self.assertEqual(config.thresholds["edelap"].high, 800)

    def test_demand_levels(self):
        path = self.write_json({"demand_levels": [0.05, 0.15, 0.30]})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.demand_thresholds.high, 0.15)
        with self.clean_env(TUPA_DEMAND_LEVELS="0.01,0.02,0.03"):
            config = load_config(["--demand-levels", "0.02,0.04,0.06"])
        self.assertEqual(config.demand_thresholds.high, 0.04)

    def test_invalid_thresholds_exits_two(self):
        for value in ("0.1,0.01,0.001", "0.1,0.2", "0,0.1,0.2", "0.1,0.2,2"):
            with self.subTest(value=value):
                self.assert_exit_two(["--levels", value])
        self.assert_exit_two(["--customers", "edenor=abc"])
        self.assert_exit_two(["--demand-levels", "0.5,0.1,0.2"])

    def test_member_deduplication(self):
        with self.clean_env():
            config = load_config(["--member", "edenor", "--member", "edenor"])
        self.assertEqual(config.members, ("edenor",))

    def test_empty_selections_fall_back_to_all(self):
        path = self.write_json({"selected_members": []})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.members, ALL_MEMBERS)

        for value in ("", "   "):
            with self.subTest(value=value), self.clean_env(TUPA_MEMBERS=value):
                config = load_config([])
            self.assertEqual(config.members, ALL_MEMBERS)

    def test_empty_enable_lists_mean_all(self):
        path = self.write_json({"members": [], "sources": []})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(set(config.members), set(ALL_MEMBERS))
        self.assertEqual(len(config.sources), ALL_SOURCE_COUNT)

    def test_timezone_precedence_and_default(self):
        with self.clean_env():
            config = load_config([])
        self.assertEqual(str(config.tz), "America/Argentina/Buenos_Aires")

        with self.clean_env(TZ="UTC"):
            config = load_config([])
        self.assertEqual(str(config.tz), "America/Argentina/Buenos_Aires")

        with self.clean_env(TZ="UTC"):
            config = load_config(["--tz", "system"])
        self.assertEqual(str(config.tz), "UTC")

        path = self.write_json({"tz": "local"})
        with self.clean_env(TZ="UTC", TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(str(config.tz), "UTC")

        self.assert_exit_two([], env={"TUPA_TZ": "-03:00"})
        self.assert_exit_two(["--tz", "+02:00"], env={"TUPA_TZ": "UTC"})

        path = self.write_json({"tz": "UTC"})
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(str(config.tz), "UTC")

        self.assert_exit_two(["--tz", "Mars/Olympus"])

    def test_default_config_json_in_working_directory(self):
        Path("config.json").write_text(json.dumps({"ttl": 15}), encoding="utf-8")
        with self.clean_env():
            config = load_config([])
        self.assertEqual(config.ttl, 15.0)

    def test_invalid_number_exits_two(self):
        self.assert_exit_two(["--port", "70000"])

    def test_missing_config_uses_defaults(self):
        with self.clean_env(), self.assertLogs("tupa.config", level="INFO") as logs:
            config = load_config(["--config", str(self.json)])
        self.assertEqual(config.members, ALL_MEMBERS)
        self.assertEqual(config.ttl, 60.0)
        self.assertTrue(any("using defaults" in m for m in logs.output))

    def test_missing_config_from_env_uses_defaults(self):
        with self.clean_env(TUPA_CONFIG=str(self.json)), self.assertLogs("tupa.config", level="INFO"):
            config = load_config([])
        self.assertEqual(config.members, ALL_MEMBERS)
        self.assertEqual(config.ttl, 60.0)

    def test_invalid_json_warns_and_uses_defaults(self):
        self.json.write_text("not json", encoding="utf-8")
        with self.clean_env(), self.assertLogs("tupa.config", level="WARNING") as logs:
            config = load_config(["--config", str(self.json)])
        self.assertEqual(config.members, ALL_MEMBERS)
        self.assertTrue(any("using defaults" in m for m in logs.output))

    def test_unknown_json_key_warns_and_ignores(self):
        path = self.write_json({"nope": 1, "ttl": 15})
        with self.clean_env(), self.assertLogs("tupa.config", level="WARNING") as logs:
            config = load_config(["--config", path])
        self.assertEqual(config.ttl, 15.0)
        self.assertTrue(any("unknown config keys" in m for m in logs.output))

    def test_unknown_member_exits_two(self):
        self.assert_exit_two(["--member", "nope"])
