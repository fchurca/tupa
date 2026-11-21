import unittest
from unittest.mock import patch

import tupa.core as core
from tupa.model import make_member


class CoreTests(unittest.TestCase):
    def test_collect_maps_failures_to_unknown_and_sorts_members(self):
        seen = []

        def good(timeout, thresholds=None):
            seen.append(timeout)
            return make_member("operational")

        def bad(timeout, thresholds=None):
            raise RuntimeError("down")

        with patch.dict(core.SOURCES, {"zeta": good, "alpha": bad}, clear=True):
            status = core.collect(None, timeout=3.5)

        self.assertRegex(
            status["updated"],
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}-03:00",
        )
        self.assertEqual(list(status["members"]), ["alpha", "zeta"])
        self.assertEqual(status["members"]["alpha"]["status"], "unknown")
        self.assertIn("RuntimeError: down", status["members"]["alpha"]["comment"])
        self.assertEqual(status["members"]["zeta"]["status"], "operational")
        self.assertEqual(seen, [3.5])

    def test_collect_honours_selected_names(self):
        calls = []

        def one(timeout, thresholds=None):
            calls.append("one")
            return make_member("operational")

        def two(timeout, thresholds=None):
            calls.append("two")
            return make_member("operational")

        with patch.dict(core.SOURCES, {"one": one, "two": two}, clear=True):
            status = core.collect(["two"], timeout=1.0)

        self.assertEqual(calls, ["two"])
        self.assertEqual(list(status["members"]), ["two"])

    def test_collect_forwards_thresholds(self):
        received = []

        def one(timeout, thresholds=None):
            received.append(thresholds)
            return make_member("operational")

        thresholds = {"one": object()}
        with patch.dict(core.SOURCES, {"one": one}, clear=True):
            core.collect(None, timeout=1.0, thresholds=thresholds)
        self.assertEqual(received, [thresholds])

    def test_status_matches_cddl_shape(self):
        def one(timeout, thresholds=None):
            return make_member("warn", comment="detalle")

        with patch.dict(core.SOURCES, {"one": one}, clear=True):
            status = core.collect(None, timeout=1.0)

        self.assertEqual(set(status), {"updated", "members"})
        member = status["members"]["one"]
        self.assertEqual(set(member), {"updated", "status", "comment"})
