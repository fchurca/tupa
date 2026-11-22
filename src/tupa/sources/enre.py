from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, time, timezone, tzinfo
from email.utils import parsedate_to_datetime

from ..model import Member, make_member, system_timezone
from .common import (
    ACTIVE_KEYS,
    PLANNED_KEYS,
    Thresholds,
    count_rows,
    decode_js_data,
    fetch_content,
    legacy_tls_context,
    parse_count,
    thresholds_for,
)


def _last_modified(headers: Mapping[str, str], tz: tzinfo) -> datetime | None:
    value = headers.get("Last-Modified")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def _enre_timestamp(
    hhmm: object, headers: Mapping[str, str], tz: tzinfo
) -> str | None:
    modified = _last_modified(headers, tz)
    if modified is None:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm).strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.combine(
                modified.date(), time(hour, minute), tzinfo=tz
            ).isoformat(timespec="seconds")
    return modified.isoformat(timespec="seconds")


def fetch_enre(
    *,
    name: str,
    empresa: str,
    url: str,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
    tz: tzinfo | None = None,
    deadline: float | None = None,
) -> Member:
    zone = tz if tz is not None else system_timezone()
    text, headers = fetch_content(
        url,
        timeout=timeout,
        tls_context=legacy_tls_context(),
        deadline=deadline,
    )
    data = decode_js_data(text)
    reported = str(data.get("empresa", "")).upper()
    if reported != empresa:
        raise ValueError(
            f"{name}: unexpected empresa {reported!r}, expected {empresa!r}"
        )

    raw_affected = data.get("totalUsuariosSinSuministro")
    affected = parse_count(raw_affected)
    if affected is None:
        return make_member(
            "unknown",
            comment=f"ENRE no informa usuarios sin suministro: {raw_affected or 'sin dato'}",
            tz=zone,
        )

    active = count_rows(data, ACTIVE_KEYS)
    planned = count_rows(data, PLANNED_KEYS)
    status = thresholds_for(name, thresholds).classify(affected)

    reported_time = str(data.get("ultimaActualizacion", "?"))
    detail = [f"{affected} usuarios sin suministro", f"{active} cortes activos"]
    if planned:
        detail.append(f"{planned} cortes programados")
    detail.append(f"ENRE ultimaActualizacion {reported_time}")
    updated = _enre_timestamp(reported_time, headers, zone)
    if updated is None:
        detail.append("fecha no informada por ENRE")
    return make_member(status, comment="; ".join(detail), updated=updated, tz=zone)
