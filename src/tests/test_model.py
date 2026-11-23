import os
import re
import unittest
from unittest.mock import patch

from tupa.model import (
    ART_TZ,
    STATUSES,
    aggregate,
    make_member,
    now_rfc3339,
    resolve_timezone,
    system_timezone,
    unknown_member,
)

TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(Z|[+-][0-9]{2}:[0-9]{2})\Z"
)


class ModelTests(unittest.TestCase):
    def test_timestamp_matches_cddl(self):
        self.assertRegex(now_rfc3339(), TIMESTAMP)

    def test_member_to_json_omits_missing_comment(self):
        member = make_member("operational", updated="2026-01-01T00:00:00-03:00")
        self.assertEqual(
            member.to_json(),
            {"updated": "2026-01-01T00:00:00-03:00", "status": "operational"},
        )

    def test_member_to_json_includes_comment(self):
        member = make_member("warn", comment="cortes", updated="2026-01-01T00:00:00Z")
        self.assertEqual(member.to_json()["comment"], "cortes")
        self.assertEqual(member.updated, "2025-12-31T21:00:00-03:00")

    def test_make_member_normalizes_to_configured_timezone(self):
        member = make_member(
            "operational", updated="2026-01-01T00:00:00-03:00", tz=resolve_timezone("UTC")
        )
        self.assertEqual(member.updated, "2026-01-01T03:00:00+00:00")

    def test_unknown_member(self):
        member = unknown_member("timeout")
        self.assertEqual(member.status, "unknown")
        self.assertIn("timeout", member.comment)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            make_member("green")
        self.assertTrue({"operational", "warn", "high", "disaster", "unknown"} <= STATUSES)

    def test_aggregate_uses_worst_known_status(self):
        results = [
            ("enre", make_member("warn", comment="cortes", updated="2026-01-01T00:00:00-03:00")),
            ("cammesa", make_member("operational", updated="2026-01-01T00:01:00-03:00")),
            (
                "extra",
                make_member(
                    "unknown",
                    comment="sin datos",
                    updated="2026-01-01T00:00:30-03:00",
                ),
            ),
        ]
        member = aggregate(results, tz=ART_TZ)
        self.assertEqual(member.status, "warn")
        self.assertEqual(member.updated, "2026-01-01T00:01:00-03:00")
        self.assertIn("enre: cortes", member.comment)
        self.assertIn("extra: sin datos", member.comment)

    def test_aggregate_all_unknown_is_unknown(self):
        member = aggregate(
            [
                ("one", make_member("unknown")),
                ("two", make_member("unknown")),
            ]
        )
        self.assertEqual(member.status, "unknown")

    def test_aggregate_without_results_is_unknown(self):
        self.assertEqual(aggregate([]).status, "unknown")

    def test_resolve_timezone(self):
        self.assertEqual(str(resolve_timezone()), "America/Argentina/Buenos_Aires")
        self.assertEqual(str(resolve_timezone("")), "America/Argentina/Buenos_Aires")
        self.assertEqual(str(resolve_timezone("UTC")), "UTC")
        self.assertEqual(str(resolve_timezone("system")), str(system_timezone()))
        self.assertEqual(str(resolve_timezone("local")), str(system_timezone()))
        for value in ("-03:00", "Mars/Olympus"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_timezone(value)

    def test_resolve_timezone_system_uses_tz(self):
        with patch.dict(os.environ, {"TZ": "UTC"}, clear=True):
            self.assertEqual(str(resolve_timezone("system")), "UTC")
            self.assertEqual(str(resolve_timezone("local")), "UTC")

    def test_system_timezone_uses_tz_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(str(system_timezone()), "America/Argentina/Buenos_Aires")
        with patch.dict(os.environ, {"TZ": "UTC"}, clear=True):
            self.assertEqual(str(system_timezone()), "UTC")
        with patch.dict(os.environ, {"TZ": "Mars/Olympus"}, clear=True):
            self.assertEqual(str(system_timezone()), "America/Argentina/Buenos_Aires")

    def test_aggregate_normalizes_mixed_offsets(self):
        results = [
            ("a", make_member("warn", updated="2026-11-21T20:00:00-03:00")),
            ("b", make_member("operational", updated="2026-11-21T21:00:00Z")),
        ]
        member = aggregate(results, tz=ART_TZ)
        self.assertEqual(member.updated, "2026-11-21T20:00:00-03:00")

    def test_aggregate_ignores_failed_source_timestamp(self):
        results = [
            (
                "cammesa",
                make_member("warn", updated="2026-11-22T13:55:00-03:00"),
            ),
            (
                "enre",
                make_member(
                    "unknown",
                    comment="source unavailable: timeout",
                    updated="2026-11-22T14:05:00-03:00",
                ),
            ),
        ]
        member = aggregate(results, tz=ART_TZ)
        self.assertEqual(member.updated, "2026-11-22T13:55:00-03:00")
