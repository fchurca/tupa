from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .model import SourceSpec
from .sources import DEFAULT_MEMBERS, DEFAULT_SOURCES, SOURCE_KINDS
from .sources import cammesa
from .sources.common import (
    DEFAULT_CUSTOMERS,
    DEFAULT_DEMAND_LEVELS,
    DEFAULT_LEVELS,
    DEFAULT_DEMAND_THRESHOLDS,
    DemandThresholds,
    Thresholds,
    thresholds_from,
)

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
        "format",
        "indent",
        "customers",
        "levels",
        "demand_levels",
    }
)
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class Config:
    members: tuple[str, ...]
    member_sources: Mapping[str, tuple[str, ...]]
    sources: Mapping[str, SourceSpec]
    serve: bool
    host: str
    port: int
    timeout: float
    ttl: float
    format: str
    indent: int
    config_path: str | None
    customers: Mapping[str, int]
    levels: tuple[float, float, float]
    demand_levels: tuple[float, float, float]
    thresholds: Mapping[str, Thresholds]
    demand_thresholds: DemandThresholds


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
        result.extend(part.strip() for part in re.split(r"[,;]", str(item)) if part.strip())
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


def _member_defs(value: object, label: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for raw_name, sources in value.items():
        if not isinstance(raw_name, str):
            raise ValueError(f"{label} names must be strings")
        name = raw_name.strip().lower()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"invalid {label} name: {raw_name!r}")
        result[name] = _names(sources, f"{label}.{name}.sources")
    return result


def _source_defs(value: object, label: str) -> dict[str, SourceSpec]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result: dict[str, SourceSpec] = {}
    for raw_name, raw_spec in value.items():
        if not isinstance(raw_name, str):
            raise ValueError(f"{label} names must be strings")
        name = raw_name.strip().lower()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"invalid {label} name: {raw_name!r}")
        if not isinstance(raw_spec, Mapping):
            raise ValueError(f"{label}.{name} must be an object")
        base = DEFAULT_SOURCES.get(name)
        kind = raw_spec.get("kind", base.kind if base else None)
        if kind not in SOURCE_KINDS:
            raise ValueError(f"invalid source kind for {name}: {kind!r}")
        member = raw_spec.get("member", base.member if base else None)
        if not isinstance(member, str) or not NAME_RE.fullmatch(member.lower()):
            raise ValueError(f"{label}.{name}.member is required")
        url = raw_spec.get("url", base.url if base else None)
        if url is not None and not isinstance(url, str):
            raise ValueError(f"{label}.{name}.url must be a string")
        empresa = raw_spec.get("empresa", base.empresa if base else None)
        if empresa is not None:
            if not isinstance(empresa, str):
                raise ValueError(f"{label}.{name}.empresa must be a string")
            empresa = empresa.strip().upper()
        region = raw_spec.get("region", base.region if base else None)
        if region is not None:
            region = _int(region, f"{label}.{name}.region", 1)
        if kind == "enre_outages" and (not url or not empresa):
            raise ValueError(f"{label}.{name} needs url and empresa")
        if kind == "cammesa_demand":
            if region is None:
                raise ValueError(f"{label}.{name} needs region")
            if url is None:
                url = cammesa.url_for(region)
        result[name] = SourceSpec(name, kind, member.lower(), url, empresa, region)
    return result


def load_config(argv: Sequence[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(prog="tupa")
    parser.add_argument("members", nargs="*", metavar="MEMBER")
    parser.add_argument("-m", "--member", "--source", dest="member_opt", action="append", default=[])
    parser.add_argument("--serve", action="store_true", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)
    parser.add_argument("--timeout", default=None)
    parser.add_argument("--ttl", default=None)
    parser.add_argument("--format", default=None)
    parser.add_argument("--indent", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--customers", default=None)
    parser.add_argument("--levels", default=None)
    parser.add_argument("--demand-levels", default=None)
    args = parser.parse_args(argv)

    def fail(message: str) -> None:
        parser.error(message)

    config_path = args.config or os.environ.get("TUPA_CONFIG")
    file_config: Mapping[str, object] = {}
    if config_path:
        path = Path(config_path)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            loaded = {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            fail(f"cannot read JSON config {config_path!r}: {exc}")
        if not isinstance(loaded, dict):
            fail("JSON config must contain an object")
        unknown = set(loaded) - CONFIG_KEYS
        if unknown:
            fail("unknown config keys: " + ", ".join(sorted(map(str, unknown))))
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
        raw_members = file_config.get("members", {})
        raw_sources = file_config.get("sources", {})
        member_defs = {**DEFAULT_MEMBERS, **_member_defs(raw_members, "members")}
        source_defs = {**DEFAULT_SOURCES, **_source_defs(raw_sources, "sources")}

        for member, source_names in member_defs.items():
            for source_name in source_names:
                if source_name not in source_defs:
                    raise ValueError(f"member {member!r} uses unknown source {source_name!r}")
                if source_defs[source_name].member != member:
                    raise ValueError(f"source {source_name!r} does not belong to {member!r}")

        selected = _names(
            args.members + (args.member_opt or [])
            or value("selected_members", "TUPA_MEMBERS", None, None)
            or os.environ.get("TUPA_SOURCES")
            or file_config.get("selected_members")
            or tuple(member_defs),
            "members",
        )
        for name in selected:
            if name not in member_defs:
                raise ValueError(f"unknown member: {name!r}")

        serve = _bool(value("serve", "TUPA_SERVE", args.serve, False), "serve")
        host = str(value("host", "TUPA_HOST", args.host, "127.0.0.1")).strip()
        if not host:
            raise ValueError("host must not be empty")
        port = _int(value("port", "TUPA_PORT", args.port, 8000), "port", 1, 65535)
        timeout = _float(value("timeout", "TUPA_TIMEOUT", args.timeout, 10.0), "timeout", 0.1)
        ttl = _float(value("ttl", "TUPA_TTL", args.ttl, 60.0), "ttl", 0.0)
        output_format = str(value("format", "TUPA_FORMAT", args.format, "json")).strip().lower()
        if output_format != "json":
            raise ValueError(f"unsupported format: {output_format!r}")
        indent = _int(value("indent", "TUPA_INDENT", args.indent, 2), "indent", 0, 8)
        customers = {
            **DEFAULT_CUSTOMERS,
            **_customers(value("customers", "TUPA_CUSTOMERS", args.customers, {}), "customers"),
        }
        levels = _levels(value("levels", "TUPA_LEVELS", args.levels, DEFAULT_LEVELS), "levels")
        demand_levels = _levels(
            value("demand_levels", "TUPA_DEMAND_LEVELS", args.demand_levels, DEFAULT_DEMAND_LEVELS),
            "demand_levels",
        )
    except (TypeError, ValueError) as exc:
        fail(str(exc))

    return Config(
        members=selected,
        member_sources=member_defs,
        sources=source_defs,
        serve=serve,
        host=host,
        port=port,
        timeout=timeout,
        ttl=ttl,
        format=output_format,
        indent=indent,
        config_path=config_path,
        customers=customers,
        levels=levels,
        demand_levels=demand_levels,
        thresholds=thresholds_from(customers, levels),
        demand_thresholds=(
            DEFAULT_DEMAND_THRESHOLDS
            if demand_levels == DEFAULT_DEMAND_LEVELS
            else DemandThresholds(*demand_levels)
        ),
    )
