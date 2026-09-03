"""Engine configuration — thresholds, weights and solver settings live in ``config/*.yaml`` (§1, §18).

``load_config()`` reads ``config/thresholds.yaml`` and applies optional project overrides;
``config_hash()`` is the reproducibility key stored with every run (§1, §18).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "thresholds.yaml"


class Config:
    """Read-only nested mapping with dotted access: ``cfg["gates.od_min"]``."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = copy.deepcopy(data)

    def __getitem__(self, key: str) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(key)
            node = node[part]
        return copy.deepcopy(node) if isinstance(node, dict | list) else node

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def section(self, key: str) -> dict[str, Any]:
        value = self[key]
        if not isinstance(value, dict):
            raise KeyError(f"{key} is not a section")
        return value

    def with_overrides(self, overrides: dict[str, Any]) -> Config:
        data = copy.deepcopy(self._data)
        _deep_update(data, overrides)
        return Config(data)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    @property
    def hash(self) -> str:
        return config_hash(self)


def _deep_update(base: dict[str, Any], upd: dict[str, Any]) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    raw = yaml.safe_load((path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    cfg = Config(raw)
    return cfg.with_overrides(overrides) if overrides else cfg


def config_hash(cfg: Config) -> str:
    """Stable SHA-256 of the effective configuration (sorted-key JSON)."""
    blob = json.dumps(cfg.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def seed_from_hash(digest: str, salt: str = "") -> int:
    """Deterministic 32-bit seed derived from a config/data hash (§5 engineering rules)."""
    h = hashlib.sha256((digest + salt).encode("utf-8")).hexdigest()
    return int(h[:8], 16)
