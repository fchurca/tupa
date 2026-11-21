from .edenor import fetch as fetch_edenor
from .edesur import fetch as fetch_edesur

SOURCES = {
    "edenor": fetch_edenor,
    "edesur": fetch_edesur,
}

__all__ = ["SOURCES"]
