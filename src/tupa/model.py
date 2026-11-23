from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATUSES = frozenset({"operational", "warn", "high", "disaster", "unknown"})

SEVERITY = {"operational": 0, "warn": 1, "high": 2, "disaster": 3, "unknown": -1}

ART_TZ: tzinfo = ZoneInfo("America/Argentina/Buenos_Aires")


def system_timezone() -> tzinfo:
    name = os.environ.get("TZ", "").strip().lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ART_TZ


def resolve_timezone(value: object = None) -> tzinfo:
    if value is None:
        return ART_TZ
    text = str(value).strip().lstrip(":")
    if not text:
        return ART_TZ
    if text.upper() in {"SYSTEM", "LOCAL"}:
        return system_timezone()
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {value!r}") from None


def now_rfc3339(tz: tzinfo | None = None) -> str:
    return datetime.now(tz if tz is not None else ART_TZ).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    kind: str
    member: str
    url: str
    empresa: str | None = None
    region: int | None = None


@dataclass(frozen=True, slots=True)
class Member:
    status: str
    updated: str
    comment: str | None = None

    def to_json(self) -> dict[str, str]:
        result = {"updated": self.updated, "status": self.status}
        if self.comment is not None:
            result["comment"] = self.comment
        return result


def _normalized_updated(value: str, tz: tzinfo) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).isoformat(timespec="seconds")


def make_member(
    status: str,
    comment: str | None = None,
    updated: str | None = None,
    *,
    tz: tzinfo | None = None,
) -> Member:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    zone = tz if tz is not None else ART_TZ
    normalized = (
        now_rfc3339(zone)
        if updated is None
        else _normalized_updated(updated, zone)
    )
    return Member(status=status, updated=normalized, comment=comment)


def unknown_member(reason: str, *, tz: tzinfo | None = None) -> Member:
    return make_member("unknown", comment=f"source unavailable: {reason}", tz=tz)


def aggregate(
    results: Sequence[tuple[str, Member]], *, tz: tzinfo | None = None
) -> Member:
    zone = tz if tz is not None else ART_TZ
    if not results:
        return unknown_member("no sources configured", tz=zone)
    known = [member for _, member in results if member.status != "unknown"]
    if known:
        status = max(known, key=lambda member: SEVERITY[member.status]).status
    else:
        status = "unknown"
    comments = [f"{name}: {member.comment or member.status}" for name, member in results]
    for_latest = known if known else [member for _, member in results]
    latest = max(datetime.fromisoformat(member.updated) for member in for_latest)
    return make_member(
        status,
        comment="; ".join(comments),
        updated=latest.isoformat(timespec="seconds"),
        tz=zone,
    )
