from __future__ import annotations

import http.client
import ipaddress
import math
import multiprocessing
import multiprocessing.connection
import re
import socket
import ssl
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPSHandler,
    HTTPRedirectHandler,
    Request,
    build_opener,
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


_registry_lock = threading.Lock()
_sockets: dict[object, list[socket.socket]] = {}
_cancelled: set[object] = set()
_fetch_local = threading.local()


def begin_fetch(token: object) -> None:
    with _registry_lock:
        _sockets.setdefault(token, [])


def set_fetch_token(token: object) -> None:
    _fetch_local.token = token
    _fetch_local.socket = None


def clear_fetch_token() -> None:
    _fetch_local.token = None
    _fetch_local.socket = None


def _current_token() -> object:
    token = getattr(_fetch_local, "token", None)
    if token is None:
        token = object()
        begin_fetch(token)
        _fetch_local.token = token
    return token


def register_socket(sock: socket.socket, token: object | None = None) -> None:
    key = token if token is not None else _current_token()
    with _registry_lock:
        socks = _sockets.setdefault(key, [])
        if all(existing is not sock for existing in socks):
            socks.append(sock)


def unregister_socket(sock: socket.socket, token: object | None = None) -> None:
    key = token if token is not None else _current_token()
    with _registry_lock:
        socks = _sockets.get(key)
        if socks is None:
            return
        for index, existing in enumerate(socks):
            if existing is sock:
                socks.pop(index)
                break
        if not socks:
            _sockets.pop(key, None)


def unregister_fetch(token: object) -> None:
    with _registry_lock:
        _sockets.pop(token, None)


def cancel_fetch(token: object) -> bool:
    with _registry_lock:
        socks = _sockets.pop(token, None)
        if socks is None:
            return False
        _cancelled.add(token)
    for sock in socks:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
    return True


def clear_cancellation(token: object | None = None) -> None:
    key = token if token is not None else _current_token()
    with _registry_lock:
        _cancelled.discard(key)


def is_cancelled(token: object | None = None) -> bool:
    key = token if token is not None else _current_token()
    with _registry_lock:
        return key in _cancelled


def active_sockets() -> int:
    with _registry_lock:
        return sum(len(socks) for socks in _sockets.values())


_original_getaddrinfo = socket.getaddrinfo
_dns_local = threading.local()


def _dns_timeout_errno() -> int:
    return getattr(socket, "EAI_TIMEDOUT", socket.EAI_AGAIN)


def _is_numeric_host(host: object) -> bool:
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _resolve_child(conn, host, port, family, socktype, proto, flags) -> None:
    try:
        results = _original_getaddrinfo(host, port, family, socktype, proto, flags)
        conn.send(("ok", results))
    except socket.gaierror as exc:
        code = exc.args[0] if exc.args else socket.EAI_AGAIN
        message = exc.args[1] if len(exc.args) > 1 else str(exc)
        conn.send(("error", code, message))
    except Exception as exc:
        conn.send(("error", socket.EAI_FAIL, f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def _bounded_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    deadline = getattr(_dns_local, "deadline", None)
    if deadline is None or _is_numeric_host(host):
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    remaining = max(0.001, deadline - time.monotonic())
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_resolve_child,
        args=(sender, host, port, family, type, proto, flags),
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        sender.close()
        if not multiprocessing.connection.wait([receiver], timeout=remaining):
            raise socket.gaierror(_dns_timeout_errno(), "name resolution timed out")
        try:
            payload = receiver.recv()
        except EOFError as exc:
            raise socket.gaierror(socket.EAI_AGAIN, "name resolution failed") from exc
        if payload[0] == "ok":
            return payload[1]
        raise socket.gaierror(payload[1], payload[2])
    finally:
        sender.close()
        receiver.close()
        if started:
            if process.is_alive():
                process.terminate()
                process.join(0.2)
            if process.is_alive():
                process.kill()
            process.join()


@contextmanager
def dns_deadline(deadline: float | None):
    previous_dns = getattr(_dns_local, "deadline", None)
    previous_fetch = getattr(_fetch_local, "deadline", None)
    _dns_local.deadline = deadline
    _fetch_local.deadline = deadline
    try:
        yield
    finally:
        _dns_local.deadline = previous_dns
        _fetch_local.deadline = previous_fetch


socket.getaddrinfo = _bounded_getaddrinfo


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
        deadline = getattr(_fetch_local, "deadline", None)
        if deadline is None:
            sock = socket.create_connection(address, timeout, source_address, **kwargs)
        else:
            sock = _connect_with_deadline(address, timeout, source_address, **kwargs)
        register_socket(sock)
        if deadline is not None:
            sock.settimeout(_remaining(deadline))
        _fetch_local.socket = sock
        return sock


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def _create_connection(self, address, timeout=None, source_address=None, **kwargs):
        deadline = getattr(_fetch_local, "deadline", None)
        if deadline is None:
            sock = socket.create_connection(address, timeout, source_address, **kwargs)
        else:
            sock = _connect_with_deadline(address, timeout, source_address, **kwargs)
        register_socket(sock)
        if deadline is not None:
            sock.settimeout(_remaining(deadline))
        _fetch_local.socket = sock
        return sock

    def connect(self) -> None:
        super().connect()
        register_socket(self.sock)
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


def _is_private_target(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return True
        if not infos:
            return True
        return any(
            ipaddress.ip_address(info[4][0]).is_private
            or ipaddress.ip_address(info[4][0]).is_loopback
            or ipaddress.ip_address(info[4][0]).is_link_local
            or ipaddress.ip_address(info[4][0]).is_reserved
            for info in infos
        )
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    )


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allow_private: bool = False):
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme.lower() not in ("http", "https") or (
            not self.allow_private and _is_private_target(target.hostname or "")
        ):
            raise HTTPError(req.full_url, code, "unsafe redirect target", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request: Request, *, timeout: float, context: ssl.SSLContext | None = None):
    initial = urlsplit(request.full_url)
    allow_private = bool(initial.hostname and _is_private_target(initial.hostname))
    opener = build_opener(
        _RestrictedRedirectHandler(allow_private),
        _DeadlineHTTPHandler(),
        _DeadlineHTTPSHandler(context=context),
    )
    return opener.open(request, timeout=timeout)


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
    token = _current_token()
    if is_cancelled(token):
        raise FetchTimeout("fetch cancelled before it started")
    remaining = _remaining(deadline, timeout)
    assert remaining is not None
    request = Request(url, headers={"User-Agent": USER_AGENT})
    previous_socket = getattr(_fetch_local, "socket", None)
    _fetch_local.socket = None
    try:
        with dns_deadline(deadline):
            with urlopen(request, timeout=remaining, context=tls_context) as response:
                sock = _response_socket(response) or getattr(_fetch_local, "socket", None)
                if sock is not None:
                    register_socket(sock, token)
                    if deadline is not None:
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
                            if is_cancelled(token):
                                raise FetchTimeout(
                                    f"fetch cancelled after {timeout:g} seconds"
                                )
                            chunk = read_chunk(min(8192, max_bytes + 1 - total))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            total += len(chunk)
                        raw = b"".join(chunks)
                except (OSError, http.client.IncompleteRead) as exc:
                    if is_cancelled(token):
                        raise FetchTimeout(
                            f"fetch cancelled after {timeout:g} seconds"
                        ) from exc
                    raise
                headers: Mapping[str, str] = getattr(response, "headers", {})
    finally:
        unregister_fetch(token)
        clear_cancellation(token)
        _fetch_local.socket = previous_socket
    if len(raw) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding), headers
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), headers


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
    MAX_DEPTH = 64

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

    def _value(self, depth: int = 0):
        if depth > self.MAX_DEPTH:
            raise ValueError(f"data nesting exceeds {self.MAX_DEPTH} levels")
        self._skip()
        if self.pos >= len(self.text):
            raise ValueError("unexpected end of data")
        char = self.text[self.pos]
        if char in ("'", '"'):
            return self._string()
        if char == "[":
            return self._array(depth + 1)
        if char == "{":
            return self._object(depth + 1)
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
                if esc == "u":
                    out.append(self._unicode_char())
                elif esc == "x":
                    out.append(chr(self._hex(2)))
                elif esc in "\r\n":
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

    def _hex(self, size: int) -> int:
        digits = self.text[self.pos : self.pos + size]
        if len(digits) != size or not re.fullmatch(rf"[0-9A-Fa-f]{{{size}}}", digits):
            raise ValueError(f"invalid hex escape at offset {self.pos}")
        self.pos += size
        return int(digits, 16)

    def _hex4(self) -> int:
        try:
            return self._hex(4)
        except ValueError:
            raise ValueError(f"invalid unicode escape at offset {self.pos - 2}") from None

    def _unicode_char(self) -> str:
        code = self._hex4()
        if 0xD800 <= code <= 0xDBFF and self.text[self.pos : self.pos + 2] == "\\u":
            saved = self.pos
            self.pos += 2
            try:
                low = self._hex4()
            except ValueError:
                self.pos = saved
            else:
                if 0xDC00 <= low <= 0xDFFF:
                    return chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00))
                self.pos = saved
        if 0xD800 <= code <= 0xDFFF:
            return "\ufffd"
        return chr(code)

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

    def _array(self, depth: int) -> list:
        self._expect("[")
        out = []
        while True:
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1
                return out
            out.append(self._value(depth))
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                continue
            self._expect("]")
            return out

    def _object(self, depth: int) -> dict:
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
            out[key] = self._value(depth)
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
    if not text or not re.fullmatch(r"[\d.,]+", text):
        return None
    separators = list(re.finditer(r"[.,]", text))
    if not separators:
        return int(text)
    tail = text[separators[-1].end() :]
    if len(tail) == 3 and tail.isdigit():
        cleaned = text.replace(".", "").replace(",", "")
    else:
        cleaned = text[: separators[-1].start()].replace(".", "").replace(",", "")
    return int(cleaned) if cleaned.isdigit() else None


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
