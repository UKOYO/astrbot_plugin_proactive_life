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
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

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


def _join(parts: list[str], max_chars: int) -> str:
    """拼成一行并压掉多余空白；超长截断（上下文不该被一条长消息撑爆）。"""
    text = " ".join(str(p) for p in parts if str(p or "").strip())
    text = " ".join(text.split())
    limit = max(20, int(max_chars))
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def is_media_only_summary(summary: str) -> bool:
    """判断摘要是否全由媒体占位标签组成（不含实质正文）。"""
    tokens = str(summary or "").split()
    return bool(tokens) and all(tok in _MEDIA_ONLY_LABELS for tok in tokens)


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


@dataclass
class _Echo:
    """上一次为某个群记录的内容，用来去重。"""

    text: str = ""
    at: float = 0.0
    media: bool = False


class BotEchoLog:
    """机器人自己发言的登记处：去重 + 限长 + 回调。

    两条捕获路径（协议端 tap、after_message_sent）会各自报一次同一条消息，
    所以去重放在这里做，调用方不必关心是谁报的。
    """

    def __init__(
        self,
        on_record: Callable[[str, str], None] | None = None,
        *,
        dedupe_window: float = 2.5,
        media_merge_window: float = 8.0,
        max_chars: int = 160,
    ) -> None:
        self._on_record = on_record
        self._dedupe_window = max(0.0, float(dedupe_window))
        self._media_merge_window = max(0.0, float(media_merge_window))
        self._max_chars = max(20, int(max_chars))
        self._last: dict[str, _Echo] = {}

    def record(self, group: str, text: str, *, media: bool = False) -> bool:
        """登记一条机器人发言。返回 True 表示确实收下了（未被去重/丢弃）。"""
        gid = str(group or "").strip()
        body = _join([str(text or "")], self._max_chars)
        if not gid or not body:
            return False
        now = time.time()
        prev = self._last.get(gid)
        if prev is not None:
            # 同一条消息被两条捕获路径各报一次（间隔毫秒级）
            if prev.text == body and now - prev.at <= self._dedupe_window:
                return False
            # 连发同样的图/表情包（刷屏），合并成一条，别把上下文挤爆
            if (
                media
                and prev.media
                and prev.text == body
                and now - prev.at <= self._media_merge_window
            ):
                return False
        self._last[gid] = _Echo(text=body, at=now, media=bool(media))
        if self._on_record is not None:
            try:
                self._on_record(gid, body)
            except Exception:  # noqa: BLE001 - 记账失败不该影响消息已经发出的事实
                pass
        return True

    def last(self, group: str) -> str:
        """最近一条已登记的发言（面板/调试用）。"""
        return self._last.get(str(group or "").strip(), _Echo()).text


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
        if self._accepts_media:
            self._on_group_message(str(group), text, media=is_media_only_summary(text))
        else:
            self._on_group_message(str(group), text)


__all__ = [
    "BotEchoLog",
    "BotSendTap",
    "is_media_only_summary",
    "summarize_components",
    "summarize_onebot_segments",
]
