import re
import unittest
from datetime import timedelta

from tupa.model import (
    AR_TZ,
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
        member = aggregate(results, tz=AR_TZ)
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
        self.assertEqual(str(resolve_timezone("UTC")), "UTC")
        self.assertEqual(resolve_timezone("-03:00").utcoffset(None), timedelta(hours=-3))
        self.assertEqual(str(resolve_timezone("system")), str(system_timezone()))
        with self.assertRaises(ValueError):
            resolve_timezone("Mars/Olympus")

    def test_aggregate_normalizes_mixed_offsets(self):
        results = [
            ("a", make_member("warn", updated="2026-11-21T20:00:00-03:00")),
            ("b", make_member("operational", updated="2026-11-21T21:00:00Z")),
        ]
        member = aggregate(results, tz=AR_TZ)
        self.assertEqual(member.updated, "2026-11-21T20:00:00-03:00")
