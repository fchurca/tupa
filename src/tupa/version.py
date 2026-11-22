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
        with _PYPROJECT.open("rb") as config:
            return str(tomllib.load(config)["project"]["version"])
