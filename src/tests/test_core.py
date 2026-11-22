import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tupa.core as core
from tupa.model import SourceSpec, make_member


def spec(name, member):
    return SourceSpec(name, "enre_outages", member, url="https://example.test", empresa="TEST")


class CoreTests(unittest.TestCase):
    def test_collect_maps_failures_to_unknown_and_sorts_members(self):
        seen = []

        def good(spec, **kwargs):
            seen.append(kwargs["timeout"])
            return make_member("operational")

        def bad(spec, **kwargs):
            raise RuntimeError("down")

        sources = {"bad": spec("bad", "alpha"), "good": spec("good", "zeta")}
        members = {"alpha": ("bad",), "zeta": ("good",)}
        with patch("tupa.core.fetch_source", side_effect=lambda s, **kw: (bad if s.name == "bad" else good)(s, **kw)):
            status = core.collect(
                None,
                timeout=3.5,
                sources=sources,
                member_sources=members,
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

    def test_collect_honours_selected_members(self):
        calls = []

        def one(spec, **kwargs):
            calls.append(spec.name)
            return make_member("operational")

        sources = {"one": spec("one", "one"), "two": spec("two", "two")}
        members = {"one": ("one",), "two": ("two",)}
        with patch("tupa.core.fetch_source", side_effect=one):
            status = core.collect(["two"], timeout=1.0, sources=sources, member_sources=members)

        self.assertEqual(calls, ["two"])
        self.assertEqual(list(status["members"]), ["two"])

    def test_collect_forwards_thresholds(self):
        received = []

        def one(spec, **kwargs):
            received.append(kwargs)
            return make_member("operational")

        thresholds = {"edenor": object()}
        demand_thresholds = object()
        sources = {"one": spec("one", "edenor")}
        members = {"edenor": ("one",)}
        with patch("tupa.core.fetch_source", side_effect=one):
            core.collect(
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
            "enre": spec("enre", "edenor"),
            "cammesa": SourceSpec("cammesa", "cammesa_demand", "edenor", region=1077),
        }
        members = {"edenor": ("enre", "cammesa")}

        def fake(spec, **kwargs):
            return enre(spec, **kwargs) if spec.kind == "enre_outages" else cammesa(spec, **kwargs)

        with patch("tupa.core.fetch_source", side_effect=fake):
            status = core.collect(None, timeout=1.0, sources=sources, member_sources=members)

        member = status["members"]["edenor"]
        self.assertEqual(member["status"], "disaster")
        self.assertEqual(member["updated"], "2026-01-01T00:01:00-03:00")
        self.assertIn("cammesa: 60% bajo", member["comment"])
        self.assertIn("enre: 138 usuarios", member["comment"])

    def test_unknown_is_ignored_unless_all_unknown(self):
        sources = {"a": spec("a", "edenor"), "b": spec("b", "edenor")}
        members = {"edenor": ("a", "b")}

        def fake(spec, **kwargs):
            if spec.name == "a":
                return make_member("operational")
            return make_member("unknown", comment="sin datos")

        with patch("tupa.core.fetch_source", side_effect=fake):
            status = core.collect(None, timeout=1.0, sources=sources, member_sources=members)
        self.assertEqual(status["members"]["edenor"]["status"], "operational")

        with patch("tupa.core.fetch_source", return_value=make_member("unknown")):
            status = core.collect(None, timeout=1.0, sources=sources, member_sources=members)
        self.assertEqual(status["members"]["edenor"]["status"], "unknown")

    def test_empty_member_is_unknown(self):
        with patch("tupa.core.fetch_source") as fetch:
            status = core.collect(
                None, timeout=1.0, sources={}, member_sources={"edenor": ()}
            )
        fetch.assert_not_called()
        self.assertEqual(status["members"]["edenor"]["status"], "unknown")

    def test_collect_from_config_forwards_settings(self):
        config = SimpleNamespace(
            members=("edenor",),
            timeout=7.0,
            thresholds={"edenor": object()},
            demand_thresholds=object(),
            sources={"one": spec("one", "edenor")},
            member_sources={"edenor": ("one",)},
        )
        with patch("tupa.core.collect", return_value={"ok": True}) as collect:
            result = core.collect_from_config(config)
        self.assertEqual(result, {"ok": True})
        collect.assert_called_once_with(
            ("edenor",),
            timeout=7.0,
            thresholds=config.thresholds,
            demand_thresholds=config.demand_thresholds,
            sources=config.sources,
            member_sources=config.member_sources,
        )

    def test_status_matches_cddl_shape(self):
        sources = {"one": spec("one", "one")}
        members = {"one": ("one",)}
        with patch("tupa.core.fetch_source", return_value=make_member("warn", comment="detalle")):
            status = core.collect(None, timeout=1.0, sources=sources, member_sources=members)

        self.assertEqual(set(status), {"updated", "members"})
        member = status["members"]["one"]
        self.assertEqual(set(member), {"updated", "status", "comment"})
