from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def model_name() -> str:
    return os.getenv("JOB_RADAR_MODEL", "gpt-5.6")


def api_key_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def local_today():
    timezone = ZoneInfo(os.getenv("JOB_RADAR_TIMEZONE", "Europe/Zurich"))
    return datetime.now(timezone).date()


def ensure_runtime_dirs(root: Path = ROOT) -> None:
    for name in ("reports", "applications"):
        (root / name).mkdir(parents=True, exist_ok=True)
