from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STATUSES = frozenset({"operational", "warn", "high", "disaster", "unknown"})

AR_TZ = timezone(timedelta(hours=-3))


def now_rfc3339() -> str:
    return datetime.now(AR_TZ).isoformat(timespec="seconds")


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


_DOWNGRADE = {
    "disaster": "high",
    "high": "warn",
    "warn": "operational",
    "operational": "operational",
    "unknown": "unknown",
}


def maintenance(status: str) -> str:
    return _DOWNGRADE[status]
