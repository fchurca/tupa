from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from .config import load_config
from .core import collect
from .output import render
from .server import run_server


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = load_config(argv)
    if config.serve:
        run_server(config)
        return 0

    status = collect(
        config.members or None,
        timeout=config.timeout,
        thresholds=config.thresholds,
        demand_thresholds=config.demand_thresholds,
        sources=config.sources,
        member_sources=config.member_sources,
    )
    try:
        sys.stdout.buffer.write(render(status, indent=config.indent))
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return 0
    members = status["members"].values()
    return 1 if members and all(m["status"] == "unknown" for m in members) else 0
