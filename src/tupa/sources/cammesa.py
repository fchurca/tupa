from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import datetime

from ..model import AR_TZ, Member, SourceSpec, make_member, now_rfc3339
from .common import DEFAULT_DEMAND_THRESHOLDS, DemandThresholds, fetch_text

BASE_URL = (
    "https://api.cammesa.com/demanda-svc/demanda/"
    "ObtieneDemandaYTemperaturaRegion?id_region="
)
WINDOW = 3


def url_for(region: int) -> str:
    return f"{BASE_URL}{region}"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _timestamp(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("-0300", "-03:00"))
    except ValueError:
        return now_rfc3339()
    return parsed.astimezone(AR_TZ).isoformat(timespec="seconds")


def _rows(payload: object) -> Sequence[tuple[str, float, float, float]]:
    if not isinstance(payload, list):
        raise ValueError("CAMMESA response is not a list")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        hoy = _number(item.get("demHoy"))
        ayer = _number(item.get("demAyer"))
        semana = _number(item.get("demSemanaAnt"))
        if hoy is None or ayer is None or semana is None:
            continue
        reference = min(ayer, semana)
        deficit = max(0.0, 1.0 - hoy / reference)
        result.append((str(item.get("fecha", "")), hoy, reference, deficit))
    return result


def fetch_demand(
    *,
    spec: SourceSpec,
    timeout: float,
    thresholds: DemandThresholds | None = None,
) -> Member:
    url = spec.url or url_for(spec.region or 0)
    payload = json.loads(fetch_text(url, timeout=timeout))
    rows = _rows(payload)
    if not rows:
        return make_member("unknown", comment="CAMMESA sin datos comparables de demanda")
    window = rows[-WINDOW:]
    fecha, hoy, reference, _ = window[-1]
    deficit = statistics.median(row[3] for row in window)
    selected = DEFAULT_DEMAND_THRESHOLDS if thresholds is None else thresholds
    comment = (
        f"{hoy:.0f} MW; {deficit:.1%} bajo referencia {reference:.0f} MW; "
        f"CAMMESA región {spec.region or '?'} {fecha}"
    )
    return make_member(selected.classify(deficit), comment=comment, updated=_timestamp(fecha))
