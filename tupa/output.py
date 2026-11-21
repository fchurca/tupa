from __future__ import annotations

import json


def render(status: dict, *, indent: int | None = 2) -> bytes:
    return (json.dumps(status, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")
