from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import tzinfo

from .config import Config
from .model import Member, SourceSpec, aggregate, now_rfc3339, unknown_member
from .sources import fetch_source
from .sources.common import Thresholds

log = logging.getLogger(__name__)

MAX_WORKERS = 16
_POOL = ThreadPoolExecutor(max_workers=MAX_WORKERS)


def collect(
    members: Sequence[str] | None,
    *,
    sources: Mapping[str, SourceSpec],
    member_sources: Mapping[str, Sequence[str]],
    timeout: float,
    thresholds: Mapping[str, Thresholds],
    demand_thresholds: Thresholds,
    tz: tzinfo,
) -> dict:
    definition = member_sources
    selected = tuple(definition) if not members else tuple(members)
    tasks = [
        (member, source_id, sources[source_id])
        for member in selected
        for source_id in definition[member]
    ]
    updated = now_rfc3339(tz)
    deadline = time.monotonic() + timeout
    groups: dict[str, list[tuple[str, Member]]] = {name: [] for name in selected}
    futures: dict[Future, tuple[str, str]] = {
        _POOL.submit(
            fetch_source,
            spec,
            timeout=timeout,
            thresholds=thresholds,
            demand_thresholds=demand_thresholds,
            tz=tz,
            deadline=deadline,
        ): (member, source_id)
        for member, source_id, spec in tasks
    }
    done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    for future in done:
        member, source_id = futures[future]
        try:
            result = future.result()
        except TimeoutError:
            log.warning("source %s timed out after %s seconds", source_id, timeout)
            result = unknown_member(
                f"{source_id} no respondió a tiempo después de {timeout:g} segundos",
                tz=tz,
            )
        except Exception as exc:
            log.warning("source %s failed: %s: %s", source_id, type(exc).__name__, exc)
            result = unknown_member(f"{type(exc).__name__}: {exc}", tz=tz)
        groups[member].append((source_id, result))
    for future in pending:
        future.cancel()
        member, source_id = futures[future]
        log.warning("source %s timed out after %s seconds", source_id, timeout)
        groups[member].append(
            (
                source_id,
                unknown_member(
                    f"{source_id} no respondió a tiempo después de {timeout:g} segundos",
                    tz=tz,
                ),
            )
        )

    resolved = {
        member: aggregate(sorted(results, key=lambda item: item[0]), tz=tz).to_json()
        for member, results in groups.items()
    }
    return {"updated": updated, "members": dict(sorted(resolved.items()))}


def collect_from_config(config: Config) -> dict:
    return collect(
        config.members,
        sources=config.sources,
        member_sources=config.member_sources,
        timeout=config.timeout,
        thresholds=config.thresholds,
        demand_thresholds=config.demand_thresholds,
        tz=config.tz,
    )
