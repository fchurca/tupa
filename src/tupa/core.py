from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import Config
from .model import Member, SourceSpec, aggregate, now_rfc3339, unknown_member
from .sources import DEFAULT_MEMBERS, DEFAULT_SOURCES, fetch_source
from .sources.common import DEFAULT_DEMAND_THRESHOLDS, DemandThresholds, Thresholds

log = logging.getLogger(__name__)

MAX_WORKERS = 16


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
    with ThreadPoolExecutor(max_workers=max(1, min(len(tasks), MAX_WORKERS))) as pool:
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
        pending = set(futures)
        try:
            for future in as_completed(futures, timeout=timeout):
                pending.discard(future)
                member, source_id = futures[future]
                try:
                    result = future.result()
                except TimeoutError:
                    log.warning("source %s timed out after %s seconds", source_id, timeout)
                    result = unknown_member(
                        f"{source_id} no respondió a tiempo después de {timeout:g} segundos"
                    )
                except Exception as exc:
                    log.warning("source %s failed: %s: %s", source_id, type(exc).__name__, exc)
                    result = unknown_member(f"{type(exc).__name__}: {exc}")
                groups[member].append((source_id, result))
        except TimeoutError:
            pass
        for future in pending:
            member, source_id = futures[future]
            future.cancel()
            log.warning("source %s timed out after %s seconds", source_id, timeout)
            groups[member].append(
                (
                    source_id,
                    unknown_member(
                        f"{source_id} no respondió a tiempo después de {timeout:g} segundos"
                    ),
                )
            )

    resolved = {
        member: aggregate(sorted(results)).to_json()
        for member, results in groups.items()
    }
    return {"updated": updated, "members": dict(sorted(resolved.items()))}


def collect_from_config(config: Config) -> dict:
    return collect(
        config.members or None,
        timeout=config.timeout,
        thresholds=config.thresholds,
        demand_thresholds=config.demand_thresholds,
        sources=config.sources,
        member_sources=config.member_sources,
    )
