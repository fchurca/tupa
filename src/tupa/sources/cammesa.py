from __future__ import annotations

import json
import math
import statistics
from collections.abc import Sequence
from datetime import datetime, tzinfo

from ..model import Member, SourceSpec, make_member
from .common import Thresholds, fetch_text

BASE_URL = (
    "https://api.cammesa.com/demanda-svc/demanda/"
    "ObtieneDemandaYTemperaturaRegion?id_region="
)
WINDOW = 3


def url_for(region: int) -> str:
    return f"{BASE_URL}{region}"


def _number(value: object, *, allow_zero: bool = False) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if number == 0 and not allow_zero:
        return None
    return number


def _timestamp(value: object, tz: tzinfo) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"CAMMESA fecha no válida: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).isoformat(timespec="seconds")


def _rows(payload: object) -> Sequence[tuple[str, float, float, float]]:
    if not isinstance(payload, list):
        raise ValueError("CAMMESA response is not a list")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        fecha = str(item.get("fecha", "")).strip()
        hoy = _number(item.get("demHoy"), allow_zero=True)
        ayer = _number(item.get("demAyer"))
        semana = _number(item.get("demSemanaAnt"))
        if not fecha or hoy is None or ayer is None or semana is None:
            continue
        reference = min(ayer, semana)
        deficit = max(0.0, 1.0 - hoy / reference)
        result.append((fecha, hoy, reference, deficit))
    return sorted(result, key=lambda row: row[0])


def fetch_demand(
    *,
    spec: SourceSpec,
    timeout: float,
    thresholds: Thresholds,
    tz: tzinfo,
    deadline: float | None = None,
) -> Member:
    payload = json.loads(fetch_text(spec.url, timeout=timeout, deadline=deadline))
    rows = _rows(payload)
    if not rows:
        return make_member(
            "unknown", comment="CAMMESA sin datos comparables de demanda", tz=tz
        )
    window = rows[-WINDOW:]
    median = statistics.median(row[3] for row in window)
    fecha, hoy, reference, deficit = min(
        reversed(window), key=lambda row: abs(row[3] - median)
    )
    updated = _timestamp(fecha, tz)
    comment = (
        f"{hoy:.0f} MW; {deficit:.1%} bajo referencia {reference:.0f} MW; "
        f"CAMMESA región {spec.region} {updated}"
    )
    return make_member(
        thresholds.classify(deficit), comment=comment, updated=updated, tz=tz
    )
