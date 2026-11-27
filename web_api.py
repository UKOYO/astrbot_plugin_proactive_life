"""自主生活与主动中枢 · WebUI 后端接口。

面板只做观测与轻量调节，真正的调度逻辑仍然全在 main.py 的协调中枢里。
这里所有函数都接受插件实例，不持有自己的状态，热重载时不会留下残留。

各业务函数都在 panel_* 模块里实现，这里只做**路由注册**。
以前这里有一百多行「纯转发壳」（``_ts`` / ``_fmt`` / ``_private_block`` …），
拆包时留下是为了兼容旧引用；现在改成直接 re-export，少一层样板也少一处漂移。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.web import error_response, json_response
from astrbot.api.web import request as web_request

from . import panel_common, panel_config, panel_actions, panel_prefs, panel_status
from .panel_actions import do_action
from .panel_common import _fmt, _left, _quiet_hours, _split_origin, _ts
from .panel_config import (  # 重新导出，保持向后兼容
    EDITABLE,
    _coerce,
    _model_options,
    _persona_options,
    apply_config,
    build_config,
)
from .panel_status import (
    _activity_block,
    _conflicts_block,
    _context_block,
    _debounce_block,
    _group_block,
    _identity_block,
    _members_block,
    _mood_snapshot,
    _motive_block,
    _private_block,
    _quiet_left,
    _rhythm_block,
    _schedule_block,
    _summary_block,
    build_status,
)

PLUGIN_NAME = "astrbot_plugin_proactive_life"
API_PREFIX = f"/{PLUGIN_NAME}/panel"

# 上面这些 import 里有一部分是给旧调用方保留的别名，运行时不直接用到
__all__ = [
    "API_PREFIX", "PLUGIN_NAME", "EDITABLE", "apply_config", "build_config",
    "build_status", "do_action", "make_handlers", "register",
    "panel_common", "panel_config", "panel_actions", "panel_prefs", "panel_status",
    "_ts", "_fmt", "_left", "_split_origin", "_quiet_hours", "_mood_snapshot",
    "_private_block", "_group_block", "_rhythm_block", "_identity_block",
    "_debounce_block", "_conflicts_block", "_schedule_block", "_activity_block",
    "_context_block", "_members_block", "_summary_block", "_quiet_left",
    "_motive_block", "_persona_options", "_model_options", "_coerce",
]


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
                logger.warning(f"proactive_life | 面板偏好保存失败: {exc}")
                return error_response("偏好保存失败", status_code=500)
        try:
            return json_response({"ok": True, "prefs": panel_prefs.load_prefs(plugin)})
        except Exception as exc:
            logger.warning(f"proactive_life | 面板偏好读取失败: {exc}")
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
