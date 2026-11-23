from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, wait
from datetime import tzinfo

from .config import Config
from .model import Member, SourceSpec, aggregate, now_rfc3339, unknown_member
from .sources import DEFAULT_MEMBERS, DEFAULT_SOURCES, fetch_source
from .sources.common import DEFAULT_DEMAND_THRESHOLDS, DemandThresholds, Thresholds

log = logging.getLogger(__name__)

MAX_WORKERS = 16
_POOL: _DaemonPool | None = None
_POOL_LOCK = threading.Lock()


class _DaemonPool:
    def __init__(self, max_workers: int):
        self._tasks: queue.Queue[tuple[Future, object, tuple, dict] | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        for index in range(max_workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"tupa-fetch-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _worker(self) -> None:
        while True:
            item = self._tasks.get()
            if item is None:
                return
            future, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue
            result: object = None
            error: BaseException | None = None
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                error = exc
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

    def submit(self, fn, *args, **kwargs) -> Future:
        future: Future = Future()
        self._tasks.put((future, fn, args, kwargs))
        return future


def _get_pool() -> _DaemonPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = _DaemonPool(MAX_WORKERS)
        return _POOL


def collect(
    members: Sequence[str] | None,
    *,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
    demand_thresholds: DemandThresholds | None = None,
    sources: Mapping[str, SourceSpec] | None = None,
    member_sources: Mapping[str, Sequence[str]] | None = None,
    tz: tzinfo | None = None,
) -> dict:
    source_defs = DEFAULT_SOURCES if sources is None else sources
    definition = DEFAULT_MEMBERS if member_sources is None else member_sources
    selected = tuple(definition) if not members else tuple(members)
    for name in selected:
        if name not in definition:
            raise ValueError(f"unknown member: {name!r}")
        for source_id in definition[name]:
            if source_id not in source_defs:
                raise ValueError(f"member {name!r} uses unknown source {source_id!r}")

    tasks = [
        (member, source_id, source_defs[source_id])
        for member in selected
        for source_id in definition[member]
    ]
    updated = now_rfc3339(tz)
    deadline = time.monotonic() + timeout
    groups: dict[str, list[tuple[str, Member]]] = {name: [] for name in selected}
    futures: dict[Future, tuple[str, str]] = {}
    pool = _get_pool() if tasks else None
    if pool is not None:
        futures = {
            pool.submit(
                fetch_source,
                spec,
                timeout=timeout,
                thresholds=thresholds,
                demand_thresholds=demand_thresholds or DEFAULT_DEMAND_THRESHOLDS,
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
        timeout=config.timeout,
        thresholds=config.thresholds,
        demand_thresholds=config.demand_thresholds,
        sources=config.sources,
        member_sources=config.member_sources,
        tz=config.tz,
    )
