"""黑话解析：用独立模型 + AstrBot 工具集（含内置搜索/MCP）解析挖掘出的高频词。

- 模型：走「黑话_解析模型」，留空回退当前聊天模型；
- 工具：context.get_llm_tool_manager().get_full_tool_set()（包含插件工具 + MCP + 搜索）；
- 有工具时跑 MainAgent（允许模型调用搜索查网络流行语），无工具时直接 text_chat；
- 解析结果落到 SlangMiner，供面板展示；失败只记日志，不影响挖掘。
"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 黑话解析"

SYSTEM_PROMPT = """你是群聊黑话解析助手。任务：判断给定高频词里哪些是真正的群黑话/梗/网络用语，并解释含义。
重要的判定标准：只有「看不懂字面意思、需要解释才知道在说什么」的词才算黑话。
像「哈哈哈」「好的」「笑死」「牛逼」「为什么」这种通俗易懂的口水词、日常用语，一律 is_slang=false。
人名、群友昵称、网名、真名、角色名、单纯称呼（如小明、老王、张总、群主、任何人的名字或外号）一律不是黑话，必须标记 is_slang=false。
可以用工具搜索确认网络流行语的真实含义（如果提供了搜索/MCP 工具）。
只输出 JSON 数组，每项：{"word":"词","meaning":"简短含义","is_slang":true/false}。
不要输出其它文字。"""


def _tool_set(context: Any):
    """取完整工具集（插件工具 + MCP + 内置搜索）；失败返回 None。"""
    try:
        mgr = context.get_llm_tool_manager()
        if mgr is None:
            return None
        tool_set = mgr.get_full_tool_set()
        if tool_set is not None and getattr(tool_set, "tools", None):
            return tool_set
    except Exception as e:  # noqa: BLE001
        logger.debug("%s | 工具集获取失败，按无工具处理: %s", _LOG, e)
    return None


def _extract_json(text: str) -> list[dict]:
    """从模型输出里抠出 JSON 数组（兼容代码块包裹）。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except Exception as e:  # noqa: BLE001
        logger.debug("%s | 解析 JSON 失败: %s", _LOG, e)
        return []


async def analyze_slang(
    context: Any,
    cfg: Any,
    words: list[str],
    *,
    event: Any = None,
    umo: str = "",
    known_names: list[str] | None = None,
) -> dict:
    """解析一组高频词，返回 {ok, items, reason}。"""
    words = [str(w).strip() for w in (words or []) if str(w).strip()][:30]
    if not words:
        return {"ok": False, "items": [], "reason": "没有待解析的词"}
    try:
        from ..common import get_chat_provider, get_provider_by_id

        pid = str(cfg.get("黑话_解析模型", "") or "").strip()
        prov = get_provider_by_id(context, pid) or get_chat_provider(context, umo or None)
        if prov is None:
            return {"ok": False, "items": [], "reason": "没有可用的模型"}
        prompt = "请解析这些群里高频词：\n" + "、".join(words)
        if known_names:
            prompt += "\n\n已知群成员/熟人名字（绝对不是黑话，若出现在词表中一律 is_slang=false）：\n" + "、".join(known_names[:60])

        tool_set = _tool_set(context)
        text = ""
        if tool_set is not None:
            # 有工具：跑 agent，允许调用搜索/MCP
            from ..agent_runner import (
                AGENT_AVAILABLE, build_agent_config, make_request, run_agent,
            )

            if AGENT_AVAILABLE:
                req = make_request(prompt, func_tool=tool_set)
                req.system_prompt = SYSTEM_PROMPT
                outcome = await run_agent(
                    context, event, req,
                    build_cfg=build_agent_config(context, umo, tool_call_timeout=90),
                    umo=umo, tool_call_timeout=90, max_steps=8,
                )
                if outcome.ok:
                    text = outcome.text
            else:
                logger.debug("%s | MainAgent 不可用，回退直连", _LOG)

        if not text:
            # 无工具 / agent 失败：直接 text_chat
            resp = await prov.text_chat(prompt=prompt, system_prompt=SYSTEM_PROMPT)
            text = str(getattr(resp, "completion_text", "") or "")

        items = _extract_json(text)
        if not items:
            return {"ok": False, "items": [], "reason": "模型未返回有效解析"}
        return {"ok": True, "items": items, "reason": ""}
    except Exception as e:  # noqa: BLE001
        logger.warning("%s | 解析失败: %s", _LOG, e)
        return {"ok": False, "items": [], "reason": str(e)[:120]}
