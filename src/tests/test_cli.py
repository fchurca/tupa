import io
import unittest
from unittest.mock import patch

import tupa.cli as cli


class FakeStdout:
    def __init__(self, buffer):
        self.buffer = buffer


def status(*states):
    return {
        "updated": "2026-01-01T00:00:00-03:00",
        "members": {
            str(index): {"updated": "2026-01-01T00:00:00-03:00", "status": state}
            for index, state in enumerate(states)
        },
    }


class CliTests(unittest.TestCase):
    def configure(self, mock_load, serve=False):
        mock_load.return_value.serve = serve
        mock_load.return_value.indent = 2
        mock_load.return_value.members = ()
        mock_load.return_value.timeout = 20.0
        mock_load.return_value.thresholds = {}
        mock_load.return_value.demand_thresholds = None
        mock_load.return_value.sources = {}
        mock_load.return_value.member_sources = {}

    def run_main(self, states, argv=None):
        stdout = io.BytesIO()
        with (
            patch("tupa.cli.load_config") as load,
            patch("tupa.cli.collect_from_config", return_value=status(*states)),
            patch("tupa.cli.sys.stdout", FakeStdout(stdout)),
        ):
            self.configure(load)
            code = cli.main(argv)
        return code, stdout.getvalue()

    def test_script_mode_writes_json_and_returns_zero(self):
        code, output = self.run_main(["operational", "unknown"])
        self.assertEqual(code, 0)
        self.assertIn(b'"status": "operational"', output)

    def test_all_unknown_returns_one(self):
        code, _ = self.run_main(["unknown", "unknown"])
        self.assertEqual(code, 1)

    def test_serve_mode_dispatches_to_daemon(self):
        with (
            patch("tupa.cli.load_config") as load,
            patch("tupa.cli.run_server") as serve,
        ):
            self.configure(load, serve=True)
            self.assertEqual(cli.main([]), 0)
        serve.assert_called_once_with(load.return_value)

    def test_broken_pipe_is_silent(self):
        class BrokenBuffer:
            def write(self, data):
                raise BrokenPipeError

            def flush(self):
                return None

        with (
            patch("tupa.cli.load_config") as load,
            patch("tupa.cli.collect_from_config", return_value=status("operational")),
            patch("tupa.cli.sys.stdout", FakeStdout(BrokenBuffer())),
        ):
            self.configure(load)
            self.assertEqual(cli.main([]), 0)
