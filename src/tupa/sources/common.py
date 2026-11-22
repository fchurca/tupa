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
from urllib.request import Request, urlopen

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
_sockets: dict[int, list[socket.socket]] = {}
_cancelled: set[int] = set()


def register_socket(sock: socket.socket) -> None:
    ident = threading.get_ident()
    with _registry_lock:
        _sockets.setdefault(ident, []).append(sock)


def unregister_socket(sock: socket.socket) -> None:
    ident = threading.get_ident()
    with _registry_lock:
        socks = _sockets.get(ident)
        if socks is None:
            return
        try:
            socks.remove(sock)
        except ValueError:
            pass
        if not socks:
            _sockets.pop(ident, None)


def cancel_thread(ident: int) -> bool:
    with _registry_lock:
        socks = _sockets.pop(ident, None)
        if socks is None:
            return False
        _cancelled.add(ident)
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


def clear_cancellation() -> None:
    with _registry_lock:
        _cancelled.discard(threading.get_ident())


def is_cancelled() -> bool:
    with _registry_lock:
        return threading.get_ident() in _cancelled


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
    previous = getattr(_dns_local, "deadline", None)
    _dns_local.deadline = deadline
    try:
        yield
    finally:
        _dns_local.deadline = previous


socket.getaddrinfo = _bounded_getaddrinfo


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
    clear_cancellation()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    remaining = (
        timeout
        if deadline is None
        else min(timeout, max(0.001, deadline - time.monotonic()))
    )
    sock: socket.socket | None = None
    try:
        with dns_deadline(deadline):
            with urlopen(request, timeout=remaining, context=tls_context) as response:
                sock = _response_socket(response)
                if sock is not None:
                    register_socket(sock)
                try:
                    raw = response.read(max_bytes + 1)
                except (OSError, http.client.IncompleteRead) as exc:
                    if is_cancelled():
                        raise FetchTimeout(
                            f"fetch cancelled after {remaining:g} seconds"
                        ) from exc
                    raise
                headers: Mapping[str, str] = getattr(response, "headers", {})
    finally:
        if sock is not None:
            unregister_socket(sock)
        clear_cancellation()
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
                if esc == "u":
                    out.append(self._unicode_char())
                else:
                    out.append(
                        {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}.get(
                            esc, esc
                        )
                    )
            else:
                out.append(char)
        raise ValueError("unterminated string")

    def _hex4(self) -> int:
        digits = self.text[self.pos : self.pos + 4]
        if len(digits) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
            raise ValueError(f"invalid unicode escape at offset {self.pos - 2}")
        self.pos += 4
        return int(digits, 16)

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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 else None
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
