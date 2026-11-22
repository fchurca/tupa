from __future__ import annotations

import math
import re
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from urllib.request import Request, urlopen

from ..version import get_version

USER_AGENT = f"TUPA/{get_version()} (+Telemetría Unificada de Potencia Argentina)"

ACTIVE_KEYS = ("cortesPreventivos", "cortesServicioMedia", "cortesServicioBaja")
PLANNED_KEYS = ("cortesProgramados", "cortesComunicados")

DEFAULT_CUSTOMERS = {
    "edenor": 3_400_000,
    "edesur": 2_700_000,
    "edelap": 400_000,
}

DEFAULT_LEVELS = (0.0001, 0.005, 0.05)

DEFAULT_DEMAND_LEVELS = (0.15, 0.30, 0.50)


@lru_cache(maxsize=1)
def legacy_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


def fetch_content(
    url: str, *, timeout: float, tls_context: ssl.SSLContext | None = None
) -> tuple[str, Mapping[str, str]]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=tls_context) as response:
        raw = response.read()
        headers: Mapping[str, str] = getattr(response, "headers", {})
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding), headers
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), headers


def fetch_text(
    url: str, *, timeout: float, tls_context: ssl.SSLContext | None = None
) -> str:
    return fetch_content(url, timeout=timeout, tls_context=tls_context)[0]


class _JSLiteral:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def decode_data(self) -> dict:
        match = re.search(r"\bvar\s+data\s*=", self.text)
        if not match:
            raise ValueError("data assignment not found")
        self.pos = match.end()
        value = self._value()
        if not isinstance(value, dict):
            raise ValueError("data is not an object")
        return value

    def _skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _expect(self, char: str) -> None:
        self._skip()
        if self.pos >= len(self.text) or self.text[self.pos] != char:
            raise ValueError(f"expected {char!r} at offset {self.pos}")
        self.pos += 1

    def _value(self):
        self._skip()
        if self.pos >= len(self.text):
            raise ValueError("unexpected end of data")
        char = self.text[self.pos]
        if char in ("'", '"'):
            return self._string()
        if char == "[":
            return self._array()
        if char == "{":
            return self._object()
        if char.isdigit() or char == "-":
            return self._number()
        for word, value in (
            ("true", True),
            ("false", False),
            ("null", None),
            ("undefined", None),
        ):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return value
        raise ValueError(f"unexpected token at offset {self.pos}: {char!r}")

    def _string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return "".join(out)
            if char == "\\":
                if self.pos >= len(self.text):
                    break
                esc = self.text[self.pos]
                self.pos += 1
                out.append({"n": "\n", "r": "\r", "t": "\t"}.get(esc, esc))
            else:
                out.append(char)
        raise ValueError("unterminated string")

    def _number(self):
        match = re.match(r"-?\d+(?:\.\d+)?", self.text[self.pos :])
        if not match:
            raise ValueError(f"invalid number at offset {self.pos}")
        self.pos += match.end()
        text = match.group(0)
        return float(text) if "." in text else int(text)

    def _array(self) -> list:
        self._expect("[")
        out = []
        while True:
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1
                return out
            out.append(self._value())
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            self._expect("]")
            return out

    def _object(self) -> dict:
        self._expect("{")
        out = {}
        while True:
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == "}":
                self.pos += 1
                return out
            if self.pos >= len(self.text):
                raise ValueError("unterminated object")
            if self.text[self.pos] in ("'", '"'):
                key = self._string()
            else:
                match = re.match(r"[A-Za-z_$][\w$]*", self.text[self.pos :])
                if not match:
                    raise ValueError(f"invalid key at offset {self.pos}")
                key = match.group(0)
                self.pos += match.end()
            self._expect(":")
            out[key] = self._value()
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            self._expect("}")
            return out


def decode_js_data(text: str) -> dict:
    return _JSLiteral(text).decode_data()


def parse_count(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(".", "").replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def count_rows(data: dict, keys) -> int:
    return sum(len(data.get(key) or ()) for key in keys)


@dataclass(frozen=True, slots=True)
class Thresholds:
    warn: int
    high: int
    disaster: int

    def classify(self, affected: int) -> str:
        if affected >= self.disaster:
            return "disaster"
        if affected >= self.high:
            return "high"
        if affected >= self.warn:
            return "warn"
        return "operational"


def thresholds_from(
    customers: Mapping[str, int], levels: Sequence[float]
) -> dict[str, Thresholds]:
    return {
        name: Thresholds(*(math.ceil(base * fraction) for fraction in levels))
        for name, base in customers.items()
    }


DEFAULT_THRESHOLDS = thresholds_from(DEFAULT_CUSTOMERS, DEFAULT_LEVELS)


def thresholds_for(
    name: str, thresholds: Mapping[str, Thresholds] | None = None
) -> Thresholds:
    return (DEFAULT_THRESHOLDS if thresholds is None else thresholds)[name.lower()]


@dataclass(frozen=True, slots=True)
class DemandThresholds:
    warn: float
    high: float
    disaster: float

    def classify(self, deficit: float) -> str:
        if deficit >= self.disaster:
            return "disaster"
        if deficit >= self.high:
            return "high"
        if deficit >= self.warn:
            return "warn"
        return "operational"


DEFAULT_DEMAND_THRESHOLDS = DemandThresholds(*DEFAULT_DEMAND_LEVELS)
