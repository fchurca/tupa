from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .model import Member, SourceSpec, aggregate, now_rfc3339, unknown_member
from .sources import DEFAULT_MEMBERS, DEFAULT_SOURCES, fetch_source
from .sources.common import DEFAULT_DEMAND_THRESHOLDS, DemandThresholds, Thresholds

log = logging.getLogger(__name__)


def collect(
    members: Sequence[str] | None,
    *,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
    demand_thresholds: DemandThresholds | None = None,
    sources: Mapping[str, SourceSpec] | None = None,
    member_sources: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    source_defs = DEFAULT_SOURCES if sources is None else sources
    definition = DEFAULT_MEMBERS if member_sources is None else member_sources
    selected = tuple(members) if members else tuple(definition)
    for name in selected:
        if name not in definition:
            raise ValueError(f"unknown member: {name!r}")

    tasks = [
        (member, source_id, source_defs[source_id])
        for member in selected
        for source_id in definition[member]
    ]
    updated = now_rfc3339()
    groups: dict[str, list[tuple[str, Member]]] = {name: [] for name in selected}
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {
            pool.submit(
                fetch_source,
                spec,
                timeout=timeout,
                thresholds=thresholds,
                demand_thresholds=demand_thresholds or DEFAULT_DEMAND_THRESHOLDS,
            ): (member, source_id)
            for member, source_id, spec in tasks
        }
        for future in as_completed(futures):
            member, source_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                log.warning("source %s failed: %s: %s", source_id, type(exc).__name__, exc)
                result = unknown_member(f"{type(exc).__name__}: {exc}")
            groups[member].append((source_id, result))

    resolved = {
        member: aggregate(sorted(results)).to_json()
        for member, results in groups.items()
    }
    return {"updated": updated, "members": dict(sorted(resolved.items()))}
