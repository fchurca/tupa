import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def get_version() -> str:
    with _PYPROJECT.open("rb") as config:
        return str(tomllib.load(config)["project"]["version"])
