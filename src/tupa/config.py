from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path

from .model import SourceSpec, resolve_timezone
from .sources import DEFAULT_MEMBERS, DEFAULT_SOURCES
from .sources.common import (
    DEFAULT_CUSTOMERS,
    DEFAULT_DEMAND_LEVELS,
    DEFAULT_LEVELS,
    Thresholds,
    thresholds_from,
)

log = logging.getLogger(__name__)

CONFIG_KEYS = frozenset(
    {
        "selected_members",
        "members",
        "sources",
        "serve",
        "host",
        "port",
        "timeout",
        "ttl",
        "indent",
        "customers",
        "levels",
        "demand_levels",
        "tz",
    }
)
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class Config:
    members: tuple[str, ...]
    tz: tzinfo
    member_sources: Mapping[str, tuple[str, ...]]
    sources: Mapping[str, SourceSpec]
    serve: bool
    host: str
    port: int
    timeout: float
    ttl: float
    indent: int
    thresholds: Mapping[str, Thresholds]
    demand_thresholds: Thresholds


def _split(value: object, label: str) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)
    else:
        raise ValueError(f"{label} must be a string or a list")
    result: list[str] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ValueError(f"{label} entries must be strings or numbers")
        result.extend(part.strip() for part in str(item).split(",") if part.strip())
    return result


def _names(value: object, label: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in _split(value, label):
        name = item.lower()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"invalid {label} name: {item!r}")
        if name not in result:
            result.append(name)
    return tuple(result)


def _enabled(value: object, label: str, available: Mapping) -> tuple[str, ...]:
    names = _names(value, label)
    if not names:
        return tuple(available)
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    return names


def _bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError(f"invalid boolean for {label}: {value!r}")


def _int(value: object, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid integer for {label}: {value!r}")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"invalid integer for {label}: {value!r}") from None
    if number < minimum or (maximum is not None and number > maximum):
        limit = f"{minimum}-{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{label} must be {limit}")
    return number


def _float(value: object, label: str, minimum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid number for {label}: {value!r}")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"invalid number for {label}: {value!r}") from None
    if number < minimum or number != number or number == float("inf"):
        raise ValueError(f"{label} must be >= {minimum}")
    return number


def _levels(value: object, label: str) -> tuple[float, float, float]:
    items = _split(value, label)
    if len(items) != 3:
        raise ValueError(f"{label} needs exactly three values")
    numbers = tuple(_float(item, label, 0.0) for item in items)
    if not all(0 < number <= 1 for number in numbers):
        raise ValueError(f"{label} must be greater than 0 and at most 1")
    if not numbers[0] < numbers[1] < numbers[2]:
        raise ValueError(f"{label} must be increasing")
    return (numbers[0], numbers[1], numbers[2])


def _customers(value: object, label: str) -> dict[str, int]:
    result: dict[str, int] = {}
    if isinstance(value, Mapping):
        pairs = [f"{name}={count}" for name, count in value.items()]
    else:
        pairs = _split(value, label)
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"invalid {label} entry: {pair!r}")
        raw_name, raw_count = pair.split("=", 1)
        name = raw_name.strip().lower()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"invalid {label} name: {raw_name!r}")
        result[name] = _int(raw_count.strip(), label, 1)
    return result


def load_config(argv: Sequence[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(prog="tupa")
    parser.add_argument("-m", "--member", dest="members", action="append", default=[])
    parser.add_argument("--serve", action="store_true", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)
    parser.add_argument("--timeout", default=None)
    parser.add_argument("--ttl", default=None)
    parser.add_argument("--indent", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--tz", default=None)
    parser.add_argument("--customers", default=None)
    parser.add_argument("--levels", default=None)
    parser.add_argument("--demand-levels", default=None)
    args = parser.parse_args(argv)

    selected_path = args.config or os.environ.get("TUPA_CONFIG")
    config_path = selected_path or "config.json"
    path = Path(config_path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if selected_path:
            log.info("config file %s not found; using defaults", config_path)
        loaded = {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        log.warning(
            "cannot read JSON config %r: %s; using defaults", config_path, exc
        )
        loaded = {}
    if not isinstance(loaded, dict):
        log.warning("JSON config must contain an object in %r; using defaults", config_path)
        loaded = {}
    unknown = set(loaded) - CONFIG_KEYS
    if unknown:
        log.warning(
            "unknown config keys: %s in %r; ignoring them",
            ", ".join(sorted(map(str, unknown))),
            config_path,
        )
        loaded = {key: value for key, value in loaded.items() if key in CONFIG_KEYS}
    file_config = loaded

    def value(key: str, env_key: str, cli: object, default: object) -> object:
        if cli is not None:
            return cli
        if env_key in os.environ:
            return os.environ[env_key]
        if key in file_config:
            return file_config[key]
        return default

    try:
        enabled_members = _enabled(
            file_config.get("members", tuple(DEFAULT_MEMBERS)), "members", DEFAULT_MEMBERS
        )
        enabled_sources = _enabled(
            file_config.get("sources", tuple(DEFAULT_SOURCES)), "sources", DEFAULT_SOURCES
        )
        member_set = set(enabled_members)
        source_set = set(enabled_sources)
        source_defs = {
            name: spec for name, spec in DEFAULT_SOURCES.items() if name in source_set
        }
        member_sources = {
            name: tuple(source for source in sources if source in source_set)
            for name, sources in DEFAULT_MEMBERS.items()
            if name in member_set
        }

        cli_members = args.members
        env_members = (os.environ.get("TUPA_MEMBERS") or "").strip()
        selected = _names(
            cli_members
            or env_members
            or file_config.get("selected_members")
            or tuple(member_sources),
            "members",
        )
        for name in selected:
            if name not in member_sources:
                raise ValueError(f"unknown or disabled member: {name!r}")

        serve = _bool(value("serve", "TUPA_SERVE", args.serve, False), "serve")
        if args.once:
            serve = False
        host = str(value("host", "TUPA_HOST", args.host, "127.0.0.1")).strip()
        if not host:
            raise ValueError("host must not be empty")
        port = _int(value("port", "TUPA_PORT", args.port, 8000), "port", 1, 65535)
        timeout = _float(value("timeout", "TUPA_TIMEOUT", args.timeout, 20.0), "timeout", 0.1)
        ttl = _float(value("ttl", "TUPA_TTL", args.ttl, 60.0), "ttl", 0.0)
        indent = _int(value("indent", "TUPA_INDENT", args.indent, 2), "indent", 0, 8)
        customers = {
            **DEFAULT_CUSTOMERS,
            **_customers(value("customers", "TUPA_CUSTOMERS", args.customers, {}), "customers"),
        }
        tz = resolve_timezone(value("tz", "TUPA_TZ", args.tz, None))
        levels = _levels(value("levels", "TUPA_LEVELS", args.levels, DEFAULT_LEVELS), "levels")
        demand_levels = _levels(
            value("demand_levels", "TUPA_DEMAND_LEVELS", args.demand_levels, DEFAULT_DEMAND_LEVELS),
            "demand_levels",
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    return Config(
        members=selected,
        tz=tz,
        member_sources=member_sources,
        sources=source_defs,
        serve=serve,
        host=host,
        port=port,
        timeout=timeout,
        ttl=ttl,
        indent=indent,
        thresholds=thresholds_from(customers, levels),
        demand_thresholds=Thresholds(*demand_levels),
    )
