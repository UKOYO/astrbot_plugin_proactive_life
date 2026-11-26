"""天气锚点（融合自 astrbot_plugin_weather）。

Open-Meteo 零 API Key；每日缓存到 safe_io，断网/超时不阻断调用方。
主要给日程生成注入「今日天气/温差」，让穿搭与活动贴合真实天气。
"""

from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 天气"
_API = "https://api.open-meteo.com/v1/forecast"
_CACHE_DAYS = 1


def _cache_path() -> Path:
    try:
        from astrbot.core.star.star_tools import StarTools

        return Path(StarTools.get_data_dir("astrbot_plugin_proactive_life")) / "weather_cache.json"
    except Exception:
        return Path.home() / "AstrBot" / "data" / "plugin_data" / "astrbot_plugin_proactive_life" / "weather_cache.json"


def _load_cache() -> dict:
    try:
        from ..safe_io import sync_read_json
        data = sync_read_json(_cache_path(), {})
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(data: dict) -> None:
    try:
        from ..safe_io import sync_write_json
        sync_write_json(_cache_path(), data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 缓存写入失败: %s", _LOG, exc)


def _geocode(city: str) -> tuple[float, float] | None:
    """用 Open-Meteo geocoding API 解析城市名。"""
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "zh", "format": "json"}
    )
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        results = data.get("results") or []
        if results:
            return float(results[0]["latitude"]), float(results[0]["longitude"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 城市解析失败（%s）: %s", _LOG, city, exc)
    return None


def get_weather(city: str, *, force: bool = False) -> dict:
    """取当日天气；优先缓存。失败返回 {}，绝不抛。"""
    city = str(city or "").strip()
    if not city:
        return {}
    today = datetime.date.today().isoformat()
    cache = _load_cache()
    entry = cache.get(city)
    if isinstance(entry, dict) and entry.get("date") == today and not force:
        return entry

    try:
        coords = _geocode(city)
        if not coords:
            return {}
        params = {
            "latitude": coords[0], "longitude": coords[1],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "timezone": "auto", "forecast_days": 1,
        }
        url = _API + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        daily = (data.get("daily") or {})
        entry = {
            "date": today, "city": city,
            "temp_max": (daily.get("temperature_2m_max") or [None])[0],
            "temp_min": (daily.get("temperature_2m_min") or [None])[0],
            "weather_code": (daily.get("weather_code") or [None])[0],
        }
        cache[city] = entry
        _save_cache(cache)
        logger.info("%s | 已更新 %s 天气: %s", _LOG, city, entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 天气查询失败，按无天气处理: %s", _LOG, exc)
        return entry if isinstance(entry, dict) else {}


def weather_prompt(cfg: Any) -> str:
    """给日程生成用的简短天气描述；关闭/失败返回空串。"""
    try:
        if not bool(cfg.get("模块_天气", True)):
            return ""
        city = str(cfg.get("天气_城市", "") or "").strip()
        if not city:
            return ""
        info = get_weather(city)
        if not info:
            return ""
        hi, lo = info.get("temp_max"), info.get("temp_min")
        parts = [f"当地（{city}）今日天气"]
        if hi is not None and lo is not None:
            parts.append(f"最高 {hi}°C / 最低 {lo}°C")
        code = info.get("weather_code")
        if code is not None:
            parts.append(f"天气代码 {code}")
        return "，".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 天气提示生成失败: %s", _LOG, exc)
        return ""
