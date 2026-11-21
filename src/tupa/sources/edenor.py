from collections.abc import Mapping

from ..model import Member
from .common import Thresholds
from .enre import fetch_enre

URL = "https://www.enre.gov.ar/paginacorte/js/data_EDN.js"


def fetch(
    timeout: float, thresholds: Mapping[str, Thresholds] | None = None
) -> Member:
    return fetch_enre(
        name="Edenor",
        empresa="EDENOR",
        url=URL,
        timeout=timeout,
        thresholds=thresholds,
    )
