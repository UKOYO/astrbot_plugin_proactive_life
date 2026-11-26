"""被动回复链路的「当前生活状态」注入。

为什么需要：主动开口（私聊/群聊破冰/摸鱼）的提示词里一直带着日程与穿搭，
但用户主动发消息、走 AstrBot 正常回复链路时，system prompt 里只有人设——
模型不知道自己「今天穿什么、此刻在做什么」，被问「你在干嘛」就只能现编，
编出来的和当天日程、穿搭完全对不上。

实现要点（踩过坑，别随手改）：
1. 挂在 ``extra_user_content_parts`` 而不是 ``system_prompt``：
   - ``system_prompt`` 需要逐字稳定，AstrBot 的 prompt 缓存靠它做前缀命中，
     每轮都变的生活状态塞进去会让缓存整段失效。
   - 部件必须 ``mark_as_temp()``：带 ``_no_save`` 的部件不会写进
     ``conversation.history``（``core.agent.message.dump_messages_with_checkpoints``
     会过滤掉），否则每轮累积一份，越聊上下文越膨胀。
2. 幂等：本次请求里已经有 :data:`MARKER` 就不再挂，防止重复 hook 叠加。
3. 降级：宿主没给 ``extra_user_content_parts``（旧版）时退回 ``system_prompt``
   尾部；任何异常都吞掉并只记 debug —— 注入失败不该把正常回复搞挂。
"""

from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from .common import read_schedule

# 幂等标记：同一请求里见到它就不再注入
MARKER = "【当前生活状态】"

# 日程正文的截断下限/上限，防止配置写飞
_MIN_LIMIT = 60
_MAX_LIMIT = 2000


def _debug(msg: str, err: BaseException | None = None) -> None:
    try:
        if err is None:
            logger.debug("proactive_life | %s", msg)
        else:
            logger.debug("proactive_life | %s: %s", msg, err)
    except Exception:
        pass


def _cfg_get(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:
        return default


def _limit_of(cfg: Any) -> int:
    try:
        limit = int(_cfg_get(cfg, "日程_被动注入字数", 300) or 300)
    except (TypeError, ValueError):
        limit = 300
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def build_life_state_block(
    cfg: Any,
    now: datetime.datetime | None = None,
    info: dict | None = None,
    mood: Any = None,
    motive: str = "",
    session_key: str = "",
    person: str = "",
) -> str:
    """拼出「当前生活状态」文本块；关掉或没有日程时返回空串。

    Args:
        cfg: 插件配置视图（``_LiveConfig`` 或普通 dict）。
        now: 当前时间，仅用于「现在几点」与日程定位，便于测试注入。
        info: 已经读好的日程信息（``read_schedule`` 的返回值）；
            传 None 时自行只读读取，读不到就返回空串。
        mood: 可选 ``MoodStore``；传入时附带当前会话情绪提示（独立开关）。
        motive: 可选 1 条未消费的高优心事摘要（只作自然提及提示，不标记消费）。
        session_key: 取情绪用的会话/对象 key。
    """
    # 模块总开关 + 本次开关，任一关闭都不介入
    try:
        from .handler_base import module_enabled

        if not module_enabled(cfg, "日程"):
            return ""
    except Exception as e:  # noqa: BLE001 - 导入失败时本次不注入
        _debug("模块开关不可用，本次不注入生活状态", e)
        return ""
    if not bool(_cfg_get(cfg, "日程_被动注入", True)):
        return ""

    now = now or datetime.datetime.now()
    if info is None:
        try:
            info = read_schedule(
                str(_cfg_get(cfg, "日程生成时间", "00:00") or "00:00"), now
            )
        except Exception as e:  # noqa: BLE001 - 日程读不到就当作没有
            _debug("日程读取失败，本次不注入生活状态", e)
            return ""
    # 日程缺失不该连累情绪/心事/黑话：这里只当作「没有日程」，不提前返回
    if not isinstance(info, dict):
        info = {}

    outfit = str(info.get("outfit") or "").strip()
    activity = str(info.get("current_activity") or "").strip()
    schedule = str(info.get("schedule") or "").strip()

    body: list[str] = []
    if activity:
        body.append(f"- 此刻在做：{activity}")
    if outfit:
        body.append(f"- 今日穿搭：{outfit}")
    if schedule:
        limit = _limit_of(cfg)
        text = schedule if len(schedule) <= limit else schedule[:limit].rstrip() + "…"
        body.append(f"- 今日日程：\n{text}")

    # ---- 跨轮情绪（独立开关，默认开；不改动 MoodStore 的消费状态）
    if mood is not None and bool(_cfg_get(cfg, "拟人_情绪注入被动", True)):
        try:
            hint = mood.hint(session_key) if session_key else ""
            if hint:
                body.append(f"- 此刻心情：{str(hint).strip()}")
        except Exception as e:  # noqa: BLE001
            _debug("情绪注入失败，本次跳过", e)

    # ---- 心事池摘要（可选 1 条，只作自然提及提示，不标记已消费）
    if motive and bool(_cfg_get(cfg, "心事_被动注入", True)):
        text = str(motive).strip()
        if text:
            body.append(f"- 可以自然提起的心事：{text[:80]}")

    # ---- 跨会话「人」档案（1~2 行，群聊只给公开字段）
    if person:
        body.append(person)

    # 没有任何实际内容才放弃注入（标题行本身不算内容）
    if not body:
        return ""

    lines = [
        f"{MARKER}（这是你自己的真实状态，聊天时自然带出即可，不要逐条复述）",
        f"- 现在：{now.strftime('%H:%M')}（{str(info.get('time_desc') or '').strip() or _time_desc(now)}）",
        *body,
    ]

    result = "\n".join(lines)
    # 总字数硬上限（含所有块），防止多条注入叠加膨胀
    try:
        cap = max(_MIN_LIMIT, min(_MAX_LIMIT, int(_cfg_get(cfg, "生活状态_总字数上限", 900) or 900)))
    except (TypeError, ValueError):
        cap = 900
    if len(result) > cap:
        result = result[:cap].rstrip() + "…"
    return result


def _time_desc(now: datetime.datetime) -> str:
    """时段描述（与 common.ScheduleReader._time_desc 保持一致）。"""
    h = now.hour % 24
    return (
        "深夜" if h < 6 else "清晨" if h < 9 else "上午" if h < 12 else
        "中午" if h < 14 else "下午" if h < 18 else "晚上" if h < 22 else "深夜"
    )


def _part_text(part: Any) -> str:
    """取一个内容部件的文本；非文本部件返回空串。"""
    if isinstance(part, dict):
        if str(part.get("type") or "") != "text":
            return ""
        return str(part.get("text") or "")
    text = getattr(part, "text", None)
    return str(text) if isinstance(text, str) else ""


def _make_text_part(text: str) -> Any:
    """造一个「只给模型看、不落盘」的文本部件；宿主不支持时返回 None。"""
    try:
        from astrbot.core.agent.message import TextPart

        return TextPart(text=text).mark_as_temp()
    except Exception as e:  # noqa: BLE001 - 旧版 AstrBot 没有 TextPart
        _debug("TextPart 不可用，改用 dict 部件", e)
        return None


def inject_temp_part(req: Any, text: str, marker: str) -> bool:
    """通用临时注入：带指定 marker 幂等，mark_as_temp 不落盘。"""
    if not text or req is None:
        return False
    try:
        parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(parts, list):
            for part in parts:
                try:
                    t = ""
                    if isinstance(part, dict):
                        t = str(part.get("text") or "")
                    else:
                        t = str(getattr(part, "text", "") or "")
                    if marker in t:
                        return False
                except Exception:  # noqa: BLE001
                    continue
            part = _make_text_part(text)
            if part is None:
                part = {"type": "text", "text": text, "_no_save": True}
            parts.append(part)
            return True
        system_prompt = getattr(req, "system_prompt", None)
        if not isinstance(system_prompt, str):
            return False
        if marker in system_prompt:
            return False
        req.system_prompt = f"{system_prompt}\n\n{text}" if system_prompt else text
        return True
    except Exception as e:  # noqa: BLE001
        _debug("临时块注入失败，本次按未注入处理", e)
        return False


def inject_life_state(req: Any, block: str) -> bool:
    """把生活状态挂到本次请求上（统一走 inject_temp_part，避免两段重复代码）。

    幂等：本次请求已含 :data:`MARKER` 时直接返回 False，不重复追加。
    """
    return inject_temp_part(req, block, MARKER)
