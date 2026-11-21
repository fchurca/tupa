from __future__ import annotations

from collections.abc import Mapping

from ..model import Member, SourceSpec
from ..sources.common import DEFAULT_DEMAND_THRESHOLDS, DemandThresholds, Thresholds
from . import cammesa
from .cammesa import fetch_demand
from .enre import fetch_enre

SOURCE_KINDS = frozenset({"enre_outages", "cammesa_demand"})

DEFAULT_SOURCES: dict[str, SourceSpec] = {
    "enre_edenor": SourceSpec(
        name="enre_edenor",
        kind="enre_outages",
        member="edenor",
        url="https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
        empresa="EDENOR",
    ),
    "enre_edesur": SourceSpec(
        name="enre_edesur",
        kind="enre_outages",
        member="edesur",
        url="https://www.enre.gov.ar/paginacorte/js/data_EDS.js",
        empresa="EDESUR",
    ),
    "cammesa_edenor": SourceSpec(
        name="cammesa_edenor",
        kind="cammesa_demand",
        member="edenor",
        url=cammesa.url_for(1077),
        region=1077,
    ),
    "cammesa_edesur": SourceSpec(
        name="cammesa_edesur",
        kind="cammesa_demand",
        member="edesur",
        url=cammesa.url_for(1078),
        region=1078,
    ),
}

DEFAULT_MEMBERS: dict[str, tuple[str, ...]] = {
    "edenor": ("cammesa_edenor", "enre_edenor"),
    "edesur": ("cammesa_edesur", "enre_edesur"),
}


def fetch_source(
    spec: SourceSpec,
    *,
    timeout: float,
    thresholds: Mapping[str, Thresholds] | None = None,
    demand_thresholds: DemandThresholds | None = None,
) -> Member:
    if spec.kind == "enre_outages":
        if not spec.url or not spec.empresa:
            raise ValueError(f"{spec.name}: enre_outages requires url and empresa")
        return fetch_enre(
            name=spec.member,
            empresa=spec.empresa,
            url=spec.url,
            timeout=timeout,
            thresholds=thresholds,
        )
    if spec.kind == "cammesa_demand":
        if not spec.region:
            raise ValueError(f"{spec.name}: cammesa_demand requires region")
        return fetch_demand(
            spec=spec,
            timeout=timeout,
            thresholds=demand_thresholds or DEFAULT_DEMAND_THRESHOLDS,
        )
    raise ValueError(f"{spec.name}: unknown source kind {spec.kind!r}")
