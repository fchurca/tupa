from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STATUSES = frozenset({"operational", "warn", "high", "disaster", "unknown"})

SEVERITY = {"operational": 0, "warn": 1, "high": 2, "disaster": 3, "unknown": -1}

AR_TZ = timezone(timedelta(hours=-3))


def now_rfc3339() -> str:
    return datetime.now(AR_TZ).isoformat(timespec="seconds")


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
) -> Member:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    return Member(
        status=status,
        updated=updated if updated is not None else now_rfc3339(),
        comment=comment,
    )


def unknown_member(reason: str) -> Member:
    return make_member("unknown", comment=f"source unavailable: {reason}")


def aggregate(results: Sequence[tuple[str, Member]]) -> Member:
    if not results:
        return unknown_member("no sources configured")
    known = [member for _, member in results if member.status != "unknown"]
    if known:
        status = max(known, key=lambda member: SEVERITY[member.status]).status
    else:
        status = "unknown"
    comments = [
        f"{name}: {member.comment or member.status}" for name, member in results
    ]
    return make_member(
        status,
        comment="; ".join(comments),
        updated=max(member.updated for _, member in results),
    )
