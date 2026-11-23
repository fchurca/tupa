from __future__ import annotations

import http.client
import math
import re
import socket
import ssl
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from urllib.error import URLError
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
)

from ..version import get_version

USER_AGENT = f"TUPA/{get_version()} (+Telemetría Unificada de Potencia Argentina)"
MAX_RESPONSE_BYTES = 1_000_000

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


_fetch_local = threading.local()


@contextmanager
def _fetch_deadline(deadline: float | None):
    previous = getattr(_fetch_local, "deadline", None)
    _fetch_local.deadline = deadline
    try:
        yield
    finally:
        _fetch_local.deadline = previous


def _remaining(deadline: float | None, fallback: float | None = None) -> float | None:
    if deadline is None:
        return fallback
    value = max(0.001, deadline - time.monotonic())
    return value if fallback is None else min(fallback, value)


def _connect_with_deadline(address, timeout: float | None, source_address, **kwargs) -> socket.socket:
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    last_error: OSError | None = None
    for family, socktype, proto, _, sockaddr in infos:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(_remaining(getattr(_fetch_local, "deadline", None), timeout))
            if source_address is not None:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise socket.gaierror(socket.EAI_AGAIN, "no address for host")


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def _create_connection(self, address, timeout=None, source_address=None, **kwargs):
        return _connect_with_deadline(address, timeout, source_address, **kwargs)


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def _create_connection(self, address, timeout=None, source_address=None, **kwargs):
        return _connect_with_deadline(address, timeout, source_address, **kwargs)


class _DeadlineHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_DeadlineHTTPConnection, req)


class _DeadlineHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_DeadlineHTTPSConnection, req, context=self._context)


def urlopen(request: Request, *, timeout: float, context: ssl.SSLContext | None = None):
    opener = OpenerDirector()
    opener.add_handler(ProxyHandler({}))
    opener.add_handler(HTTPDefaultErrorHandler())
    opener.add_handler(HTTPRedirectHandler())
    opener.add_handler(HTTPErrorProcessor())
    opener.add_handler(_DeadlineHTTPHandler())
    opener.add_handler(_DeadlineHTTPSHandler(context=context))
    response = opener.open(request, timeout=timeout)
    if response is None:
        raise URLError(f"unknown url type: {request.type}")
    return response


def fetch_content(
    url: str,
    *,
    timeout: float,
    tls_context: ssl.SSLContext | None = None,
    deadline: float | None = None,
) -> tuple[str, Mapping[str, str]]:
    remaining = _remaining(deadline, timeout)
    assert remaining is not None
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with _fetch_deadline(deadline):
        try:
            response = urlopen(request, timeout=remaining, context=tls_context)
        except URLError as exc:
            reason = exc.reason
            if deadline is not None and (
                isinstance(reason, TimeoutError) or time.monotonic() >= deadline
            ):
                raise TimeoutError(f"fetch deadline after {timeout:g} seconds") from exc
            raise
        with response:
            try:
                chunks: list[bytes] = []
                total = 0
                while total <= MAX_RESPONSE_BYTES:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(f"fetch deadline after {timeout:g} seconds")
                    chunk = response.read(min(8192, MAX_RESPONSE_BYTES + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
            except (OSError, http.client.IncompleteRead) as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"fetch deadline after {timeout:g} seconds") from exc
                raise
            headers: Mapping[str, str] = getattr(response, "headers", {})
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
    try:
        return raw.decode("utf-8"), headers
    except UnicodeDecodeError:
        return raw.decode("latin-1"), headers


def fetch_text(
    url: str,
    *,
    timeout: float,
    tls_context: ssl.SSLContext | None = None,
    deadline: float | None = None,
) -> str:
    return fetch_content(
        url, timeout=timeout, tls_context=tls_context, deadline=deadline
    )[0]


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
        if char.isdigit() or char in "-+.":
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
                if esc in ("u", "x"):
                    raise ValueError(f"unsupported escape \\{esc} at offset {self.pos}")
                if esc in "\r\n":
                    if esc == "\r" and self.text[self.pos : self.pos + 1] == "\n":
                        self.pos += 1
                else:
                    out.append(
                        {
                            "b": "\b",
                            "f": "\f",
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            "v": "\v",
                            "0": "\0",
                        }.get(esc, esc)
                    )
            else:
                out.append(char)
        raise ValueError("unterminated string")

    def _number(self):
        match = re.match(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
            self.text[self.pos :],
        )
        if not match:
            raise ValueError(f"invalid number at offset {self.pos}")
        self.pos += match.end()
        text = match.group(0)
        return float(text) if any(marker in text for marker in ".eE") else int(text)

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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 else None
    text = re.sub(r"\s+", "", str(value))
    if not re.fullmatch(r"\d+|\d{1,3}(?:\.\d{3})+", text):
        return None
    return int(text.replace(".", ""))


def count_rows(data: dict, keys) -> int:
    return sum(len(data.get(key) or ()) for key in keys)


@dataclass(frozen=True, slots=True)
class Thresholds:
    warn: float
    high: float
    disaster: float

    def classify(self, value: float) -> str:
        if value >= self.disaster:
            return "disaster"
        if value >= self.high:
            return "high"
        if value >= self.warn:
            return "warn"
        return "operational"


def thresholds_from(
    customers: Mapping[str, int], levels: Sequence[float]
) -> dict[str, Thresholds]:
    return {
        name: Thresholds(*(math.ceil(base * fraction) for fraction in levels))
        for name, base in customers.items()
    }


def thresholds_for(name: str, thresholds: Mapping[str, Thresholds]) -> Thresholds:
    try:
        return thresholds[name]
    except KeyError:
        raise ValueError(f"no thresholds configured for {name!r}") from None
