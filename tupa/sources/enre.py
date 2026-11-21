from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from ..model import AR_TZ, Member, maintenance, make_member
from .common import (
    ACTIVE_KEYS,
    PLANNED_KEYS,
    Thresholds,
    count_rows,
    decode_js_data,
    fetch_text,
    legacy_tls_context,
    parse_count,
    thresholds_for,
)


def _enre_timestamp(hhmm: object) -> str:
    now = datetime.now(AR_TZ)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm).strip())
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            ).isoformat(timespec="seconds")
    return now.isoformat(timespec="seconds")


def fetch_enre(
    *,
    name: str,
    empresa: str,
    url: str,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
) -> Member:
    data = decode_js_data(
        fetch_text(url, timeout=timeout, tls_context=legacy_tls_context())
    )
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
        )

    active = count_rows(data, ACTIVE_KEYS)
    planned = count_rows(data, PLANNED_KEYS)
    status = thresholds_for(name, thresholds).classify(affected)
    if active == 0 and planned > 0:
        status = maintenance(status)

    detail = [f"{affected} usuarios sin suministro", f"{active} cortes activos"]
    if planned:
        detail.append(f"{planned} cortes programados")
    detail.append(f"ENRE {data.get('ultimaActualizacion', '?')}")
    return make_member(
        status,
        comment="; ".join(detail),
        updated=_enre_timestamp(data.get("ultimaActualizacion")),
    )
