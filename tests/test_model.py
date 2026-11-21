import re
import unittest

from tupa.model import STATUSES, maintenance, make_member, now_rfc3339, unknown_member

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

    def test_maintenance_downgrades_one_stage(self):
        for before, after in (
            ("disaster", "high"),
            ("high", "warn"),
            ("warn", "operational"),
            ("operational", "operational"),
            ("unknown", "unknown"),
        ):
            with self.subTest(before=before):
                self.assertEqual(maintenance(before), after)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            make_member("green")
        self.assertTrue({"operational", "warn", "high", "disaster", "unknown"} <= STATUSES)
