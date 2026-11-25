"""统一的 MainAgent 装配与运行。

private / group / worker 三处原本各抄一份：
  取会话配置 → 组 MainAgentBuildConfig → 建 ProviderRequest →
  build_main_agent → step_until_done → get_final_llm_resp → 错误串过滤。
差异只有：tool_call_timeout、max_steps、是否预置 func_tool、是否带会话历史。

现在统一到这里，框架 API 变化只需改一处。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .common import is_llm_error_text

# 核心依赖可能在旧版 AstrBot 上缺失，用标志位降级而不是让整个插件导入失败
try:
    from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
    from astrbot.core.provider.entities import ProviderRequest

    AGENT_AVAILABLE = True
except Exception as _e:  # pragma: no cover - 取决于运行环境
    MainAgentBuildConfig = None  # type: ignore[assignment]
    build_main_agent = None  # type: ignore[assignment]
    ProviderRequest = None  # type: ignore[assignment]
    AGENT_AVAILABLE = False
    logger.warning("proactive_life | MainAgent 依赖导入失败，主动发言/破冰/摸鱼将停用: %s", _e)


@dataclass
class AgentOutcome:
    """一轮 agent 执行的结果。"""

    ok: bool = False
    text: str = ""
    reason: str = ""  # ok=False 时的原因，写日志用


def build_agent_config(context: Any, umo: str, *, tool_call_timeout: int = 120):
    """按会话配置组 MainAgentBuildConfig（persona 安全模式跟随 AstrBot 设置）。"""
    try:
        cfg_all = context.get_config(umo=umo) or {}
    except Exception:
        cfg_all = {}
    provider_settings = cfg_all.get("provider_settings", {}) or {}
    persona_cfg = (cfg_all.get("agent_runner", {}).get("config", {}) or {}).get("persona", {})
    return MainAgentBuildConfig(
        tool_call_timeout=tool_call_timeout,
        streaming_response=False,
        llm_safety_mode=persona_cfg.get("safety_mode", True),
        safety_mode_strategy=persona_cfg.get("safety_mode_strategy", "system_prompt"),
        provider_settings=provider_settings,
    )


def make_request(prompt: str, *, func_tool: Any = None):
    """构造带 prompt 的 ProviderRequest；func_tool 为空时不覆盖默认工具集。"""
    req = ProviderRequest()
    req.prompt = prompt
    req.system_prompt = ""
    req.contexts = []
    if func_tool is not None:
        req.func_tool = func_tool
    return req


async def attach_history(
    context: Any,
    req: Any,
    umo: str,
    *,
    create_if_missing: bool = False,
    log_prefix: str = "proactive_life",
) -> None:
    """把会话历史塞进 req.contexts；失败降级为不带历史，不抛。"""
    try:
        cid = await context.conversation_manager.get_curr_conversation_id(umo)
        if not cid and create_if_missing:
            cid = await context.conversation_manager.new_conversation(umo, "system")
        if cid:
            conv = await context.conversation_manager.get_conversation(umo, cid)
            if conv:
                req.conversation = conv
                req.contexts = json.loads(conv.history or "[]")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        logger.debug("%s | 读取会话历史失败，本次不带历史: %s", log_prefix, e)
    if getattr(req, "contexts", None) is None:
        req.contexts = []


async def run_agent(
    context: Any,
    event: Any,
    req: Any,
    *,
    build_cfg: Any = None,
    umo: str = "",
    tool_call_timeout: int = 120,
    max_steps: int = 15,
) -> AgentOutcome:
    """跑一轮 MainAgent，返回最终纯文本。

    所有失败都折成 ok=False（不抛），调用方只需判断 ok。
    """
    if not AGENT_AVAILABLE:
        return AgentOutcome(False, reason="MainAgent 依赖不可用")
    try:
        if build_cfg is None:
            build_cfg = build_agent_config(
                context, umo or getattr(event, "unified_msg_origin", ""),
                tool_call_timeout=tool_call_timeout,
            )
        result = await build_main_agent(
            event=event, plugin_context=context, config=build_cfg, req=req
        )
        if not result:
            return AgentOutcome(False, reason="build_main_agent 返回空")
        runner = result.agent_runner
        async for _ in runner.step_until_done(max_steps):
            pass
        resp = runner.get_final_llm_resp()
        text = (resp.completion_text or "").strip() if resp else ""
        if not text:
            return AgentOutcome(False, reason="模型未返回内容")
        if is_llm_error_text(text):
            return AgentOutcome(False, reason="模型返回错误串")
        return AgentOutcome(True, text=text)
    except Exception as e:  # noqa: BLE001 - 主动链路失败不该冒泡打断调度
        logger.warning("proactive_life | agent 执行异常: %s", e)
        return AgentOutcome(False, reason=str(e)[:120])
