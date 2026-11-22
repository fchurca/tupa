import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@lru_cache(maxsize=1)
def get_version() -> str:
    try:
        return version("tupa")
    except PackageNotFoundError:
        pass
    try:
        with _PYPROJECT.open("rb") as config:
            return str(tomllib.load(config)["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return "0+unknown"
