"""自主生活与主动中枢 · WebUI 后端接口。

面板只做观测与轻量调节，真正的调度逻辑仍然全在 main.py 的协调中枢里。
这里所有函数都接受插件实例，不持有自己的状态，热重载时不会留下残留。
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response
from astrbot.api.web import request as web_request

from . import name_resolver
from . import panel_common, panel_status, panel_config, panel_actions, panel_prefs
from .panel_config import EDITABLE  # 重新导出，保持向后兼容

PLUGIN_NAME = "astrbot_plugin_proactive_life"
API_PREFIX = f"/{PLUGIN_NAME}/panel"


def _ts(value: Any) -> float | None:
    """委托给 panel_common._ts（拆分后保持单一实现）。"""
    return panel_common._ts(value)


def _fmt(value: Any) -> str | None:
    """委托给 panel_common._fmt（拆分后保持单一实现）。"""
    return panel_common._fmt(value)


def _left(until_ts: float | None, now: float) -> int:
    """委托给 panel_common._left（拆分后保持单一实现）。"""
    return panel_common._left(until_ts, now)


def _split_origin(origin: str) -> tuple[str, str]:
    """委托给 panel_common._split_origin（拆分后保持单一实现）。"""
    return panel_common._split_origin(origin)


def _quiet_hours(cfg: dict) -> list[int]:
    """委托给 panel_common._quiet_hours（拆分后保持单一实现）。"""
    return panel_common._quiet_hours(cfg)


# ---------------------------------------------------------------- status


def _mood_snapshot(plugin, key: str) -> dict:
    """委托给 panel_status._mood_snapshot（拆分后保持单一实现）。"""
    return panel_status._mood_snapshot(plugin, key)


def _private_block(plugin, now: float, now_dt: datetime.datetime) -> dict:
    """委托给 panel_status._private_block（拆分后保持单一实现）。"""
    return panel_status._private_block(plugin, now, now_dt)


def _group_block(plugin, now: float) -> dict:
    """委托给 panel_status._group_block（拆分后保持单一实现）。"""
    return panel_status._group_block(plugin, now)


def _rhythm_block(plugin) -> dict:
    """委托给 panel_status._rhythm_block（拆分后保持单一实现）。"""
    return panel_status._rhythm_block(plugin)


def build_status(plugin) -> dict:
    """委托给 panel_status.build_status（拆分后保持单一实现）。"""
    return panel_status.build_status(plugin)


def _identity_block(plugin) -> dict:
    """委托给 panel_status._identity_block（拆分后保持单一实现）。"""
    return panel_status._identity_block(plugin)


def _debounce_block(plugin) -> dict:
    """委托给 panel_status._debounce_block（拆分后保持单一实现）。"""
    return panel_status._debounce_block(plugin)


def _conflicts_block(plugin) -> list[dict]:
    """委托给 panel_status._conflicts_block（拆分后保持单一实现）。"""
    return panel_status._conflicts_block(plugin)


def _schedule_block(cfg) -> dict:
    """委托给 panel_status._schedule_block（拆分后保持单一实现）。"""
    return panel_status._schedule_block(cfg)


def _activity_block(plugin) -> list[dict]:
    """委托给 panel_status._activity_block（拆分后保持单一实现）。"""
    return panel_status._activity_block(plugin)


def _context_block(plugin) -> dict:
    """委托给 panel_status._context_block（拆分后保持单一实现）。"""
    return panel_status._context_block(plugin)


def _members_block(gh, cfg) -> dict:
    """委托给 panel_status._members_block（拆分后保持单一实现）。"""
    return panel_status._members_block(gh, cfg)


def _summary_block(gh, cfg) -> dict:
    """委托给 panel_status._summary_block（拆分后保持单一实现）。"""
    return panel_status._summary_block(gh, cfg)


def _quiet_left(hours: list[int], now_dt: datetime.datetime) -> int:
    """委托给 panel_status._quiet_left（拆分后保持单一实现）。"""
    return panel_status._quiet_left(hours, now_dt)


def _motive_block(plugin) -> dict:
    """委托给 panel_status._motive_block（拆分后保持单一实现）。"""
    return panel_status._motive_block(plugin)


# ---------------------------------------------------------------- config


async def _persona_options(plugin) -> list[dict]:
    """委托给 panel_config._persona_options（拆分后保持单一实现）。"""
    return await panel_config._persona_options(plugin)


def _model_options(plugin) -> list[dict]:
    """委托给 panel_config._model_options（拆分后保持单一实现）。"""
    return panel_config._model_options(plugin)


async def build_config(plugin) -> dict:
    """委托给 panel_config.build_config（拆分后保持单一实现）。"""
    return await panel_config.build_config(plugin)


def _coerce(key: str, value: Any, typ: type) -> Any:
    """委托给 panel_config._coerce（拆分后保持单一实现）。"""
    return panel_config._coerce(key, value, typ)


def apply_config(plugin, patch: dict) -> dict:
    """委托给 panel_config.apply_config（拆分后保持单一实现）。"""
    return panel_config.apply_config(plugin, patch)


# ---------------------------------------------------------------- action


async def do_action(plugin, payload: dict) -> dict:
    """委托给 panel_actions.do_action（拆分后保持单一实现）。"""
    return await panel_actions.do_action(plugin, payload)


# ---------------------------------------------------------------- handlers


def make_handlers(plugin):
    """把接口函数绑到插件实例上，返回 {route_suffix: (handler, methods, desc)}。"""

    async def api_status():
        try:
            return json_response(build_status(plugin))
        except Exception as exc:
            logger.warning(f"proactive_life | 面板状态读取失败: {exc}", exc_info=True)
            return error_response("状态读取失败", status_code=500)

    async def api_config():
        if web_request.method.upper() == "POST":
            payload = await web_request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体不是 JSON 对象", status_code=400)
            return json_response(apply_config(plugin, payload))
        try:
            return json_response(await build_config(plugin))
        except Exception as exc:
            logger.warning(f"proactive_life | 面板配置读取失败: {exc}")
            return error_response("配置读取失败", status_code=500)

    async def api_action():
        try:
            payload = await web_request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体不是 JSON 对象", status_code=400)
            return json_response(await do_action(plugin, payload))
        except Exception as exc:
            logger.warning("proactive_life | 面板动作执行失败: %s", exc, exc_info=True)
            return error_response("动作执行失败", status_code=500)

    async def api_prefs():
        if web_request.method.upper() == "POST":
            try:
                payload = await web_request.json(default={})
                if not isinstance(payload, dict):
                    return error_response("请求体不是 JSON 对象", status_code=400)
                prefs = panel_prefs.save_prefs(payload, plugin)
                return json_response({"ok": True, "prefs": prefs})
            except Exception as exc:
                logger.warning("proactive_life | 面板偏好保存失败: %s", exc)
                return error_response("偏好保存失败", status_code=500)
        try:
            return json_response({"ok": True, "prefs": panel_prefs.load_prefs(plugin)})
        except Exception as exc:
            logger.warning("proactive_life | 面板偏好读取失败: %s", exc)
            return error_response("偏好读取失败", status_code=500)

    return {
        "status": (api_status, ["GET"], "主动中枢：运行状态快照"),
        "config": (api_config, ["GET", "POST"], "主动中枢：读取与写入配置"),
        "action": (api_action, ["POST"], "主动中枢：日程生成与保存"),
        "prefs": (api_prefs, ["GET", "POST"], "主动中枢：面板本地偏好读写"),
    }


def register(plugin) -> None:
    for suffix, (handler, methods, desc) in make_handlers(plugin).items():
        plugin.context.register_web_api(
            f"{API_PREFIX}/{suffix}", handler, methods, desc
        )
    logger.info("proactive_life | 面板接口已注册 %s", API_PREFIX)
