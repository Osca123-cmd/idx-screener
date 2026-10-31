"""Load config.yaml into a nested, attribute-accessible object."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.yaml"


def _to_ns(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in value.items()})
    return value


def load(path: Path | str = DEFAULT_PATH) -> SimpleNamespace:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = _to_ns(raw)
    cfg.database = str(ROOT / cfg.database)
    cfg.universe = str(ROOT / cfg.universe)
    return cfg


@lru_cache(maxsize=1)
def get() -> SimpleNamespace:
    return load()
