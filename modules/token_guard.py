"""闲聊限长：把「随口聊两句」的请求压短，直接省输出 Token。

为什么不能只写提示词：提示词约束是「建议」，低智模型照样写小作文。
真正的物理限长要落在请求参数上。

**本版 AstrBot 的限制**：``ProviderRequest`` 没有请求级 ``max_tokens`` /
``frequency_penalty`` 字段，``openai_source`` 只从
``provider_config["custom_extra_body"]`` 读这两个参数。所以这里的做法是：

1. ``on_llm_request`` 判定为纯闲聊 → 备份 provider 的 ``custom_extra_body``，
   临时写入 ``max_tokens`` / ``frequency_penalty``，记下备份与时间；
2. ``on_llm_response`` / ``after_message_sent`` → 立刻还原；
3. 自愈：下一次 ``on_llm_request`` 发现上一次覆盖超过 ``_STALE_SEC`` 秒仍未
   还原（请求异常/被中断），先无条件还原，避免 provider 被永久锁在 160 tokens。

**已知边界（必须知道）**：同一 provider 上如果两个会话的请求落在同一个覆盖
窗口里，另一个会话也会吃到限长。个人自用（单活跃会话）无感；多群高并发请把
``拟人_闲聊限长启用`` 关掉，只保留提示词级约束。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

# 上一次覆盖的存活上限：超过就当异常残留，下次请求先还原
_STALE_SEC = 120.0

# 命中这些词说明是在干活/排障，不压限长（代码、长文、方案都需要展开）
_TECH_WORDS = (
    "代码", "报错", "错误", "异常", "函数", "接口", "配置", "部署", "安装",
    "脚本", "正则", "sql", "json", "python", "traceback", "stack", "编译",
    "docker", "git", "命令", "日志", "为什么", "怎么实现", "原理", "区别",
)

# 闲聊判定的消息长度上限（超过就不算「随口一句」）
_CASUAL_MAX_LEN = 30

# 模块级：当前生效的覆盖（同一时刻只保留一份，简化还原逻辑）
_active: dict[str, Any] = {}


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def is_casual_chat(event: Any, req: Any, cfg: Any) -> bool:
    """这条请求是不是「纯闲聊」——只有闲聊才值得压限长。"""
    try:
        text = str(getattr(event, "message_str", "") or "").strip()
        if not text or len(text) > _CASUAL_MAX_LEN:
            return False
        low = text.lower()
        if "```" in text or any(w in low for w in _TECH_WORDS):
            return False
        # 工具链/工具结果在跑：这一轮要产出调用或长解释，别压
        if getattr(req, "tool_calls_result", None):
            return False
        func_tool = getattr(req, "func_tool", None)
        if func_tool is not None:
            empty = getattr(func_tool, "empty", None)
            try:
                if callable(empty) and not empty():
                    return False
            except Exception:  # noqa: BLE001
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


def _extra_body(provider: Any) -> dict | None:
    """取 provider 的 custom_extra_body（没有就不支持本机制）。"""
    conf = getattr(provider, "provider_config", None)
    if not isinstance(conf, dict):
        return None
    body = conf.get("custom_extra_body")
    if body is None:
        conf["custom_extra_body"] = {}
        body = conf["custom_extra_body"]
    return body if isinstance(body, dict) else None


def restore(provider: Any = None) -> bool:
    """还原上一次的临时覆盖；返回是否真的还原过。"""
    global _active
    if not _active:
        return False
    target = provider if provider is not None else _active.get("provider")
    body = _extra_body(target) if target is not None else None
    if body is None:
        _active = {}
        return False
    backup = _active.get("backup") or {}
    try:
        body.clear()
        body.update(backup)
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 闲聊限长还原失败: %s", e)
    _active = {}
    return True


def _restore_if_stale(now: float) -> None:
    if not _active:
        return
    if now - float(_active.get("at", 0.0)) < _STALE_SEC:
        return
    logger.debug("proactive_life | 闲聊限长残留超时，自动还原 provider 参数")
    restore()


def apply_casual_cap(plugin: Any, event: Any, req: Any) -> bool:
    """纯闲聊时给 provider 临时压 max_tokens / 提高频率惩罚。

    返回是否施加了覆盖（调用方无需处理失败）。
    """
    now = time.time()
    _restore_if_stale(now)
    if _active:
        # 上一次还没还原（并发/异常）：不再叠加，避免把备份覆盖成别人的值
        return False
    cfg = getattr(plugin, "cfg", None)
    # 默认关闭：本版只能临时改 provider 的共享参数，多群并发会串味，
    # 要省输出 Token 的用户自己打开（单活跃会话无感）。
    if not bool(_cfg(cfg, "拟人_闲聊限长启用", False)):
        # 面板关掉开关时把残留覆盖还原，不然 provider 会一直停在 160 tokens
        restore()
        return False
    try:
        if not is_casual_chat(event, req, cfg):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        # 本文件在 modules/ 子包下，common 在插件根目录：必须用 ..common。
        # 写成 .common 会 ModuleNotFoundError，而这里紧跟着 except 会把它吞掉，
        # 结果「闲聊限长」永远返回 False（开关打开也不生效）。
        from ..common import get_chat_provider

        umo = str(getattr(event, "unified_msg_origin", "") or "")
        provider = get_chat_provider(getattr(plugin, "context", None), umo or None)
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 闲聊限长取模型失败，按不启用处理: %s", e)
        return False
    if provider is None:
        return False
    body = _extra_body(provider)
    if body is None:
        return False
    try:
        cap = int(_cfg(cfg, "拟人_闲聊限长max_tokens", 160) or 160)
    except (TypeError, ValueError):
        cap = 160
    try:
        penalty = float(_cfg(cfg, "拟人_闲聊限长频率惩罚", 0.35) or 0.35)
    except (TypeError, ValueError):
        penalty = 0.35
    cap = max(32, min(4096, cap))
    penalty = max(0.0, min(2.0, penalty))
    backup = dict(body)
    try:
        body["max_tokens"] = cap
        body["frequency_penalty"] = penalty
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 闲聊限长写入失败: %s", e)
        return False
    _active.update({"provider": provider, "backup": backup, "at": now})
    logger.debug("proactive_life | 闲聊限长生效（max_tokens=%d, freq=%.2f）", cap, penalty)
    return True
