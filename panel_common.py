"""面板共用小工具（时间格式化、origin 拆分、免打扰解析）。

从 web_api.py 拆出，不改变任何行为。
"""

from __future__ import annotations

import datetime
from typing import Any


def _ts(value: Any) -> float | None:
    try:
        if not value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str | None:
    ts = _ts(value)
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _left(until_ts: float | None, now: float) -> int:
    if not until_ts:
        return 0
    return max(0, int(round(until_ts - now)))


def _split_origin(origin: str) -> tuple[str, str]:
    """origin 形如 platform_id:message_type:session_id，取平台与末段。"""
    parts = str(origin or "").split(":")
    if len(parts) >= 3:
        return parts[0], parts[2]
    return "", str(origin or "")


def _quiet_hours(cfg: dict) -> list[int]:
    """免打扰时段（统一走 common 的解析，避免多份实现漂移）。"""
    from .common import parse_quiet_hours

    return parse_quiet_hours(cfg.get("免打扰时段"))


