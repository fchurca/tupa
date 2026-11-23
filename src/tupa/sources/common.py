from __future__ import annotations

import http.client
import ipaddress
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPSHandler,
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    UnknownHandler,
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


class FetchTimeout(TimeoutError):
    pass


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
    allowed = False
    for family, socktype, proto, _, sockaddr in infos:
        if not getattr(_fetch_local, "allow_private", False) and _restricted_ip(
            sockaddr[0]
        ):
            continue
        allowed = True
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
    if not allowed:
        raise socket.gaierror(
            getattr(socket, "EAI_ACCES", socket.EAI_FAIL), "target address not allowed"
        )
    raise socket.gaierror(socket.EAI_AGAIN, "no address for host")


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def _create_connection(self, address, timeout=None, source_address=None, **kwargs):
        sock = _connect_with_deadline(address, timeout, source_address, **kwargs)
        deadline = getattr(_fetch_local, "deadline", None)
        if deadline is not None:
            sock.settimeout(_remaining(deadline))
        _fetch_local.socket = sock
        return sock


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def _create_connection(self, address, timeout=None, source_address=None, **kwargs):
        sock = _connect_with_deadline(address, timeout, source_address, **kwargs)
        deadline = getattr(_fetch_local, "deadline", None)
        if deadline is not None:
            sock.settimeout(_remaining(deadline))
        _fetch_local.socket = sock
        return sock

    def connect(self) -> None:
        super().connect()
        deadline = getattr(_fetch_local, "deadline", None)
        if deadline is not None:
            self.sock.settimeout(_remaining(deadline))
        _fetch_local.socket = self.sock


class _DeadlineHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(_DeadlineHTTPConnection, req)


class _DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, context: ssl.SSLContext | None = None):
        super().__init__(context=context)
        self.context = context

    def https_open(self, req):
        return self.do_open(_DeadlineHTTPSConnection, req, context=self.context)


_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _restricted_ip(address: object) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except (TypeError, ValueError):
        return True
    return bool(
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed in _CGNAT_NETWORK
    )


def _target_privacy(host: str) -> bool | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return None
        if not infos:
            return None
        return any(_restricted_ip(info[4][0]) for info in infos)
    return _restricted_ip(address)


def _is_private_target(host: str) -> bool:
    result = _target_privacy(host)
    return True if result is None else result


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allow_private: bool = False):
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme.lower() not in ("http", "https") or (
            not self.allow_private and _target_privacy(target.hostname or "") is not False
        ):
            raise HTTPError(req.full_url, code, "unsafe redirect target", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request: Request, *, timeout: float, context: ssl.SSLContext | None = None):
    initial = urlsplit(request.full_url)
    allow_private = _target_privacy(initial.hostname or "") is True
    opener = OpenerDirector()
    opener.add_handler(ProxyHandler({}))
    opener.add_handler(UnknownHandler())
    opener.add_handler(HTTPDefaultErrorHandler())
    opener.add_handler(_RestrictedRedirectHandler(allow_private))
    opener.add_handler(HTTPErrorProcessor())
    opener.add_handler(_DeadlineHTTPHandler())
    opener.add_handler(_DeadlineHTTPSHandler(context=context))
    previous = getattr(_fetch_local, "allow_private", False)
    _fetch_local.allow_private = allow_private
    try:
        return opener.open(request, timeout=timeout)
    finally:
        _fetch_local.allow_private = previous


def _response_socket(response: object) -> socket.socket | None:
    raw = getattr(getattr(response, "fp", None), "raw", None)
    for candidate in (getattr(raw, "_sock", None), raw):
        if isinstance(candidate, socket.socket):
            return candidate
    return None


def fetch_content(
    url: str,
    *,
    timeout: float,
    tls_context: ssl.SSLContext | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    deadline: float | None = None,
) -> tuple[str, Mapping[str, str]]:
    remaining = _remaining(deadline, timeout)
    assert remaining is not None
    request = Request(url, headers={"User-Agent": USER_AGENT})
    previous_socket = getattr(_fetch_local, "socket", None)
    _fetch_local.socket = None
    try:
        with _fetch_deadline(deadline):
            try:
                response = urlopen(request, timeout=remaining, context=tls_context)
            except URLError as exc:
                reason = exc.reason
                if isinstance(reason, FetchTimeout):
                    raise reason from exc
                if deadline is not None and (
                    isinstance(reason, TimeoutError) or time.monotonic() >= deadline
                ):
                    raise FetchTimeout(
                        f"fetch deadline after {timeout:g} seconds"
                    ) from exc
                raise
            with response:
                sock = _response_socket(response) or getattr(_fetch_local, "socket", None)
                if sock is not None and deadline is not None:
                    sock.settimeout(_remaining(deadline, remaining))
                try:
                    stream = getattr(response, "fp", None)
                    read_chunk = getattr(stream, "read1", None)
                    if read_chunk is None:
                        raw = response.read(max_bytes + 1)
                    else:
                        chunks: list[bytes] = []
                        total = 0
                        while total <= max_bytes:
                            if deadline is not None and time.monotonic() >= deadline:
                                raise FetchTimeout(
                                    f"fetch deadline after {timeout:g} seconds"
                                )
                            chunk = read_chunk(min(8192, max_bytes + 1 - total))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            total += len(chunk)
                        raw = b"".join(chunks)
                except (OSError, http.client.IncompleteRead) as exc:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise FetchTimeout(
                            f"fetch deadline after {timeout:g} seconds"
                        ) from exc
                    raise
                headers: Mapping[str, str] = getattr(response, "headers", {})
    finally:
        _fetch_local.socket = previous_socket
    if len(raw) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")
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
    levels = DEFAULT_THRESHOLDS if thresholds is None else thresholds
    try:
        return levels[name.lower()]
    except KeyError:
        raise ValueError(f"no thresholds configured for {name!r}") from None


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
