from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, time, timezone, tzinfo
from email.utils import parsedate_to_datetime

from ..model import ART_TZ, Member, make_member
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
    except (TypeError, ValueError):
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
            art_date = modified.astimezone(ART_TZ).date()
            return (
                datetime.combine(art_date, time(hour, minute), tzinfo=ART_TZ)
                .astimezone(tz)
                .isoformat(timespec="seconds")
            )
    return modified.isoformat(timespec="seconds")


def fetch_enre(
    *,
    name: str,
    empresa: str,
    url: str,
    timeout: float,
    thresholds: Mapping[str, Thresholds],
    tz: tzinfo,
    deadline: float | None = None,
) -> Member:
    text, headers = fetch_content(
        url,
        timeout=timeout,
        tls_context=legacy_tls_context(),
        deadline=deadline,
    )
    data = decode_js_data(text)
    reported = str(data.get("empresa", "")).strip().upper()
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
            tz=tz,
        )

    active = count_rows(data, ACTIVE_KEYS)
    planned = count_rows(data, PLANNED_KEYS)
    status = thresholds_for(name, thresholds).classify(affected)

    reported_time = str(data.get("ultimaActualizacion", "?"))
    detail = [f"{affected} usuarios sin suministro", f"{active} cortes activos"]
    if planned:
        detail.append(f"{planned} cortes programados")
    detail.append(f"ENRE ultimaActualizacion {reported_time}")
    updated = _enre_timestamp(reported_time, headers, tz)
    if updated is None:
        detail.append("fecha no informada por ENRE")
    return make_member(status, comment="; ".join(detail), updated=updated, tz=tz)
