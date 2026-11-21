from __future__ import annotations

import argparse
import configparser
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .sources import SOURCES
from .sources.common import (
    DEFAULT_CUSTOMERS,
    DEFAULT_LEVELS,
    Thresholds,
    thresholds_from,
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    sources: tuple[str, ...]
    serve: bool
    host: str
    port: int
    timeout: float
    ttl: float
    format: str
    indent: int | None
    config_path: str | None
    customers: dict[str, int]
    levels: tuple[float, float, float]
    thresholds: dict[str, Thresholds]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tupa",
        description="Aggregate Argentine power-grid status as JSON.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "sources",
        nargs="*",
        default=None,
        metavar="SOURCE",
        help=f"sources to query (default: all): {', '.join(SOURCES)}",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        dest="source_flags",
        metavar="SOURCE",
        help="select one source (repeatable)",
    )
    parser.add_argument(
        "--serve", action="store_true", default=None, help="run the HTTP daemon"
    )
    parser.add_argument("--host", default=None, metavar="ADDR")
    parser.add_argument("--port", type=str, default=None, metavar="PORT")
    parser.add_argument("--timeout", type=str, default=None, metavar="SECONDS")
    parser.add_argument("--ttl", type=str, default=None, metavar="SECONDS")
    parser.add_argument("--indent", type=str, default=None, metavar="SPACES")
    parser.add_argument("--format", choices=("json",), default=None)
    parser.add_argument("--config", default=None, metavar="PATH")
    parser.add_argument("--customers", default=None, metavar="NAME=COUNT,...")
    parser.add_argument("--levels", default=None, metavar="WARN,HIGH,DISASTER")
    return parser


def _merge_sources(positional, flags):
    parts: list[str] = []
    for group in (positional, flags):
        if group:
            parts.extend(group)
    return tuple(parts) if parts else None


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _read_ini(path):
    if not path:
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(Path(path)):
        raise ConfigError(f"config file not found: {path}")
    return dict(parser.items("tupa")) if parser.has_section("tupa") else {}


def _as_sources(value):
    if value is None:
        return ()
    parts = value.replace(";", ",").split(",") if isinstance(value, str) else list(value)
    return tuple(dict.fromkeys(part.strip() for part in parts if part.strip()))


def _as_bool(value, label):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{label}: expected a boolean, got {value!r}")


def _as_int(value, label, low, high):
    try:
        result = int(str(value).strip())
    except ValueError:
        raise ConfigError(f"{label}: expected an integer, got {value!r}") from None
    if not low <= result <= high:
        raise ConfigError(f"{label}: must be between {low} and {high}")
    return result


def _as_float(value, label, low):
    try:
        result = float(str(value).strip())
    except ValueError:
        raise ConfigError(f"{label}: expected a number, got {value!r}") from None
    if result < low:
        raise ConfigError(f"{label}: must be >= {low}")
    return result


def _as_customers(value, defaults):
    result = dict(defaults)
    if value is None:
        return result
    text = str(value).strip()
    if not text:
        raise ConfigError("customers: empty value")
    for part in text.split(","):
        name, separator, count = part.partition("=")
        name = name.strip().lower()
        if not separator or not re.fullmatch(r"[a-z0-9_]+", name):
            raise ConfigError(f"customers: expected name=count, got {part.strip()!r}")
        result[name] = _as_int(count.strip(), f"customers[{name}]", 1, 10**9)
    return result


def _as_levels(value, defaults):
    if value is None:
        return tuple(defaults)
    parts = [
        part.strip()
        for part in str(value).replace(";", ",").split(",")
        if part.strip()
    ]
    if len(parts) != 3:
        raise ConfigError("levels: expected three fractions: warn,high,disaster")
    levels = tuple(_as_float(part, "levels", 0.0) for part in parts)
    if any(level <= 0 for level in levels):
        raise ConfigError("levels: fractions must be greater than zero")
    if any(first >= second for first, second in zip(levels, levels[1:])):
        raise ConfigError("levels: fractions must be increasing")
    if levels[-1] > 1:
        raise ConfigError("levels: fractions must be at most 1")
    return levels


def load_config(argv: Sequence[str] | None = None) -> Config:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config_path = (
            args.config if args.config is not None else os.environ.get("TUPA_CONFIG")
        )
        ini = _read_ini(config_path)

        def pick(cli, env_name, ini_name, default):
            return _first(cli, os.environ.get(env_name), ini.get(ini_name), default)

        raw_sources = pick(
            _merge_sources(args.sources, args.source_flags),
            "TUPA_SOURCES",
            "sources",
            None,
        )
        sources = _as_sources(raw_sources)
        unknown = [name for name in sources if name not in SOURCES]
        if unknown:
            raise ConfigError(
                f"unknown source(s): {', '.join(unknown)}; available: {', '.join(SOURCES)}"
            )

        serve = _as_bool(pick(args.serve, "TUPA_SERVE", "serve", False), "serve")
        host = str(pick(args.host, "TUPA_HOST", "host", "127.0.0.1"))
        port = _as_int(pick(args.port, "TUPA_PORT", "port", 8000), "port", 1, 65535)
        timeout = _as_float(
            pick(args.timeout, "TUPA_TIMEOUT", "timeout", 10.0), "timeout", 0.1
        )
        ttl = _as_float(pick(args.ttl, "TUPA_TTL", "ttl", 60.0), "ttl", 0.0)
        indent = _as_int(pick(args.indent, "TUPA_INDENT", "indent", 2), "indent", 0, 8)
        fmt = str(pick(args.format, "TUPA_FORMAT", "format", "json"))
        if fmt != "json":
            raise ConfigError(f"format: unsupported value {fmt!r}")
        customers = _as_customers(
            pick(args.customers, "TUPA_CUSTOMERS", "customers", None),
            DEFAULT_CUSTOMERS,
        )
        levels = _as_levels(
            pick(args.levels, "TUPA_LEVELS", "levels", None), DEFAULT_LEVELS
        )
        thresholds = thresholds_from(customers, levels)
    except ConfigError as exc:
        parser.error(str(exc))

    return Config(
        sources=sources,
        serve=serve,
        host=host,
        port=port,
        timeout=timeout,
        ttl=ttl,
        format=fmt,
        indent=indent,
        config_path=config_path,
        customers=customers,
        levels=levels,
        thresholds=thresholds,
    )
