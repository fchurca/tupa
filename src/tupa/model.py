from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATUSES = frozenset({"operational", "warn", "high", "disaster", "unknown"})

SEVERITY = {"operational": 0, "warn": 1, "high": 2, "disaster": 3, "unknown": -1}

try:
    ART_TZ: tzinfo = ZoneInfo("America/Argentina/Buenos_Aires")
except ZoneInfoNotFoundError:
    ART_TZ = timezone(timedelta(hours=-3))
AR_TZ = ART_TZ
UTC_TZ = timezone.utc
_OFFSET_RE = re.compile(r"\A([+-])(\d{2}):?(\d{2})\Z")


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
        return system_timezone()
    text = str(value).strip().lstrip(":")
    if not text or text.upper() in {"SYSTEM", "LOCAL"}:
        return system_timezone()
    if text.upper() in {"UTC", "GMT", "Z"}:
        return UTC_TZ
    match = _OFFSET_RE.fullmatch(text)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        return timezone(
            sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        )
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {value!r}") from None


def now_rfc3339(tz: tzinfo | None = None) -> str:
    return datetime.now(tz if tz is not None else system_timezone()).isoformat(
        timespec="seconds"
    )


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    kind: str
    member: str
    url: str | None = None
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


def make_member(
    status: str,
    comment: str | None = None,
    updated: str | None = None,
    *,
    tz: tzinfo | None = None,
) -> Member:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    return Member(
        status=status,
        updated=updated if updated is not None else now_rfc3339(tz),
        comment=comment,
    )


def unknown_member(reason: str, *, tz: tzinfo | None = None) -> Member:
    return make_member("unknown", comment=f"source unavailable: {reason}", tz=tz)


def _normalize_updated(value: str, tz: tzinfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def aggregate(
    results: Sequence[tuple[str, Member]], *, tz: tzinfo | None = None
) -> Member:
    zone = tz if tz is not None else system_timezone()
    if not results:
        return unknown_member("no sources configured", tz=zone)
    known = [member for _, member in results if member.status != "unknown"]
    if known:
        status = max(known, key=lambda member: SEVERITY[member.status]).status
    else:
        status = "unknown"
    comments = [f"{name}: {member.comment or member.status}" for name, member in results]
    for_latest = known if known else [member for _, member in results]
    latest = max(
        (_normalize_updated(member.updated, zone) for member in for_latest),
        default=None,
    )
    return make_member(
        status,
        comment="; ".join(comments),
        updated=latest.isoformat(timespec="seconds") if latest else None,
        tz=zone,
    )
