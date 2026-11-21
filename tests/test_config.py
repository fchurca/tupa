import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tupa.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ini = Path(self.tmp.name) / "tupa.ini"

    def write_ini(self, text):
        self.ini.write_text(text, encoding="utf-8")
        return str(self.ini)

    def clean_env(self, **values):
        return patch.dict(os.environ, values, clear=True)

    def quiet_error(self):
        return contextlib.redirect_stderr(io.StringIO())

    def test_defaults(self):
        with self.clean_env(), patch("sys.argv", ["tupa"]):
            config = load_config([])
        self.assertEqual(config.sources, ())
        self.assertFalse(config.serve)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertEqual((config.timeout, config.ttl, config.indent), (10.0, 60.0, 2))
        self.assertEqual(
            config.customers,
            {"edenor": 3_400_000, "edesur": 2_700_000, "edelap": 400_000},
        )
        self.assertEqual(config.levels, (0.0001, 0.005, 0.05))
        self.assertEqual(config.thresholds["edenor"].warn, 340)
        self.assertEqual(config.thresholds["edesur"].high, 13_500)
        self.assertEqual(config.thresholds["edelap"].disaster, 20_000)

    def test_precedence_args_over_env_over_ini(self):
        path = self.write_ini(
            "[tupa]\nsources = edenor\nhost = ini.example\nport = 9000\n"
        )
        env = {
            "TUPA_SOURCES": "edesur",
            "TUPA_HOST": "env.example",
            "TUPA_PORT": "9100",
        }
        argv = ["--config", path, "--host", "args.example", "--source", "edenor"]
        with self.clean_env(**env):
            config = load_config(argv)
        self.assertEqual(config.sources, ("edenor",))
        self.assertEqual(config.host, "args.example")
        self.assertEqual(config.port, 9100)

    def test_env_over_ini_and_serve_flag(self):
        path = self.write_ini("[tupa]\nsources=edenor\nhost=ini.example\nserve=no\n")
        with self.clean_env(TUPA_CONFIG=path, TUPA_HOST="env.example", TUPA_SERVE="true"):
            config = load_config([])
        self.assertEqual(config.config_path, path)
        self.assertEqual(config.sources, ("edenor",))
        self.assertEqual(config.host, "env.example")
        self.assertTrue(config.serve)

    def test_ini_only(self):
        path = self.write_ini("[tupa]\nsources=edenor, edesur\nttl=15\n")
        with self.clean_env():
            config = load_config(["--config", path])
        self.assertEqual(config.sources, ("edenor", "edesur"))
        self.assertEqual(config.ttl, 15.0)

    def test_customers_merge_with_defaults(self):
        with self.clean_env(), patch("sys.argv", ["tupa"]):
            config = load_config(["--customers", "edenor=3500000"])
        self.assertEqual(config.customers["edenor"], 3_500_000)
        self.assertEqual(config.customers["edesur"], 2_700_000)
        self.assertEqual(config.thresholds["edenor"].warn, 350)

    def test_customers_from_ini(self):
        path = self.write_ini("[tupa]\ncustomers=edelap=500000, edenor=4000000\n")
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.customers["edelap"], 500_000)
        self.assertEqual(config.thresholds["edelap"].disaster, 25_000)

    def test_levels_from_ini_and_env(self):
        path = self.write_ini("[tupa]\nlevels=0.001,0.01,0.1\n")
        with self.clean_env(TUPA_CONFIG=path):
            config = load_config([])
        self.assertEqual(config.levels, (0.001, 0.01, 0.1))
        self.assertEqual(config.thresholds["edenor"].warn, 3_400)

        with self.clean_env(TUPA_LEVELS="0.0002,0.002,0.02"):
            config = load_config([])
        self.assertEqual(config.levels, (0.0002, 0.002, 0.02))
        self.assertEqual(config.thresholds["edelap"].high, 800)

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

    def test_source_deduplication(self):
        with self.clean_env():
            config = load_config(["--source", "edenor", "edenor"])
        self.assertEqual(config.sources, ("edenor",))

    def test_unknown_source_exits_two(self):
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["nope"])
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_number_exits_two(self):
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--port", "70000"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_config_exits_two(self):
        with self.clean_env(), self.quiet_error(), self.assertRaises(SystemExit) as ctx:
            load_config(["--config", str(self.ini)])
        self.assertEqual(ctx.exception.code, 2)
