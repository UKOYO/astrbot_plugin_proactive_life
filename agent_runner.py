"""统一的 MainAgent 装配与运行。

private / group / worker 三处原本各抄一份：
  取会话配置 → 组 MainAgentBuildConfig → 建 ProviderRequest →
  build_main_agent → step_until_done → get_final_llm_resp → 错误串过滤。
差异只有：tool_call_timeout、max_steps、是否预置 func_tool、是否带会话历史。

现在统一到这里，框架 API 变化只需改一处。
"""

from __future__ import annotations

import asyncio
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


def clean_agent_text(text: str, cfg: Any) -> str:
    """主动链路出站清洗：思考过滤 + 八股清洗（各自受模块总开关约束）。

    为什么需要：主动开口 / 破冰 / 摸鱼都是自己造 prompt 跑 agent，绕过了
    ``on_llm_response`` / ``on_decorating_result`` 两个钩子——模型吐的
    ``<think>`` 与八股会原样发到群里/私聊。这里复用同一套清洗实现，
    返回空串表示「这条不该发」（清洗后只剩兜底句），由调用方按失败处理。

    用户在面板关掉「思考过滤」/「八股清洗」总开关时，这里必须跟着不生效，
    否则"关了还在改"等于开关失效。
    """
    if not text or not str(text).strip() or cfg is None:
        return str(text or "").strip()
    try:
        from .handler_base import module_enabled

        do_think = module_enabled(cfg, "思考过滤")
        do_polish = module_enabled(cfg, "八股清洗")
    except Exception:  # noqa: BLE001
        do_think, do_polish = True, True
    if not do_think and not do_polish:
        return str(text).strip()
    try:
        from .modules.outbound import clean_llm_text, polish_text

        cleaned = str(text)
        if do_think:
            cleaned, _changed = clean_llm_text(cleaned, cfg)
            cleaned = str(cleaned or "").strip()
            try:
                fallback = str(cfg.get("出站_空回复兜底", "") or "").strip()
            except Exception:  # noqa: BLE001
                fallback = ""
            # 清洗后只剩「空回复兜底」：主动开口时宁可这轮不发，也别把兜底句当自言自语发出去
            if not cleaned or (fallback and cleaned == fallback):
                return ""
        else:
            cleaned = cleaned.strip()
            if not cleaned:
                return ""
        if not do_polish:
            return cleaned
        polished = str(polish_text(cleaned, cfg) or "").strip()
        return polished or cleaned
    except Exception as e:  # noqa: BLE001 - 清洗失败按原文发，不能把主动链路搞挂
        logger.debug("proactive_life | 主动链路出站清洗失败，按原文发送: %s", e)
        return str(text).strip()


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


def max_steps_from(cfg: Any, key: str, default: int) -> int:
    """读「最大步数」配置：缺键/脏值回落默认，钳到 1..40。

    主动链路每一步都是一次模型调用（可能还带工具往返），默认值刻意压低：
    破冰/追问只是「说一句话」，摸鱼才需要多步检索。
    """
    try:
        val = int(cfg.get(key, default) or default)
    except (TypeError, ValueError, AttributeError):
        val = default
    return max(1, min(40, val))


async def run_agent(
    context: Any,
    event: Any,
    req: Any,
    *,
    build_cfg: Any = None,
    umo: str = "",
    tool_call_timeout: int = 120,
    max_steps: int = 15,
    total_timeout: float | None = None,
    clean_cfg: Any = None,
) -> AgentOutcome:
    """跑一轮 MainAgent，返回最终纯文本。

    所有失败都折成 ok=False（不抛），调用方只需判断 ok。
    total_timeout 是整轮硬超时（含全部 step 与工具调用）；None 表示不限，
    由调用方自己控制。main 的调度循环外层有 300s，但面板手动触发等入口
    没有外层保护，所以这里提供兜底。

    clean_cfg 传插件配置时，最终文本会先过「思考过滤 + 八股清洗」再返回
    （主动链路不走 on_llm_response 钩子，不在这里洗就会把 think/八股发出去）。
    """
    if not AGENT_AVAILABLE:
        return AgentOutcome(False, reason="MainAgent 依赖不可用")
    if total_timeout and total_timeout > 0:
        try:
            return await asyncio.wait_for(
                _run_agent_inner(
                    context, event, req,
                    build_cfg=build_cfg, umo=umo,
                    tool_call_timeout=tool_call_timeout, max_steps=max_steps,
                    clean_cfg=clean_cfg,
                ),
                timeout=float(total_timeout),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "proactive_life | agent 整轮超时（%.0fs，max_steps=%d），按失败处理",
                float(total_timeout), max_steps,
            )
            return AgentOutcome(False, reason=f"整轮超时 {total_timeout:.0f}s")
        except Exception as e:  # noqa: BLE001
            logger.warning("proactive_life | agent 执行异常: %s", e)
            return AgentOutcome(False, reason=str(e)[:120])
    return await _run_agent_inner(
        context, event, req,
        build_cfg=build_cfg, umo=umo,
        tool_call_timeout=tool_call_timeout, max_steps=max_steps,
        clean_cfg=clean_cfg,
    )


async def _run_agent_inner(
    context: Any,
    event: Any,
    req: Any,
    *,
    build_cfg: Any = None,
    umo: str = "",
    tool_call_timeout: int = 120,
    max_steps: int = 15,
    clean_cfg: Any = None,
) -> AgentOutcome:
    """run_agent 的实际执行体（不含总超时包装）。"""
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
        if clean_cfg is not None:
            cleaned = clean_agent_text(text, clean_cfg)
            if not cleaned:
                return AgentOutcome(False, reason="出站清洗后为空")
            text = cleaned
        return AgentOutcome(True, text=text)
    except Exception as e:  # noqa: BLE001 - 主动链路失败不该冒泡打断调度
        logger.warning("proactive_life | agent 执行异常: %s", e)
        return AgentOutcome(False, reason=str(e)[:120])
