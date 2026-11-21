from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .model import now_rfc3339, unknown_member
from .sources import SOURCES
from .sources.common import DEFAULT_THRESHOLDS, Thresholds

log = logging.getLogger(__name__)


def collect(
    names: Sequence[str] | None,
    *,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
) -> dict:
    selected = tuple(names) if names else tuple(SOURCES)
    selected_thresholds = DEFAULT_THRESHOLDS if thresholds is None else thresholds
    updated = now_rfc3339()
    members: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(selected))) as pool:
        futures = {
            pool.submit(
                SOURCES[name], timeout=timeout, thresholds=selected_thresholds
            ): name
            for name in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                members[name] = future.result().to_json()
            except Exception as exc:
                log.warning("source %s failed: %s: %s", name, type(exc).__name__, exc)
                members[name] = unknown_member(f"{type(exc).__name__}: {exc}").to_json()
    return {"updated": updated, "members": dict(sorted(members.items()))}
