from __future__ import annotations

from collections.abc import Mapping
from datetime import tzinfo

from ..model import Member, SourceSpec
from . import cammesa, oceba
from .cammesa import fetch_demand
from .common import Thresholds
from .enre import fetch_enre
from .oceba import fetch_oceba

DEFAULT_SOURCES: dict[str, SourceSpec] = {
    "enre_edenor": SourceSpec(
        name="enre_edenor",
        kind="enre_outages",
        member="edenor",
        url="https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
        label="ENRE Edenor",
        docs_url="https://www.enre.gov.ar/mapacortes/index.html",
        empresa="EDENOR",
    ),
    "enre_edesur": SourceSpec(
        name="enre_edesur",
        kind="enre_outages",
        member="edesur",
        url="https://www.enre.gov.ar/paginacorte/js/data_EDS.js",
        label="ENRE Edesur",
        docs_url="https://www.enre.gov.ar/mapacortes/index.html",
        empresa="EDESUR",
    ),
    "cammesa_edenor": SourceSpec(
        name="cammesa_edenor",
        kind="cammesa_demand",
        member="edenor",
        url=cammesa.url_for(1077),
        label="CAMMESA Edenor (1077)",
        docs_url="https://cammesaweb.cammesa.com/informes-y-estadisticas/",
        region=1077,
    ),
    "cammesa_edesur": SourceSpec(
        name="cammesa_edesur",
        kind="cammesa_demand",
        member="edesur",
        url=cammesa.url_for(1078),
        label="CAMMESA Edesur (1078)",
        docs_url="https://cammesaweb.cammesa.com/informes-y-estadisticas/",
        region=1078,
    ),
    "cammesa_edelap": SourceSpec(
        name="cammesa_edelap",
        kind="cammesa_demand",
        member="edelap",
        url=cammesa.url_for(1943),
        label="CAMMESA Edelap (1943)",
        docs_url="https://cammesaweb.cammesa.com/informes-y-estadisticas/",
        region=1943,
    ),
    "oceba_edelap": SourceSpec(
        name="oceba_edelap",
        kind="oceba_outages",
        member="edelap",
        url=oceba.URL,
        label="OCEBA Edelap",
        docs_url=oceba.DOCS_URL,
    ),
}

DEFAULT_MEMBERS: dict[str, tuple[str, ...]] = {
    "edenor": ("cammesa_edenor", "enre_edenor"),
    "edesur": ("cammesa_edesur", "enre_edesur"),
    "edelap": ("cammesa_edelap", "oceba_edelap"),
}


def fetch_source(
    spec: SourceSpec,
    *,
    timeout: float,
    thresholds: Mapping[str, Thresholds],
    demand_thresholds: Thresholds,
    tz: tzinfo,
    deadline: float | None = None,
) -> Member:
    if spec.kind == "enre_outages":
        return fetch_enre(
            name=spec.member,
            empresa=spec.empresa,
            url=spec.url,
            timeout=timeout,
            thresholds=thresholds,
            tz=tz,
            deadline=deadline,
        )
    if spec.kind == "oceba_outages":
        return fetch_oceba(
            spec=spec,
            timeout=timeout,
            thresholds=thresholds,
            tz=tz,
            deadline=deadline,
        )
    return fetch_demand(
        spec=spec,
        timeout=timeout,
        thresholds=demand_thresholds,
        tz=tz,
        deadline=deadline,
    )
