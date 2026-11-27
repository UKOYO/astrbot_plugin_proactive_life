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

# 「按需注入」用的触发词分层：
# - 状态/时间层：对方在问此刻在做什么、今天怎么安排
# - 外观/穿搭层：对方在问长相、衣服、打扮
# 关键词只做粗筛，命中才多给几行；没命中就不给，避免低智模型把日程当稿子念。
_STATUS_WORDS = (
    "干嘛", "在干", "干什么", "做什么", "在忙", "忙吗", "忙不忙", "忙啥", "忙什么",
    "日程", "安排", "计划", "今天", "现在", "几点", "在哪", "在哪呢", "出门",
    "睡了", "睡没", "醒了吗", "吃饭", "吃啥", "吃什么", "午饭", "晚饭", "夜宵",
    "上班", "下班", "加班", "上课", "放假", "周末", "休息",
)
_LOOK_WORDS = (
    "穿什么", "穿啥", "穿的", "穿搭", "衣服", "服装", "衣着", "套装",
    "裙子", "裤子", "短裤", "牛仔裤", "外套", "鞋", "袜", "内衣",
    "打扮", "造型", "ootd", "头发", "发色", "瞳色", "眼睛",
    "长什么", "长相", "样子", "自拍", "全身照", "照片", "好看吗",
)

# 极短追问/索看句（如「看看」「发来」）：本身不含服装词，
# 仅在最近 1~2 轮对话刚提过穿搭/日程时延续按需注入。
_FOLLOWUP_SHORT_WORDS = (
    "看看", "看下", "看一下", "发来", "发我", "给我看", "重发", "再发",
)
_FOLLOWUP_MAX_LEN = 10

# 低信息量短句：这类消息没有明确话题，才适合把「可提起的心事」递给模型。
# 有明确主题时塞心事，模型会把话题硬拐到心事上。
_LOW_CONTENT_WORDS = (
    "在吗", "在么", "在嘛", "在不在", "在", "喂", "干嘛", "干嘛呢", "干什么",
    "忙吗", "忙不忙", "无聊", "好无聊", "没事", "没事干", "好闲", "闲",
    "早", "早安", "早上好", "午安", "中午好", "晚上好", "晚安", "睡了", "睡了没",
    "醒了", "哈喽", "hello", "hi", "嗨", "在干啥", "干啥呢", "干啥",
)
_LOW_CONTENT_MAX_LEN = 12


def _hit_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def is_low_content_message(text: str) -> bool:
    """消息是不是「没有明确主题」的短招呼/催场（决定要不要递心事提示）。"""
    t = str(text or "").strip().lower()
    if not t or len(t) > _LOW_CONTENT_MAX_LEN:
        return False
    return _hit_any(t, _LOW_CONTENT_WORDS)


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
    user_text: str | None = None,
    slang: str = "",
    context_hint: str = "",
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
        person: 跨会话人物档案文本。
        user_text: 对方这条消息的正文，用于「按需注入」的关键词门控。
            None（默认）表示调用方没给正文，维持全量注入；空串表示这条消息
            确实没有文字（纯图片等），按「没问到」处理。
        slang: 群黑话提示（``SlangMiner.hint`` 的返回值）。独立入参：
            以前拼进 ``motive`` 里，会被 ``心事_被动注入`` 开关连坐、还被
            心事的 80 字截断砍掉尾巴。
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

    # 按需分层：常态只给时间锚点 + 心情 + 人物档案，
    # 对方问到「在干嘛/今天」才给此刻在做与日程，问到「穿什么」才给外观与穿搭。
    # 目的：日程块紧贴用户消息、近因效应极强，低智模型看到就会硬背。
    want_state = True
    want_look = True
    on_demand_active = user_text is not None and bool(
        _cfg_get(cfg, "日程_被动按需注入", True)
    )
    if on_demand_active:
        probe = str(user_text or "").strip().lower()
        want_state = _hit_any(probe, _STATUS_WORDS)
        want_look = _hit_any(probe, _LOOK_WORDS)
        if (
            not (want_state or want_look)
            and len(probe) <= _FOLLOWUP_MAX_LEN
            and _hit_any(probe, _FOLLOWUP_SHORT_WORDS)
            and context_hint
        ):
            hint_probe = str(context_hint).strip().lower()
            want_state = _hit_any(hint_probe, _STATUS_WORDS)
            want_look = _hit_any(hint_probe, _LOOK_WORDS)

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
    appearance = str(_cfg_get(cfg, "日程_外观锚点", "") or "").strip()
    activity = str(info.get("current_activity") or "").strip()
    schedule = str(info.get("schedule") or "").strip()

    body: list[str] = []
    if want_state and activity:
        body.append(f"- 此刻在做：{activity}")
    # 固定外观是稳定信息，跟当天穿搭分开给：换衣服不改长相
    if want_look and appearance:
        body.append(f"- 固定外观（不会随穿搭变化）：{appearance}")
    if want_look and outfit:
        body.append(f"- 今日穿搭：{outfit}")
    if want_state and schedule:
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

    # ---- 群黑话（独立开关：不受「心事_被动注入」连坐，也不被心事 80 字截断）
    if slang:
        text = str(slang).strip()
        if text:
            body.append(f"- {text}")

    # ---- 跨会话「人」档案（1~2 行，群聊只给公开字段）
    if person:
        body.append(person)

    # 没有任何实际内容才放弃注入（标题行本身不算内容）。
    # 例外：按需注入模式下确实拿到了用户正文——这时至少保留时间锚点，
    # 让对方问「现在几点/在干嘛」时模型心里有个时间感。
    if not body and not on_demand_active:
        return ""

    lines = [
        f"{MARKER}（背景备查：这是你自己此刻的真实状态，对方没问到就绝口不提；"
        "严禁主动报备、严禁生硬转折、严禁把日程当稿子念；"
        "对方聊的是别的事，就当这份资料不存在）",
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


def inject_temp_images(
    req: Any,
    image_urls: list[str],
    *,
    max_images: int = 2,
    modalities: Any = None,
) -> int:
    """把「机器人自己刚发出去的图」作为临时图片部件挂到本次请求上。

    为什么必须走 ``extra_user_content_parts``：
      - 塞进 ``system_prompt`` 会让 base64 撑爆 prompt 缓存，还会写进历史；
      - 塞进 ``image_urls`` 会被当成「用户发的图」，模型分不清是谁发的。

    安全阀：``modalities`` 传了非空列表且不含 ``image`` 时直接跳过——宿主的
    ``sanitize_contexts_by_modalities`` 只清洗 ``contexts``，不清洗
    ``extra_user_content_parts``，纯文本模型会原样收到图片块而报错。

    Returns:
        实际追加的图片部件数量。
    """
    urls: list[str] = []
    seen: set[str] = set()
    for u in list(image_urls or []):
        s = str(u or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        urls.append(s)
    if not urls:
        return 0
    if isinstance(modalities, list) and modalities and "image" not in modalities:
        _debug("当前模型不支持图片模态，跳过临时图片注入")
        return 0
    try:
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            return 0
        existing: set[str] = set()
        for part in parts:
            url = getattr(getattr(part, "image_url", None), "url", None)
            if isinstance(url, str) and url:
                existing.add(url)
        try:
            from astrbot.core.agent.message import ImageURLPart
        except Exception as e:  # noqa: BLE001 - 旧版 AstrBot 没有该部件
            _debug("ImageURLPart 不可用，本次不注入图片", e)
            return 0
        cap = max(1, int(max_images or 2))
        added = 0
        for u in urls:
            if added >= cap:
                break
            if u in existing:
                continue
            try:
                part = ImageURLPart(
                    image_url=ImageURLPart.ImageURL(url=u)
                ).mark_as_temp()
            except Exception as e:  # noqa: BLE001
                _debug("图片部件构造失败，跳过这一张", e)
                continue
            parts.append(part)
            existing.add(u)
            added += 1
        return added
    except Exception as e:  # noqa: BLE001
        _debug("临时图片注入失败，本次按未注入处理", e)
        return 0


# ---- 临时注入的总预算（所有临时块合计，字符数）----
# 临时块紧贴用户消息，越长越稀释注意力，也越白烧 Token。
# full 档 600 字、lite 档 300 字；超预算按优先级裁剪。
_BUDGET_FULL = 600
_BUDGET_LITE = 300

# 「刚才那张图」相关词：命中才值得把机器人自己发的图作为视觉部件再喂一遍
_IMAGE_WORDS = ("图", "表情", "啥", "这", "哈哈", "草", "发")
_IMAGE_MAX_LEN = 10


# 小模型特征：auto 档命中这些就用 lite（拍平场景提示 + 更小的注入预算）
_LITE_MODEL_MARKERS = (
    "lite", "mini", "flash", "nano", "small", "tiny",
    "7b", "8b", "9b", "13b", "haiku",
)


def resolve_model_tier(cfg: Any, model_name: str = "") -> str:
    """解析「模型档位」：显式 full/lite 优先，auto 时按模型名猜。

    auto + flash/mini/lite/haiku/7b… → lite；其余 → full。
    模型名拿不到时按 full（保守：不擅自减配）。
    """
    try:
        tier = str(cfg.get("拟人_模型档位", "auto") or "auto").strip().lower()
    except Exception:  # noqa: BLE001
        tier = "auto"
    if tier in ("full", "lite"):
        return tier
    name = str(model_name or "").strip().lower()
    if name and any(m in name for m in _LITE_MODEL_MARKERS):
        return "lite"
    return "full"


def inject_budget(cfg: Any, model_name: str = "") -> int:
    """当前档位的临时注入总预算（字符）。"""
    return _BUDGET_LITE if resolve_model_tier(cfg, model_name) == "lite" else _BUDGET_FULL


def image_worth_injecting(user_text: str) -> bool:
    """对方这条消息是不是在说「刚才那张图」。

    不是的话只保留一句文字摘要（``[表情包]``），不再把图作为视觉部件塞进去：
    每次 @ 都白付一份视觉 Token，而模型根本用不上。
    """
    t = str(user_text or "").strip()
    if not t:
        return False
    if len(t) <= _IMAGE_MAX_LEN:
        return True
    return any(w in t for w in _IMAGE_WORDS)


def _cut_text(text: str, keep: int) -> str:
    """把文本裁到 keep 字以内，尽量在行边界断开（避免半句）。

    末尾的省略号也算进 keep，否则预算会超出 1 个字。
    """
    if keep >= len(text):
        return text
    head = text[: max(0, keep - 1)]
    cut = head.rfind("\n")
    if cut >= 80:
        head = head[:cut]
    return head.rstrip() + "…"


def _fit_budget(parts: list[dict], budget: int) -> list[dict]:
    """按优先级裁剪临时块，保证合计字数不超预算。

    - **保底块**（身份锚定、生活状态）：默认不动，只有预算被压到极低时才裁；
    - **弹性块**：说话方式(2) 先裁尾巴但保留核心（``min_keep``），
      群近期动态(3) 其次整块让路。

    为什么把说话规范留在群动态前面：它决定"像不像人"，每轮都在起作用；
    群动态只对当下这一条消息有用，少一条不会让模型整体跑偏。
    """
    def size(part: dict) -> int:
        return len(str(part.get("text") or ""))

    total = sum(size(p) for p in parts)
    if total <= budget:
        return parts

    def trim(part: dict, keep: int) -> None:
        nonlocal total
        text = str(part.get("text") or "")
        trimmed = _cut_text(text, keep)
        total -= len(text) - len(trimmed)
        part["text"] = trimmed

    def drop(part: dict) -> None:
        nonlocal total
        total -= size(part)
        part["text"] = ""

    # 1) 说话方式：裁尾巴，保留核心
    for part in parts:
        if total <= budget:
            break
        if part.get("elastic") and part.get("priority") == 2 and part.get("text"):
            keep = max(int(part.get("min_keep", 150)), size(part) - (total - budget))
            if keep < size(part):
                trim(part, keep)

    # 2) 群近期动态：整块丢弃
    for part in parts:
        if total <= budget:
            break
        if part.get("elastic") and part.get("priority") == 3 and part.get("text"):
            drop(part)

    # 3) 真到这一步说明档位预算被压得极低：连保底块也要缩，但仍保留 60 字
    for part in parts:
        if total <= budget:
            break
        text = str(part.get("text") or "")
        if not text:
            continue
        keep = max(60, len(text) - (total - budget))
        if keep < len(text):
            trim(part, keep)
    return parts


def _recent_context_text(req: Any, limit: int = 2) -> str:
    """提取 req.contexts 最近几条纯文本，用于极短追问（「看看」「发来」）延续判定。"""
    contexts = getattr(req, "contexts", None)
    if not isinstance(contexts, list) or not contexts:
        return ""
    texts: list[str] = []
    for item in contexts[-max(1, limit):]:
        if isinstance(item, dict):
            content = item.get("content", "")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            elif isinstance(content, list):
                for part in content:
                    t = _part_text(part).strip()
                    if t:
                        texts.append(t)
        else:
            content = getattr(item, "content", None)
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
    return "\n".join(texts)


def inject_passive_context(plugin: Any, event: Any, req: Any) -> None:
    """被动回复链路的一次性注入编排（说话方式 / 近期动态 / 生活状态）。

    顺序固定：说话方式 → 群近期动态 → 生活状态（越靠后越贴近用户消息）。
    所有块先收集、再按全局预算统一裁剪、最后统一注入，避免叠加膨胀。
    任何一步失败都只记 debug：注入失败绝不能影响正常回复。
    """
    try:
        from .common import canonical_session_key, get_chat_provider

        # 当前会话真实模型名（说话方式避坑用）与支持的模态（图片注入安全阀用）
        model_name = ""
        provider_modalities = None
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            prov = get_chat_provider(plugin.context, umo or None)
            if prov is not None:
                model_name = str(prov.get_model() or "")
                _pcfg = getattr(prov, "provider_config", None)
                if hasattr(_pcfg, "get"):
                    provider_modalities = _pcfg.get("modalities", None)
        except Exception:  # noqa: BLE001
            pass

        # 情绪 Key 统一走 common.canonical_session_key：私聊用 uid，群聊用裸群号，
        # 与写入端（handler_base._mood_apply）保持一致。
        session_key = canonical_session_key(event)
        user_text = str(getattr(event, "message_str", "") or "")

        # 群聊主动接话的 prompt 里已经带了一份说话规范（_GUARDRAIL），
        # 这里再挂 speech_style 等于同一请求两份规则，纯浪费 Token。
        try:
            group_triggered = bool(event.get_extra("_group_active_message_triggered"))
        except Exception:  # noqa: BLE001
            group_triggered = False

        parts: list[dict] = []

        # 身份锚定：**只在对方盘问模型/厂商（私聊）或问「你是谁」（群聊）时注入**，
        # 平时零开销；语气交给用户人设，插件只挡"自曝底层模型"。
        try:
            from .modules.identity_guard import MARKER as ID_MARKER, guard_for_text

            try:
                bot_name = plugin.group_h._bot_name()
            except Exception:  # noqa: BLE001
                bot_name = ""
            try:
                is_private = bool(event.is_private_chat())
            except Exception:  # noqa: BLE001
                is_private = False
            guard = guard_for_text(
                plugin.cfg, bot_name, user_text, private=is_private
            )
            if guard:
                parts.append({
                    "priority": 1, "marker": ID_MARKER,
                    "text": guard, "name": "身份锚定",
                })
        except Exception as e:  # noqa: BLE001
            _debug("身份锚定注入失败", e)

        if not group_triggered:
            try:
                from .modules.speech_style import (
                    MARKER as SPEECH_MARKER,
                    speech_style_block,
                )

                style = speech_style_block(plugin.cfg, model_name)
                if style:
                    parts.append({
                        "priority": 2, "marker": SPEECH_MARKER,
                        "text": style, "name": "说话方式",
                        "min_keep": 150, "elastic": True,
                    })
            except Exception as e:  # noqa: BLE001
                _debug("说话方式注入失败", e)

        # 心事：只在对方发来「没有明确主题」的短招呼时递
        motive_hint = ""
        if bool(_cfg_get(plugin.cfg, "心事_被动注入", True)):
            try:
                if is_low_content_message(user_text):
                    motive_hint = plugin.motive_mgr.peek_passive_hint()
            except Exception:  # noqa: BLE001
                pass

        # 群黑话：独立入参（不再拼进 motive，避免被心事开关连坐 + 80 字截断）
        _sync_cfg = getattr(plugin, "_sync_runtime_state", None)
        if callable(_sync_cfg):
            _sync_cfg()
        slang_hint = ""
        try:
            _on_module = getattr(plugin, "_module_on", None)
            _black_on = callable(_on_module) and _on_module("黑话")
            _gid = str(getattr(event, "get_group_id", lambda: "")() or "")
            if _black_on and _gid:
                slang = getattr(plugin, "_slang", None)
                if slang is None:
                    from .modules.slang import SlangMiner

                    slang = SlangMiner(int(_cfg_get(plugin.cfg, "黑话_最小命中", 3) or 3))
                    plugin._slang = slang
                slang_hint = slang.hint(_gid, plugin.cfg)
        except Exception:  # noqa: BLE001
            pass

        # 跨会话人物档案：群聊只带公开字段，私聊才带温度/欠着的事
        person_block = ""
        try:
            is_pm_private = bool(event.is_private_chat())
            _getter = getattr(plugin, "_get_person_memory", None)
            _pm = _getter() if callable(_getter) else getattr(plugin, "person_memory", None)
            _uid = str(event.get_sender_id() or "")
            # 私聊用配置里的「对方称呼」标识对方（+UID 兜底），
            # 否则 /new 清空记忆后模型会把恋人当成陌生网友。
            _display = ""
            if is_pm_private:
                try:
                    _display = str(plugin.cfg.get("角色_对方称呼", "") or "").strip()
                except Exception:  # noqa: BLE001
                    _display = ""
            if _pm is not None:
                person_block = _pm.block(
                    _uid,
                    scope="private" if is_pm_private else "group",
                    name=str(event.get_sender_name() or ""),
                    current_text=user_text,
                    display_name=_display,
                )
            elif is_pm_private and _display:
                # 关掉「跨会话记忆」（如 lowload 预设）时档案整块不取，
                # 但私聊称呼是配置项、与记忆无关，仍要带上，否则又退回裸 QQ 昵称。
                person_block = f"- 当前私聊对象：{_display}（UID:{_uid}）"
        except Exception as e:  # noqa: BLE001
            _debug("人物档案注入失败", e)

        # 群聊带字 @ 兜底（**必须排在【当前生活状态】之前**）：
        # 刚在群里发生的事比「今天穿什么/现在做什么」和几小时前的旧历史都更新，
        # 越靠近用户当前消息，模型越会拿它当接话依据。这些消息不进
        # conversation.history（表情包/插件管线直发），只能靠这里临时补给。
        # （戳一戳 / 纯艾特已通过 build_prompt 带过上下文，靠 _poke_trigger 跳过）
        try:
            is_group = not bool(event.is_private_chat())
            is_poke = bool(event.get_extra("_poke_trigger"))
            gid_echo = str(getattr(event, "get_group_id", lambda: "")() or "")
            ctx = getattr(plugin, "interact_ctx", None)
            if (
                is_group
                and not is_poke
                and gid_echo
                and ctx is not None
                and plugin._module_on("戳一戳")
                and bool(_cfg_get(plugin.cfg, "戳一戳_收录自己发言", True))
            ):
                from .modules.interact_context import (
                    RECENT_ECHO_MARKER,
                    build_recent_echo_block,
                    collect_recent_self_images,
                )

                items = ctx.recent(gid_echo)
                cur_prompt = str(
                    getattr(req, "prompt", "")
                    or getattr(event, "message_str", "")
                    or ""
                )
                try:
                    bname = plugin.group_h._bot_name()
                except Exception:  # noqa: BLE001
                    bname = ""
                # 视觉门控：只有对方像是在说刚才那张图时，才把图作为视觉部件再喂一遍；
                # 纯文本模型直接跳过（避免文案里写了「附带的图片」却没真附上去）。
                supports_image = not (
                    isinstance(provider_modalities, list)
                    and provider_modalities
                    and "image" not in provider_modalities
                )
                want_image = supports_image and image_worth_injecting(user_text)
                self_images = collect_recent_self_images(items) if want_image else []
                echo_block = build_recent_echo_block(
                    items,
                    bot_name=bname,
                    current_prompt=cur_prompt,
                    has_attached_images=bool(self_images),
                )
                if echo_block:
                    parts.append({
                        "priority": 3, "marker": RECENT_ECHO_MARKER,
                        "text": echo_block, "name": "群近期动态",
                        "images": self_images, "elastic": True,
                    })
        except Exception as e:  # noqa: BLE001
            _debug("群近期动态注入失败，本次跳过", e)

        context_hint = _recent_context_text(req, limit=2)
        block = build_life_state_block(
            plugin.cfg,
            mood=getattr(plugin, "mood", None),
            motive=motive_hint,
            session_key=session_key,
            person=person_block,
            user_text=user_text,
            slang=slang_hint,
            context_hint=context_hint,
        )
        if block:
            parts.append({
                "priority": 1, "marker": MARKER,
                "text": block, "name": "生活状态",
            })

        # 统一预算裁剪后再注入：常态按模型档位省流（300/600），
        # 但用户主动按需召回穿搭/日程/此刻在做时，放宽到「生活状态_总字数上限」+核心说话规范，
        # 避免刚取出来的穿搭细节又被尾部截断。
        budget = inject_budget(plugin.cfg, model_name)
        if block and (
            "- 今日穿搭：" in block
            or "- 今日日程：" in block
            or "- 此刻在做：" in block
        ):
            try:
                life_cap = max(
                    _MIN_LIMIT,
                    min(_MAX_LIMIT, int(_cfg_get(plugin.cfg, "生活状态_总字数上限", 900) or 900)),
                )
            except (TypeError, ValueError):
                life_cap = 900
            budget = max(budget, min(_MAX_LIMIT, life_cap + 250))

        fitted = [p for p in _fit_budget(parts, budget) if str(p.get("text") or "")]
        for idx, part in enumerate(fitted):
            text = str(part.get("text") or "")
            if idx + 1 < len(fitted) and not text.endswith("\n"):
                text = text + "\n"
            if not inject_temp_part(req, text, part["marker"]):
                continue
            _debug(f"已注入{part['name']}（{len(text)} 字）")
            images = part.get("images") or []
            if images:
                injected = inject_temp_images(
                    req, images, modalities=provider_modalities
                )
                if injected:
                    _debug(f"已注入自己刚发的 {injected} 张图")
    except Exception as e:  # noqa: BLE001 - 注入失败不能影响正常回复
        _debug("生活状态注入异常，本次跳过", e)


def inject_life_state(req: Any, block: str) -> bool:
    """把生活状态挂到本次请求上（统一走 inject_temp_part，避免两段重复代码）。

    幂等：本次请求已含 :data:`MARKER` 时直接返回 False，不重复追加。
    """
    return inject_temp_part(req, block, MARKER)
