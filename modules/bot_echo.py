"""机器人自己发言的采集：给戳一戳/艾特上下文补上「我刚说了什么」。

背景（为什么要有这个模块）：
  戳/艾特会带一段最近上下文给模型，但早先只收群友发的消息。只要机器人刚
  走别的路子说过话——表情包制作、入群欢迎、退群说明、主动插话、后台摸鱼
  的输出——被戳或被 @ 时模型对自己刚干过的事一无所知，接话自然没头没尾。

捕获点（按覆盖面排序）：
  1. `BotSendTap`：优先挂在协议端 `bot.api.call_action`（即 aiocqhttp 底层
     `_api.call_action`），不存在时降级挂在 `bot.call_action`。
     机器人出声的路径再多，最后都要落到 ``send_group_msg`` /
     ``send_group_forward_msg`` / ``send_msg``，在这一层挂一次就能全拿到：
     LLM 回复、插件 yield / `set_result` 走管线的结果、
     `context.send_message` 主动发、以及插件绕过 AstrBot 直接调协议端
     `bot.send_group_msg` / `bot.call_action` / `bot.api.call_action` 发的
     （例如入群欢迎、退群说明、抽老婆等）。
  2. ``after_message_sent`` 钩子（见 main.py）：非 aiocqhttp 平台与非群
     场景的兜底。
  3. 上面两条会对同一条消息各记一次，去重统一由 `BotEchoLog.record` 负责。

本模块只做三件事：把消息链打成可读摘要、去重、限长。不碰 LLM、不注册钩子。

补充（v1.5.10）：纯媒体占位可以细化为带语义的标签。``after_message_sent``
能拿到「本次命中的插件处理器」，于是表情包插件（memelite 等）发出来的那张
GIF 不再只是一句 ``[表情包]``，而是 ``[发送了表情包: 摸头 (动图/GIF)]``——
模型在历史动态里才知道「上一条是我自己调表情包插件生成的」，被追问时不瞎猜。
"""

from __future__ import annotations

import base64
import inspect
import io
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

#: 这些后缀多半是动图/表情包，单独标一下，模型才知道自己发的是表情而不是照片
_STICKER_SUFFIXES = (".gif", ".webp", ".apng")

#: base64 图片魔数前缀（GIF87a/GIF89a = R0lGOD，WebP RIFF = UklGR）
_STICKER_B64_PREFIXES = ("base64://r0lgod", "base64://uklgr")

#: 组件类型 → 可读摘要。模型拿不到媒体本体，至少要让它知道「我发过什么」
_COMPONENT_LABELS = {
    "Face": "[表情]",
    "Record": "[语音]",
    "Video": "[视频]",
    "File": "[文件]",
    "Node": "[合并转发]",
    "Nodes": "[合并转发]",
    "Forward": "[合并转发]",
    "Json": "[卡片]",
    "Contact": "[名片]",
    "Music": "[音乐]",
    "Poke": "[戳一戳]",
    "Dice": "[骰子]",
    "RPS": "[猜拳]",
}

#: OneBot v11 segment 类型 → 可读摘要（协议端出站时的字段名）
_SEGMENT_LABELS = {
    "face": "[表情]",
    "mface": "[表情包]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "node": "[合并转发]",
    "nodes": "[合并转发]",
    "forward": "[合并转发]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "contact": "[名片]",
    "music": "[音乐]",
    "poke": "[戳一戳]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
    "markdown": "[卡片]",
}

#: 纯媒体占位标签集合，用于判定一条摘要是否不含实质文本
_MEDIA_ONLY_LABELS = frozenset(
    {"[图片]", "[表情包]", *_COMPONENT_LABELS.values(), *_SEGMENT_LABELS.values()}
)

#: 语义化表情包标签前缀：带具体关键词时用它，模型才知道「上一条是我调插件生成的」
STICKER_SEMANTIC_PREFIX = "[发送了表情包"

#: 表情包插件的来源线索（插件目录名/模块路径/资源路径里出现即认）
_MEME_SOURCE_HINTS = ("memelite", "meme", "表情包", "表情制作", "sticker")

#: 事件附加信息里可能放表情包关键词的键名线索
_MEME_KEY_HINTS = ("meme", "sticker", "表情")

#: 文本里显式的 meme 触发写法：``meme 摸头`` / ``表情包 摸头`` / ``#meme:摸头``
_MEME_TEXT_RE = re.compile(
    r"(?:^|[\s#])(?:meme|memes|表情包|表情)\s*[:：]?\s*([^\s，。！？、,!?]{1,16})",
    re.IGNORECASE,
)

#: 语义标签里「具体到关键词」的档位（见 media_rank）
_SEMANTIC_RANK = 4

#: 把带括号补充/语义化的媒体标签折回基础占位，便于「是否纯媒体」判定
_MEDIA_LABEL_RE = re.compile(r"\[(发送了表情包|表情包|图片)[^\[\]]*\]")

#: CQ 码解析（兼容部分插件直接用字符串调用 send_group_msg）
_CQ_CODE_RE = re.compile(r"\[CQ:([a-zA-Z0-9_]+)(?:,([^\]]*))?\]")


def _image_label(file: str = "", url: str = "", summary: str = "") -> str:
    """图片 → ``[图片]`` / ``[表情包]``。"""
    raw_file = str(file or "").strip()
    lower_file = raw_file.lower()
    if any(lower_file.startswith(prefix) for prefix in _STICKER_B64_PREFIXES):
        return "[表情包]"
    tail = str(raw_file or url or "").split("?")[0].strip().lower()
    if any(tail.endswith(suffix) for suffix in _STICKER_SUFFIXES):
        return "[表情包]"
    if "表情" in str(summary or ""):
        return "[表情包]"
    return "[图片]"


def _is_animated_source(file: str = "", url: str = "") -> bool:
    """这张图是不是动图（GIF/WebP/APNG），含 base64 / data URL 两种内联形态。"""
    for src in (file, url):
        low = str(src or "").strip().lower()
        if not low:
            continue
        if any(low.startswith(prefix) for prefix in _STICKER_B64_PREFIXES):
            return True
        if low.startswith(("data:image/gif", "data:image/webp", "data:image/apng")):
            return True
        head = low[:64]
        # 裸 base64 的 GIF87a/GIF89a（R0lGOD）/ WebP（UklGR）魔数
        if "r0lgod" in head or "uklgr" in head:
            return True
        tail = low[:400].split("?")[0]
        if any(tail.endswith(suffix) for suffix in _STICKER_SUFFIXES):
            return True
    return False


def sticker_label(keyword: str = "", *, animated: bool = False) -> str:
    """拼语义化表情包标签。

    - 有关键词 + 动图 → ``[发送了表情包: 摸头 (动图/GIF)]``
    - 只有关键词       → ``[发送了表情包: 摸头]``
    - 只有动图         → ``[表情包 (动图/GIF)]``
    - 都没有           → ``[表情包]``
    """
    kw = " ".join(str(keyword or "").split())[:16].strip(" :：,，")
    if kw:
        if animated:
            return f"{STICKER_SEMANTIC_PREFIX}: {kw} (动图/GIF)]"
        return f"{STICKER_SEMANTIC_PREFIX}: {kw}]"
    return "[表情包 (动图/GIF)]" if animated else "[表情包]"


def is_sticker_summary(text: str) -> bool:
    """这条摘要是不是（或带语义的）表情包占位。"""
    body = str(text or "").strip()
    if not body:
        return False
    return body.startswith("[表情包") or body.startswith(STICKER_SEMANTIC_PREFIX)


def media_rank(text: str) -> int:
    """纯媒体标签的具体程度（0 = 不是图片/表情包占位）。

    ``[发送了表情包: 摸头 (动图/GIF)]``(4) > ``[表情包 (动图/GIF)]``(3)
    > ``[表情包]``(2) > ``[图片]``(1)：双路径上报同一条消息时取更具体的那个。
    """
    body = str(text or "").strip()
    if not body:
        return 0
    if body.startswith(STICKER_SEMANTIC_PREFIX):
        return _SEMANTIC_RANK
    if body.startswith("[表情包"):
        return 3 if body.startswith(("[表情包 ", "[表情包(")) else 2
    if body.startswith("[图片"):
        return 1
    return 0


#: 旧名保留（模块内部与老调用方都在用）
_media_rank = media_rank


def _join(parts: list[str], max_chars: int) -> str:
    """拼成一行并压掉多余空白；超长截断（上下文不该被一条长消息撑爆）。"""
    text = " ".join(str(p) for p in parts if str(p or "").strip())
    text = " ".join(text.split())
    limit = max(20, int(max_chars))
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def is_media_only_summary(summary: str) -> bool:
    """判断摘要是否全由媒体占位标签组成（不含实质正文）。

    兼容语义化标签：``[发送了表情包: 摸头 (动图/GIF)]``、``[表情包 (动图/GIF)]``
    都算纯媒体，先把它们折回基础占位再按 token 判定。
    """

    def _canon(match: re.Match[str]) -> str:
        kind = str(match.group(1) or "")
        return " [图片] " if kind == "图片" else " [表情包] "

    text = _MEDIA_LABEL_RE.sub(_canon, str(summary or ""))
    tokens = text.split()
    return bool(tokens) and all(tok in _MEDIA_ONLY_LABELS for tok in tokens)


def _event_extras(event: Any) -> dict:
    """取事件附加信息（``get_extra()`` 无参返回整份 dict，老版本退回 ``_extras``）。"""
    if event is None:
        return {}
    try:
        getter = getattr(event, "get_extra", None)
        extras = getter() if callable(getter) else None
    except Exception:  # noqa: BLE001
        extras = None
    if isinstance(extras, dict):
        return extras
    raw = getattr(event, "_extras", None)
    return raw if isinstance(raw, dict) else {}


def _short_keyword(raw: str, *, limit: int = 16) -> str:
    """把一段文本收成「表情包关键词」；像整句/太长/空 → 空串。"""
    text = _CQ_CODE_RE.sub(" ", str(raw or ""))
    text = " ".join(text.replace("@", " ").split()).strip(" :：,，。.!！?？~")
    if not text or len(text) > limit:
        return ""
    if any(ch in text for ch in "。！？!?，,;；\n"):
        return ""
    return text


def detect_meme_source(event: Any = None, chain: Any = None) -> str:
    """这条出站纯图是不是表情包插件生成的；命中返回线索词，否则空串。

    查三处：本次事件命中的插件处理器（AstrBot 的 ``activated_handlers``，
    里面带插件模块路径/函数名）、事件附加信息、以及出站图片的本地路径
    （memelite 之类会把资源放在 ``memes/`` 下）。
    """
    haystack: list[str] = []
    extras = _event_extras(event)
    for handler in list(extras.get("activated_handlers") or []):
        for attr in ("handler_module_path", "handler_full_name", "handler_name"):
            val = str(getattr(handler, attr, "") or "")
            if val:
                haystack.append(val)
    for key, val in extras.items():
        haystack.append(str(key or ""))
        if isinstance(val, str) and val:
            haystack.append(val[:80])
    for comp in list(chain or []):
        if type(comp).__name__ != "Image":
            continue
        for attr in ("file", "path", "url"):
            low = str(getattr(comp, attr, "") or "").strip().lower()
            # 内联 base64 是噪音（又长又没语义），不参与匹配
            if not low or low.startswith(("base64://", "data:")):
                continue
            haystack.append(low[:200])
    blob = " ".join(haystack).lower()
    for hint in _MEME_SOURCE_HINTS:
        if hint in blob:
            return hint
    return ""


def detect_meme_keyword(event: Any = None, chain: Any = None) -> str:
    """嗅出这次表情包用的关键词（「摸头」「rua」…），拿不到返回空串。

    优先级：插件显式写在附加信息里的关键词 > 触发文本里显式的 meme 写法 >
    确认来源是表情包插件时，把触发它的那句短消息当关键词。
    """
    extras = _event_extras(event)
    for key, val in extras.items():
        if not isinstance(val, str) or not val.strip():
            continue
        if any(hint in str(key or "").lower() for hint in _MEME_KEY_HINTS):
            kw = _short_keyword(val)
            if kw:
                return kw
    text = str(getattr(event, "message_str", "") or "")
    matched = _MEME_TEXT_RE.search(text)
    if matched:
        kw = _short_keyword(matched.group(1))
        if kw:
            return kw
    if detect_meme_source(event, chain):
        return _short_keyword(text)
    return ""


def refine_self_media_summary(summary: str, *, event: Any = None, chain: Any = None) -> str:
    """把纯媒体占位细化为带语义的表情包标签；非纯媒体摘要原样返回。

    典型场景：memelite 之类的表情包插件走管线 yield 一张 GIF，``after_message_sent``
    只看到 ``[表情包]``。这里结合事件来源与关键词，细化成
    ``[发送了表情包: 摸头 (动图/GIF)]``，模型在历史动态里才知道「上一条是我
    调表情包插件生成的」，被追问「这啥」时不至于瞎猜。
    """
    body = str(summary or "").strip()
    if not body or not is_media_only_summary(body) or not is_sticker_summary(body):
        return body
    if len(body.split()) != 1:  # 多条媒体混排时不改，免得语义打架
        return body
    animated = any(
        _is_animated_source(
            str(getattr(comp, "file", "") or getattr(comp, "path", "") or ""),
            str(getattr(comp, "url", "") or ""),
        )
        for comp in list(chain or [])
        if type(comp).__name__ == "Image"
    )
    keyword = detect_meme_keyword(event, chain)
    if not keyword and not animated:
        return body
    return sticker_label(keyword, animated=animated)


def _parse_cq_params(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    if not raw:
        return params
    for piece in raw.split(","):
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        params[k.strip()] = v.strip()
    return params


def _summarize_cq_string(raw: str, *, max_chars: int = 160) -> str:
    """把含或不含 CQ 码的纯字符串转成可读摘要。"""
    if not raw:
        return ""

    def _replace(match: re.Match[str]) -> str:
        ctype = str(match.group(1) or "").lower()
        params = _parse_cq_params(str(match.group(2) or ""))
        if ctype == "image":
            return " " + _image_label(
                params.get("file", ""),
                params.get("url", ""),
                params.get("summary", ""),
            ) + " "
        if ctype == "at":
            target = params.get("name") or params.get("qq") or ""
            return f" @{target} " if target else " @某人 "
        if ctype == "reply":
            return " "
        label = _SEGMENT_LABELS.get(ctype)
        return f" {label} " if label else " "

    replaced = _CQ_CODE_RE.sub(_replace, raw)
    return _join([replaced], max_chars)


def summarize_components(chain: Any, *, max_chars: int = 160) -> str:
    """把 AstrBot 消息组件链打成一行可读摘要（文本 + 媒体占位）。

    用于 ``after_message_sent`` 拿到的 ``MessageEventResult.chain``。
    """
    parts: list[str] = []
    for comp in list(chain or []):
        ctype = type(comp).__name__
        if ctype == "Plain":
            text = str(getattr(comp, "text", "") or "")
            if text.strip():
                parts.append(text.strip())
        elif ctype == "Image":
            parts.append(
                _image_label(
                    str(getattr(comp, "file", "") or getattr(comp, "path", "") or ""),
                    str(getattr(comp, "url", "") or ""),
                )
            )
        elif ctype == "At":
            name = str(getattr(comp, "name", "") or getattr(comp, "qq", "") or "")
            parts.append(f"@{name}" if name else "@某人")
        elif ctype == "Reply":
            continue  # 引用只是元信息，不进摘要
        else:
            label = _COMPONENT_LABELS.get(ctype)
            if label:
                parts.append(label)
    return _join(parts, max_chars)


def summarize_onebot_segments(segments: Any, *, max_chars: int = 160) -> str:
    """把 OneBot v11 出站消息打成一行可读摘要。

    兼容 ``list[dict]``、单个 ``dict``、以及纯字符串（含 CQ 码）。
    用于协议端 tap：这里拿到的是最接近「实际发出去的东西」。
    """
    if segments is None:
        return ""
    if isinstance(segments, str):
        return _summarize_cq_string(segments, max_chars=max_chars)
    if isinstance(segments, dict):
        seg_list = [segments]
    else:
        try:
            seg_list = list(segments)
        except TypeError:
            return _summarize_cq_string(str(segments), max_chars=max_chars)

    parts: list[str] = []
    for seg in seg_list:
        if isinstance(seg, str):
            sub = _summarize_cq_string(seg, max_chars=max_chars)
            if sub:
                parts.append(sub)
            continue
        if not isinstance(seg, dict):
            continue
        stype = str(seg.get("type") or "")
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        data = data or {}
        if stype == "text":
            text = str(data.get("text") or "")
            if text.strip():
                parts.append(text.strip())
        elif stype == "image":
            parts.append(
                _image_label(
                    str(data.get("file") or ""),
                    str(data.get("url") or ""),
                    str(data.get("summary") or ""),
                )
            )
        elif stype == "at":
            target = str(data.get("name") or data.get("qq") or "")
            parts.append(f"@{target}" if target else "@某人")
        elif stype == "reply":
            continue  # 回复引用不进摘要
        elif stype == "node":
            content = data.get("content")
            inner = summarize_onebot_segments(content, max_chars=60) if content else ""
            parts.append(f"[合并转发:{inner}]" if inner else "[合并转发]")
        else:
            label = _SEGMENT_LABELS.get(stype)
            if label:
                parts.append(label)
    return _join(parts, max_chars)


# ---------------------------------------------------------------- 图片预览抽取
#
# 为什么要有这一段：机器人自己发出的一张图/表情包，模型只能看到 `[表情包]`
# 这个干巴巴的占位。群里紧接着问「@bot 这啥」时，模型根本不知道自己发了什么，
# 只能瞎猜。这里把出站图片抽成一张 512px 的 JPEG 预览（data URL，不落盘），
# 让模型能真正「看见」自己刚发出去的东西。

#: 单张源图上限；超过就跳过（宁可没有预览，也不要在出站路径上卡住）
_PREVIEW_MAX_BYTES = 8 * 1024 * 1024
#: 预览长边、JPEG 质量、一条消息最多带几张
_PREVIEW_MAX_SIDE = 512
_PREVIEW_QUALITY = 75
_PREVIEW_MAX_IMAGES = 2

#: 纯媒体标签的「具体程度」见 media_rank（[发送了表情包: 摸头] > [表情包] > [图片]）


def _accepts_kw(fn: Any, name: str) -> bool:
    """目标回调是否接受某个关键字参数（用于向旧调用方/旧单测保持兼容）。"""
    if fn is None:
        return False
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD or p.name == name:
            return True
    return False


def _local_path_of(source: str) -> str:
    """把 ``file:///...`` 或裸路径转成本地文件路径；远程/空值返回空串。"""
    s = str(source or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith(("http://", "https://", "//")):
        return ""  # 远程图不拉：出站路径上不能等网络
    if low.startswith("file:"):
        try:
            path = url2pathname(unquote(urlparse(s).path))
        except Exception:  # noqa: BLE001
            return ""
        # Windows 的 file:///E:/x.gif 会被解析成 /E:/x.gif
        if re.match(r"^[\\/][A-Za-z]:", path):
            path = path.lstrip("\\/")
        return path
    return s


def _read_image_bytes(source: str) -> bytes:
    """把图片源读成原始字节（base64://、data URL、file://、本地路径）。"""
    s = str(source or "").strip()
    if not s:
        return b""
    low = s.lower()
    if low.startswith("base64://"):
        raw = s[len("base64://") :]
        if len(raw) > _PREVIEW_MAX_BYTES * 4 // 3 + 64:
            return b""
        try:
            return base64.b64decode(raw + "=" * (-len(raw) % 4))
        except Exception:  # noqa: BLE001
            return b""
    if low.startswith("data:"):
        head, sep, body = s.partition(",")
        if not sep or ";base64" not in head.lower():
            return b""
        if len(body) > _PREVIEW_MAX_BYTES * 4 // 3 + 64:
            return b""
        try:
            return base64.b64decode(body + "=" * (-len(body) % 4))
        except Exception:  # noqa: BLE001
            return b""
    path = _local_path_of(s)
    if not path:
        return b""
    try:
        if not os.path.isfile(path):
            return b""
        if os.path.getsize(path) > _PREVIEW_MAX_BYTES:
            return b""
        with open(path, "rb") as f:
            return f.read(_PREVIEW_MAX_BYTES + 1)
    except OSError:
        return b""


def extract_preview_data_url(source: str, max_side: int = 512) -> str:
    """把一张图（GIF/WebP/PNG/JPEG…）压成 ``data:image/jpeg;base64,...`` 预览。

    动图抽一个代表帧（``n_frames // 3``，失败回退第 0 帧），转 RGB 后缩到
    长边 ``max_side`` 以内。Pillow 缺失、源不可读、解析失败一律静默返回空串：
    这条路径在消息已经发出去之后跑，绝不能抛异常。
    """
    try:
        raw = _read_image_bytes(source)
        if not raw or len(raw) > _PREVIEW_MAX_BYTES:
            return ""
        from PIL import Image  # 延迟导入：没装 Pillow 就自然降级

        limit = max(64, int(max_side or _PREVIEW_MAX_SIDE))
        with Image.open(io.BytesIO(raw)) as img:
            frames = int(getattr(img, "n_frames", 1) or 1)
            if frames > 1:
                try:
                    img.seek(frames // 3)
                except Exception:  # noqa: BLE001 - 抽帧失败就退回第 0 帧
                    try:
                        img.seek(0)
                    except Exception:  # noqa: BLE001
                        return ""
            frame = img.convert("RGB")
            frame.thumbnail((limit, limit))
            buf = io.BytesIO()
            frame.save(buf, format="JPEG", quality=_PREVIEW_QUALITY)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{data}" if data else ""
    except Exception:  # noqa: BLE001 - 预览只是加分项，失败就当没有
        return ""


def extract_image_previews(sources: Any, *, max_images: int = _PREVIEW_MAX_IMAGES) -> list[str]:
    """把若干图片源转成预览 data URL（保序去重、最多 ``max_images`` 张）。"""
    cap = max(1, int(max_images or _PREVIEW_MAX_IMAGES))
    out: list[str] = []
    seen: set[str] = set()
    for src in list(sources or []):
        if len(out) >= cap:
            break
        s = str(src or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        preview = extract_preview_data_url(s)
        if preview:
            out.append(preview)
    return out


def extract_onebot_image_sources(segments: Any) -> list[str]:
    """从 OneBot v11 出站消息里抽出图片源（file/url/path，含 CQ 码与合并转发）。"""
    if segments is None:
        return []
    if isinstance(segments, str):
        out: list[str] = []
        for match in _CQ_CODE_RE.finditer(segments):
            if str(match.group(1) or "").lower() != "image":
                continue
            params = _parse_cq_params(str(match.group(2) or ""))
            src = params.get("file") or params.get("url") or ""
            if src:
                out.append(src)
        return out
    if isinstance(segments, dict):
        seg_list: list[Any] = [segments]
    else:
        try:
            seg_list = list(segments)
        except TypeError:
            return []
    out = []
    for seg in seg_list:
        if isinstance(seg, str):
            out.extend(extract_onebot_image_sources(seg))
            continue
        if not isinstance(seg, dict):
            continue
        stype = str(seg.get("type") or "")
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        data = data or {}
        if stype == "image":
            src = str(data.get("file") or data.get("url") or data.get("path") or "")
            if src:
                out.append(src)
        elif stype in ("node", "nodes", "forward"):
            content = data.get("content") or data.get("messages")
            if content:
                out.extend(extract_onebot_image_sources(content))
    return out


def extract_component_image_sources(chain: Any) -> list[str]:
    """从 AstrBot 组件链里抽出图片源（优先本地 path/file，其次远程 url）。"""
    out: list[str] = []
    for comp in list(chain or []):
        if type(comp).__name__ != "Image":
            continue
        for attr in ("path", "file", "url"):
            val = str(getattr(comp, attr, "") or "").strip()
            if val:
                out.append(val)
                break
    return out


@dataclass
class _Echo:
    """上一次为某个群记录的内容，用来去重。"""

    text: str = ""
    at: float = 0.0
    media: bool = False
    #: 图片预览（data:image/jpeg;base64,...），最多 2 张
    images: list[str] = field(default_factory=list)


class BotEchoLog:
    """机器人自己发言的登记处：去重 + 限长 + 回调。

    两条捕获路径（协议端 tap、after_message_sent）会各自报一次同一条消息，
    所以去重放在这里做，调用方不必关心是谁报的。

    坑（实测踩过）：同一条 GIF 表情包发送，``BotSendTap`` 报成 ``[表情包]``、
    ``after_message_sent`` 报成 ``[图片]``，只按文本相等去重会留下两条，
    模型于是以为「发了一张图片和一个表情包」。因此这里对「同为纯媒体、
    且都属于图片/表情包系」的两次上报做合并，取更具体的标签（见
    :func:`media_rank`：``[发送了表情包: 摸头 (动图/GIF)]`` > ``[表情包]`` >
    ``[图片]``），并把后到的图片预览补到同一条上（通过 ``on_update`` 通知下游）。

    语义标签往往比协议端 tap 晚到（要等 ``after_message_sent`` 才拿到插件来源），
    所以「升级成带关键词的语义标签」这一种合并额外放宽到 ``media_merge_window``。
    """

    def __init__(
        self,
        on_record: Callable[..., None] | None = None,
        *,
        on_update: Callable[..., None] | None = None,
        dedupe_window: float = 2.5,
        media_merge_window: float = 8.0,
        max_chars: int = 160,
    ) -> None:
        self._on_record = on_record
        self._on_update = on_update
        self._record_accepts_images = _accepts_kw(on_record, "images")
        self._update_accepts_images = _accepts_kw(on_update, "images")
        self._dedupe_window = max(0.0, float(dedupe_window))
        self._media_merge_window = max(0.0, float(media_merge_window))
        self._max_chars = max(20, int(max_chars))
        self._last: dict[str, _Echo] = {}

    # ---- 回调（老调用方只吃 (group, text)，所以按签名探测再决定带不带 images） ----
    def _emit_record(self, gid: str, text: str, images: list[str]) -> None:
        if self._on_record is None:
            return
        try:
            if self._record_accepts_images:
                self._on_record(gid, text, images=list(images))
            else:
                self._on_record(gid, text)
        except Exception:  # noqa: BLE001 - 记账失败不该影响消息已经发出的事实
            pass

    def _emit_update(self, gid: str, text: str, images: list[str]) -> None:
        if self._on_update is None:
            return
        try:
            if self._update_accepts_images:
                self._on_update(gid, text, images=list(images))
            else:
                self._on_update(gid, text)
        except Exception:  # noqa: BLE001
            pass

    def record(
        self,
        group: str,
        text: str,
        *,
        media: bool = False,
        images: list[str] | None = None,
    ) -> bool:
        """登记一条机器人发言。返回 True 表示确实收下了（未被去重/丢弃）。"""
        gid = str(group or "").strip()
        body = _join([str(text or "")], self._max_chars)
        if not gid or not body:
            return False
        imgs = list(dict.fromkeys(str(u) for u in (images or []) if u))[:2]
        now = time.time()
        prev = self._last.get(gid)
        if prev is not None and now - prev.at <= self._dedupe_window:
            # 同一条消息被两条捕获路径各报一次（间隔毫秒级）
            if prev.text == body:
                # 后到的那条补上了图片预览 → 原地补全，不新增条目
                if imgs and not prev.images:
                    prev.images = imgs
                    self._emit_update(gid, prev.text, prev.images)
                return False
            # 同一条发送被两条路径分别识别成 [图片] / [表情包] → 合并成一条
            if media and prev.media and media_rank(prev.text) and media_rank(body):
                merged = body if media_rank(body) > media_rank(prev.text) else prev.text
                merged_imgs = prev.images or imgs
                if merged != prev.text or (imgs and not prev.images):
                    prev.text = merged
                    prev.images = merged_imgs
                    self._emit_update(gid, prev.text, prev.images)
                return False
        if prev is not None:
            # 语义标签比旧占位更具体时，放宽窗口原地升级同一条：
            # after_message_sent 拿到插件来源比协议端 tap 晚，别因此多记一条
            if (
                media
                and prev.media
                and now - prev.at <= self._media_merge_window
                and media_rank(body) >= _SEMANTIC_RANK
                and media_rank(body) > media_rank(prev.text)
            ):
                prev.text = body
                if imgs and not prev.images:
                    prev.images = imgs
                self._emit_update(gid, prev.text, prev.images)
                return False
            # 连发同样的图/表情包（刷屏），合并成一条，别把上下文挤爆
            if (
                media
                and prev.media
                and prev.text == body
                and now - prev.at <= self._media_merge_window
            ):
                return False
        self._last[gid] = _Echo(text=body, at=now, media=bool(media), images=imgs)
        self._emit_record(gid, body, imgs)
        return True

    def last(self, group: str) -> str:
        """最近一条已登记的发言（面板/调试用）。"""
        return self._last.get(str(group or "").strip(), _Echo()).text

    def last_images(self, group: str) -> list[str]:
        """最近一条已登记发言附带的图片预览（面板/调试用）。"""
        return list(self._last.get(str(group or "").strip(), _Echo()).images)


class BotSendTap:
    """挂在协议端 client 上的出站 tap：机器人往群里发东西时回调一次。

    为什么优先挂在 ``bot.api.call_action``（即 aiocqhttp 的 ``_api.call_action``）：
    AstrBot 里「机器人出声」的路子很多——LLM 回复和插件结果走管线、
    `context.send_message` 主动发、插件调 `bot.send_group_msg` /
    `bot.call_action`（如入群欢迎/退群说明）、还有插件直接调
    `bot.api.call_action`（如抽老婆/Qzone）。在 aiocqhttp 里它们最终全都
    汇聚到 `bot._api.call_action`，在这一层挂一次就能全收齐且不重复。
    若目标对象没有 `.api` / `._api`（例如精简 mock），则退回挂在 `bot.call_action`。
    """

    #: 往群里发的动作；私聊动作不采（戳/艾特上下文只按群维护）
    _GROUP_ACTIONS = ("send_group_msg", "send_group_forward_msg")

    def __init__(self, on_group_message: Callable[..., Any]) -> None:
        self._on_group_message = on_group_message
        self._accepts_media = self._check_accepts_media(on_group_message)
        self._accepts_images = _accepts_kw(on_group_message, "images")
        # key = id(target), value = (target, orig, had_instance_attr)
        self._installed: dict[int, tuple[Any, Any, bool]] = {}

    @staticmethod
    def _check_accepts_media(fn: Callable[..., Any]) -> bool:
        try:
            sig = inspect.signature(fn)
            for p in sig.parameters.values():
                if p.kind == inspect.Parameter.VAR_KEYWORD or p.name == "media":
                    return True
            return False
        except (TypeError, ValueError):
            return False

    @property
    def installed(self) -> int:
        return len(self._installed)

    @staticmethod
    def _resolve_target(bot: Any) -> Any:
        """优先定位底层的 api 对象（如 aiocqhttp 的 CQHttp._api），否则用 bot 本身。"""
        if bot is None:
            return None
        for attr in ("api", "_api"):
            try:
                api_obj = getattr(bot, attr, None)
            except Exception:  # noqa: BLE001
                api_obj = None
            if api_obj is not None and callable(getattr(api_obj, "call_action", None)):
                return api_obj
        if callable(getattr(bot, "call_action", None)):
            return bot
        return None

    def install(self, bot: Any) -> bool:
        """给一个协议端实例挂 tap。重复安装/无 call_action 时返回 False。"""
        target = self._resolve_target(bot)
        if target is None:
            return False
        key = id(target)
        if key in self._installed:
            return False
        if getattr(target, "_pl_bot_echo_tap", False) is True:
            return False
        orig = getattr(target, "call_action", None)
        if not callable(orig):
            return False
        had_instance_attr = "call_action" in getattr(target, "__dict__", {})

        async def tap(*args, **params):
            result = await orig(*args, **params)
            # 只有真的发成功了才记账：发失败的消息不该进上下文
            try:
                action = args[0] if args else params.get("action")
                call_params = dict(params)
                call_params.pop("action", None)
                if len(args) > 1 and isinstance(args[1], dict):
                    call_params = {**args[1], **call_params}
                self._capture(action, call_params)
            except Exception:  # noqa: BLE001 - 记账失败绝不能影响发送结果
                pass
            return result

        try:
            setattr(target, "call_action", tap)
            setattr(target, "_pl_bot_echo_tap", True)
        except Exception:  # noqa: BLE001
            return False
        self._installed[key] = (target, orig, had_instance_attr)
        return True

    def uninstall(self, bot: Any) -> None:
        target = self._resolve_target(bot)
        candidates = [target, bot]
        item = None
        real_target = None
        for cand in candidates:
            if cand is None:
                continue
            found = self._installed.pop(id(cand), None)
            if found is not None:
                item = found
                real_target = cand
                break
        if item is None or real_target is None:
            return
        _, orig, had_instance_attr = item
        try:
            if had_instance_attr:
                setattr(real_target, "call_action", orig)
            else:
                delattr(real_target, "call_action")  # 还原成类方法，别留影子实例属性
        except Exception:  # noqa: BLE001
            try:
                setattr(real_target, "call_action", orig)
            except Exception:  # noqa: BLE001
                pass
        try:
            setattr(real_target, "_pl_bot_echo_tap", False)
        except Exception:  # noqa: BLE001
            pass

    def uninstall_all(self) -> None:
        for target, _, _ in list(self._installed.values()):
            self.uninstall(target)

    def _capture(self, action: Any, params: dict) -> None:
        """从一次出站动作里抽出「发给哪个群、说了什么」。"""
        name = str(action or "")
        params = params or {}
        group = params.get("group_id")
        if name in self._GROUP_ACTIONS:
            pass
        elif name == "send_msg" and (
            group or str(params.get("message_type") or "") == "group"
        ):
            pass
        else:
            return  # 私聊/取数据之类的动作不采
        if not group:
            return
        payload = params.get("message")
        if payload is None and name == "send_group_forward_msg":
            payload = params.get("messages")
        text = summarize_onebot_segments(payload)
        if not text and name == "send_group_forward_msg":
            text = "[合并转发]"
        if not text:
            return
        if not self._accepts_media:
            self._on_group_message(str(group), text)
            return
        media = is_media_only_summary(text)
        if not self._accepts_images:
            self._on_group_message(str(group), text, media=media)
            return
        images: list[str] = []
        # 先快筛：出站消息里压根没有图片段时不做任何解码
        sources = extract_onebot_image_sources(payload)
        if sources:
            images = extract_image_previews(sources)
        self._on_group_message(str(group), text, media=media, images=images)


__all__ = [
    "BotEchoLog",
    "BotSendTap",
    "STICKER_SEMANTIC_PREFIX",
    "detect_meme_keyword",
    "detect_meme_source",
    "extract_component_image_sources",
    "extract_image_previews",
    "extract_onebot_image_sources",
    "extract_preview_data_url",
    "is_media_only_summary",
    "is_sticker_summary",
    "media_rank",
    "refine_self_media_summary",
    "sticker_label",
    "summarize_components",
    "summarize_onebot_segments",
]
