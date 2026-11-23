import threading
import time
import unittest
from unittest.mock import patch

import tupa.core as core
from tupa.model import ART_TZ, SourceSpec, make_member, resolve_timezone
from tupa.sources import DEFAULT_SOURCES
from tupa.sources.common import DEFAULT_DEMAND_LEVELS, Thresholds

DEMAND_THRESHOLDS = Thresholds(*DEFAULT_DEMAND_LEVELS)


def spec(member):
    return SourceSpec(
        "enre_outages",
        member,
        url="https://example.test",
        label=member,
        docs_url="https://docs.example.test",
        empresa="TEST",
    )


def collect(*args, **kwargs):
    kwargs.setdefault("thresholds", {})
    kwargs.setdefault("demand_thresholds", DEMAND_THRESHOLDS)
    kwargs.setdefault("tz", ART_TZ)
    return core.collect(*args, **kwargs)


class CoreTests(unittest.TestCase):
    def test_collect_maps_failures_to_unknown_and_sorts_members(self):
        seen = []

        def good(spec, **kwargs):
            seen.append(kwargs["timeout"])
            return make_member("operational")

        def bad(spec, **kwargs):
            raise RuntimeError("down")

        sources = {"bad": spec("alpha"), "good": spec("zeta")}
        members = {"alpha": ("bad",), "zeta": ("good",)}
        with patch("tupa.core.fetch_source", side_effect=lambda s, **kw: (bad if s.member == "alpha" else good)(s, **kw)):
            status = collect(
                None,
                timeout=3.5,
                sources=sources,
                member_sources=members,
                tz=ART_TZ,
            )

        self.assertRegex(
            status["updated"],
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}-03:00",
        )
        self.assertEqual(list(status["members"]), ["alpha", "zeta"])
        self.assertEqual(status["members"]["alpha"]["status"], "unknown")
        self.assertIn("RuntimeError: down", status["members"]["alpha"]["comment"])
        self.assertEqual(status["members"]["zeta"]["status"], "operational")
        self.assertEqual(seen, [3.5])

    def test_collect_normalizes_all_timestamps_to_configured_timezone(self):
        zone = resolve_timezone("UTC")
        seen = []

        def source(spec, **kwargs):
            seen.append(kwargs["tz"])
            return make_member("operational", updated="2026-01-01T00:00:00Z")

        with patch("tupa.core.fetch_source", side_effect=source):
            status = collect(
                None,
                timeout=1.0,
                sources={"one": spec("one")},
                member_sources={"one": ("one",)},
                tz=zone,
            )
        self.assertEqual(seen, [zone])
        self.assertTrue(status["updated"].endswith("+00:00"))
        self.assertTrue(status["members"]["one"]["updated"].endswith("+00:00"))

    def test_collect_maps_timeout_to_spanish_unknown(self):
        def slow(spec, **kwargs):
            raise TimeoutError()

        sources = {"slow": spec("edenor")}
        members = {"edenor": ("slow",)}
        with patch("tupa.core.fetch_source", side_effect=slow):
            status = collect(None, timeout=10.0, sources=sources, member_sources=members)
        member = status["members"]["edenor"]
        self.assertEqual(member["status"], "unknown")
        self.assertIn("slow no respondió a tiempo después de 10 segundos", member["comment"])

    def test_collect_honours_selected_members(self):
        calls = []

        def one(spec, **kwargs):
            calls.append(spec.member)
            return make_member("operational")

        sources = {"one": spec("one"), "two": spec("two")}
        members = {"one": ("one",), "two": ("two",)}
        with patch("tupa.core.fetch_source", side_effect=one):
            status = collect(["two"], timeout=1.0, sources=sources, member_sources=members)

        self.assertEqual(calls, ["two"])
        self.assertEqual(list(status["members"]), ["two"])

    def test_empty_members_means_all_members(self):
        sources = {"one": spec("one"), "two": spec("two")}
        members = {"one": ("one",), "two": ("two",)}
        with patch("tupa.core.fetch_source", return_value=make_member("operational")) as fetch:
            status = collect([], timeout=1.0, sources=sources, member_sources=members)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(list(status["members"]), ["one", "two"])

    def test_timeout_marks_unknown_without_waiting_for_blocked_source(self):
        release = threading.Event()

        def blocked(spec, **kwargs):
            release.wait(5)
            return make_member("operational")

        sources = {"slow": spec("edenor")}
        members = {"edenor": ("slow",)}
        start = time.monotonic()
        try:
            with patch("tupa.core.fetch_source", side_effect=blocked):
                status = collect(None, timeout=0.1, sources=sources, member_sources=members)
        finally:
            release.set()
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(status["members"]["edenor"]["status"], "unknown")
        self.assertIn("no respondió", status["members"]["edenor"]["comment"])

    def test_collect_forwards_thresholds(self):
        received = []

        def one(spec, **kwargs):
            received.append(kwargs)
            return make_member("operational")

        thresholds = {"edenor": object()}
        demand_thresholds = object()
        sources = {"one": spec("edenor")}
        members = {"edenor": ("one",)}
        with patch("tupa.core.fetch_source", side_effect=one):
            collect(
                None,
                timeout=1.0,
                thresholds=thresholds,
                demand_thresholds=demand_thresholds,
                sources=sources,
                member_sources=members,
            )
        self.assertEqual(received[0]["thresholds"], thresholds)
        self.assertIs(received[0]["demand_thresholds"], demand_thresholds)

    def test_member_uses_worst_known_source(self):
        def enre(spec, **kwargs):
            return make_member("warn", comment="138 usuarios", updated="2026-01-01T00:00:00-03:00")

        def cammesa(spec, **kwargs):
            return make_member("disaster", comment="60% bajo", updated="2026-01-01T00:01:00-03:00")

        sources = {
            "enre": spec("edenor"),
            "cammesa": DEFAULT_SOURCES["cammesa_edenor"],
        }
        members = {"edenor": ("enre", "cammesa")}

        def fake(spec, **kwargs):
            return enre(spec, **kwargs) if spec.kind == "enre_outages" else cammesa(spec, **kwargs)

        with patch("tupa.core.fetch_source", side_effect=fake):
            status = collect(
                None, timeout=1.0, sources=sources, member_sources=members, tz=ART_TZ
            )

        member = status["members"]["edenor"]
        self.assertEqual(member["status"], "disaster")
        self.assertEqual(member["updated"], "2026-01-01T00:01:00-03:00")
        self.assertIn("cammesa: 60% bajo", member["comment"])
        self.assertIn("enre: 138 usuarios", member["comment"])

    def test_empty_member_is_unknown(self):
        with patch("tupa.core.fetch_source") as fetch:
            status = collect(
                None, timeout=1.0, sources={}, member_sources={"edenor": ()}
            )
        fetch.assert_not_called()
        self.assertEqual(status["members"]["edenor"]["status"], "unknown")

    def test_status_matches_cddl_shape(self):
        sources = {"one": spec("one")}
        members = {"one": ("one",)}
        with patch("tupa.core.fetch_source", return_value=make_member("warn", comment="detalle")):
            status = collect(None, timeout=1.0, sources=sources, member_sources=members)

        self.assertEqual(set(status), {"updated", "members"})
        member = status["members"]["one"]
        self.assertEqual(set(member), {"updated", "status", "comment"})
