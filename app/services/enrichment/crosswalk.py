"""Map free-text LMS dispositions to the controlled CallDisposition enum."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.models.enums import CallDisposition

_PATH = Path(__file__).resolve().parents[2] / "data" / "disposition_crosswalk.yaml"


@lru_cache(maxsize=1)
def load_crosswalk() -> dict[str, str]:
    with _PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def map_lms_disposition(value: str | None) -> CallDisposition:
    if not value:
        return CallDisposition.OTHER
    target = load_crosswalk().get(str(value).strip().lower())
    if target and target in CallDisposition._value2member_map_:
        return CallDisposition(target)
    return CallDisposition.OTHER


__all__ = ["load_crosswalk", "map_lms_disposition"]
