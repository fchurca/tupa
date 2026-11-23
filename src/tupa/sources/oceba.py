from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, time, timedelta, tzinfo

from ..model import ART_TZ, Member, SourceSpec, make_member
from .common import Thresholds, fetch_content, parse_count, thresholds_for

URL = "https://oceba.gba.gov.ar/nueva_web/s.php?distribuidora=1&i=123"
DOCS_URL = "https://oceba.gba.gov.ar/nueva_web/s.php?i=124"
SECTIONS = (
    "Interrupciones preventivas",
    "Interrupciones Programadas por Mantenimiento y Obras",
    "Interrupciones en el Servicio de Media Tensión",
    "Interrupciones en el Servicio de Baja Tensión",
)
_UPDATED = re.compile(r"Ultima actualizacion:\s*(\d{1,2}:\d{2})", re.IGNORECASE)
_AFFECTED = re.compile(
    r"Total de usuarios sin suministro\s*</h4>\s*<p[^>]*>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)


def _value(pattern: re.Pattern[str], text: str, label: str) -> str:
    match = pattern.search(text)
    if not match:
        raise ValueError(f"OCEBA {label} not found")
    return match.group(1).strip()


def _rows(text: str, heading: str) -> int:
    start = text.lower().find(heading.lower())
    if start < 0:
        raise ValueError(f"OCEBA section {heading!r} not found")
    table_start = text.find("<table", start)
    table_end = text.find("</table>", table_start)
    if table_start < 0 or table_end < 0:
        raise ValueError(f"OCEBA table {heading!r} not found")
    return max(0, len(re.findall(r"<tr\b", text[table_start:table_end], re.IGNORECASE)) - 1)


def _timestamp(value: str, tz: tzinfo) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if not match:
        raise ValueError(f"OCEBA invalid update time: {value!r}")
    now = datetime.now(ART_TZ)
    parsed = datetime.combine(
        now.date(), time(int(match.group(1)), int(match.group(2))), tzinfo=ART_TZ
    )
    if parsed > now + timedelta(minutes=5):
        parsed -= timedelta(days=1)
    return parsed.astimezone(tz).isoformat(timespec="seconds")


def fetch_oceba(
    *,
    spec: SourceSpec,
    timeout: float,
    thresholds: Mapping[str, Thresholds],
    tz: tzinfo,
    deadline: float | None = None,
) -> Member:
    text, _ = fetch_content(spec.url, timeout=timeout, deadline=deadline)
    if "EDELAP S.A." not in text.upper():
        raise ValueError(f"{spec.member}: unexpected distributor")
    reported = _value(_UPDATED, text, "update time")
    raw_affected = re.sub(r"<[^>]+>", "", _value(_AFFECTED, text, "affected users"))
    affected = parse_count(raw_affected)
    if affected is None:
        return make_member(
            "unknown",
            comment=f"OCEBA no informa usuarios sin suministro: {raw_affected or 'sin dato'}",
            tz=tz,
        )
    updated = _timestamp(reported, tz)
    active = sum(_rows(text, heading) for heading in SECTIONS)
    detail = [
        f"{affected} usuarios sin suministro",
        f"{active} cortes informados",
        f"OCEBA ultimaActualizacion {reported}",
    ]
    return make_member(
        thresholds_for(spec.member, thresholds).classify(affected),
        comment="; ".join(detail),
        updated=updated,
        tz=tz,
    )
