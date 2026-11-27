"""戳一戳/艾特的统一交互上下文（两种情景 + 自己近期发言回溯）。

情景 A：用户先说话/发图（忘记 @），随后补一个 @ 或戳一戳
        → 触发时把「前面那几条」当上下文一起带进 LLM。
情景 B：用户先 @ / 戳一戳，随后才发文字或图
        → 先记一个 pending 触发，等后续消息到了再合并发出。
情景 C：机器人刚走表情包制作/入群欢迎/退群说明等插件管线发出过消息，
        随后群友带字 @ 机器人 → 通过临时注入块把这段刚发生的动态补给 LLM，
        避免模型只看到很久以前的对话历史而没头没尾乱接话。

设计：纯状态机，不注册事件钩子、不直接调 LLM；由 main.py 事件钩子驱动。
图片同时带 url（gif/webp 都支持，交给宿主模型处理）。
窗口里同时按时间序收「群友说的」和「机器人自己刚发出去的」（role="self"）——
表情包制作、入群欢迎、退群通知、主动插话这类自己做的事，被戳/被 @ 时
要能被模型看见。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: 带字 @ 触发时的临时注入块幂等标记（不落盘、不影响 system_prompt 缓存）
RECENT_ECHO_MARKER = "【本群刚才的动态】"

#: 纯媒体标签的具体程度：[表情包] 比 [图片] 更具体，双路径上报时取更具体的那个
#: （标签规则统一由 modules.bot_echo 维护，这里只做排序，不重复媒体逻辑）
try:  # 极端加载方式（非包内导入）下退回本地兜底表
    from .bot_echo import media_rank as _shared_media_rank
except Exception:  # noqa: BLE001
    _shared_media_rank = None  # type: ignore[assignment]

#: 兜底用的具体程度表，档位与 bot_echo.media_rank 保持一致
_MEDIA_SPECIFICITY = {"[图片]": 1, "[表情包]": 2, "[表情包 (动图/GIF)]": 3}

#: 带关键词的语义标签前缀（``[发送了表情包: 摸头 (动图/GIF)]``）
_SEMANTIC_STICKER_PREFIX = "[发送了表情包"

#: 系统状态符号：AstrBot 的指令回执几乎都带，正常群聊几乎不带
_RECEIPT_SYMBOLS = ("✅", "❌", "⚠", "✔", "✖", "☑", "⛔")

#: 指令回执的典型开头（英文大小写不敏感），命中即视为系统回执
_RECEIPT_PREFIXES = (
    "requested to stop",
    "successfully switched",
    "successfully reset",
    "current provider",
    "usage:",
    "用法:",
    "用法：",
    "已切换",
    "切换成功",
    "已重置",
    "重置成功",
    "已停止",
    "已启用",
    "已禁用",
    "已开启",
    "已关闭",
    "已设置",
    "设置成功",
    "操作成功",
    "新会话已",
    "已创建新会话",
    "当前模型",
    "当前会话",
    "当前提供商",
    "未知指令",
    "无效的指令",
)

#: 单独出现的指令名（用户发 ``/provider``、``/new`` 这类，本身没有任何聊天信息量）
_COMMAND_WORDS = frozenset({
    "provider", "providers", "new", "reset", "help", "model", "models",
    "persona", "personas", "plugin", "plugins", "llm", "session", "sid",
    "history", "stop", "restart", "reload", "config", "dashboard",
    "websearch", "kb", "tts", "stt", "version", "alter_cmd",
})

#: 指令前缀（``/provider``、``！new`` 之类）
_COMMAND_PREFIXES = "/!！#／"


def is_receipt_text(text: str) -> bool:
    """是不是「指令类回执 / 系统消息」——这类内容不该进戳/艾特上下文。

    踩过的坑：群里只要有人发过 ``/provider``、``/new``，或者机器人回过一句
    ``✅ Requested to stop 1 running tasks.``，这些都会落到上下文窗口里。
    纯艾特时窗口「非空」，逻辑就误判成「用户先说过话」，抢答一句空话，
    用户随后补的正文反而被当成普通群消息漏掉。
    """
    body = str(text or "").strip()
    if not body:
        return False
    if any(sym in body for sym in _RECEIPT_SYMBOLS):
        return True
    low = body.lower()
    if low.startswith(_RECEIPT_PREFIXES):
        return True
    # 指令本身：``/provider`` / ``！new`` / 裸 ``provider``
    parts = body.lstrip(_COMMAND_PREFIXES).split()
    if not parts:
        return False
    word = parts[0].strip(":：。.!！?？").lower()
    return bool(word) and word in _COMMAND_WORDS


def _media_rank(text: str) -> int:
    """纯媒体标签的具体程度（0 表示不是图片/表情包占位）。"""
    body = str(text or "").strip()
    if _shared_media_rank is not None:
        try:
            return int(_shared_media_rank(body))
        except Exception:  # noqa: BLE001 - 兜底表也能用
            pass
    if body.startswith(_SEMANTIC_STICKER_PREFIX):
        return 4
    return _MEDIA_SPECIFICITY.get(body, 0)


@dataclass
class InteractItem:
    sender: str
    text: str
    image_urls: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)
    #: "user" = 群友/用户说的，"self" = 机器人自己刚发出去的
    role: str = "user"
    #: 群友昵称（可选，空串时不强行拼前缀）
    sender_name: str = ""

    def has_content(self) -> bool:
        return bool(self.text.strip() or self.image_urls)

    def is_self(self) -> bool:
        return self.role == "self"


@dataclass
class PendingTrigger:
    kind: str = "poke"          # "poke" / "at"
    sender: str = ""
    group: str = ""
    at: float = field(default_factory=time.time)


class InteractContext:
    """按群维护最近消息 + 待合并触发；有界、带过期。"""

    def __init__(
        self,
        window: int = 6,
        ttl: float = 20.0,
        *,
        self_ttl: float | None = None,
    ) -> None:
        self._window = max(2, int(window))
        self._ttl = max(3.0, float(ttl))
        self._self_ttl: float | None = (
            max(3.0, float(self_ttl)) if self_ttl is not None else None
        )
        self._recent: dict[str, deque[InteractItem]] = {}
        self._pending: dict[str, PendingTrigger] = {}

    def _item_ttl(self, item: InteractItem) -> float:
        if item.is_self() and self._self_ttl is not None:
            return max(self._ttl, self._self_ttl)
        return self._ttl

    # ---- 配置热更新（面板改完立即生效，不用重载） ----
    def update(
        self,
        *,
        window: int | None = None,
        ttl: float | None = None,
        self_ttl: float | None = None,
    ) -> None:
        if window is not None:
            w = max(2, int(window))
            if w != self._window:
                self._window = w
                for key in list(self._recent):
                    self._recent[key] = deque(list(self._recent[key])[-w:], maxlen=w)
        if ttl is not None:
            self._ttl = max(3.0, float(ttl))
        if self_ttl is not None:
            self._self_ttl = max(3.0, float(self_ttl))

    # ---- 最近消息 ----
    def observe(
        self,
        group: str,
        sender: str,
        text: str,
        image_urls: list[str] | None = None,
        *,
        role: str = "user",
        sender_name: str = "",
    ) -> None:
        key = str(group or "")
        if not key:
            return
        urls = [str(u) for u in (image_urls or []) if u]
        # 指令类回执（``/provider``、``/new``、带 ✅ 的系统消息）不进上下文：
        # 它们既不是群友的聊天内容，也不该让纯艾特误判成「用户先说过话」。
        if not urls and is_receipt_text(text):
            return
        bucket = self._recent.setdefault(key, deque(maxlen=self._window))
        bucket.append(InteractItem(
            sender=str(sender or ""),
            text=str(text or ""),
            image_urls=urls,
            role="self" if role == "self" else "user",
            sender_name=str(sender_name or "").strip(),
        ))
        self._expire(key)

    def observe_self(
        self,
        group: str,
        text: str,
        *,
        bot_name: str = "",
        image_urls: list[str] | None = None,
    ) -> None:
        """记录机器人自己刚发出去的一条（文本已是可读摘要，含图片/表情包占位）。

        去重由上游 `modules.bot_echo.BotEchoLog` 负责：同一条消息会被协议端
        tap 与 after_message_sent 各报一次，这里不再重复判断。

        ``image_urls`` 是这张图/表情包的预览（``data:image/jpeg;base64,...``），
        让模型在被追问时能真的「看见」自己刚发的是什么。
        """
        urls = [str(u) for u in (image_urls or []) if u]
        deduped = list(dict.fromkeys(urls))[:2]
        self.observe(
            group, bot_name, text, deduped, role="self", sender_name=bot_name
        )

    def update_last_self(
        self,
        group: str,
        *,
        text: str | None = None,
        image_urls: list[str] | None = None,
    ) -> bool:
        """原地补全最后一条 ``self`` 项（不新增条目）。

        用于双路径上报去重：协议端 tap 先报 ``[图片]``、``after_message_sent``
        再报 ``[表情包]``（或更具体的 ``[发送了表情包: 摸头 (动图/GIF)]``）
        并带上预览时，把更具体的标签与图片补到同一条上，否则模型会以为
        「发了一张图片和一个表情包」。

        返回 True 表示确实改动了内容。
        """
        key = str(group or "")
        bucket = self._recent.get(key)
        if not bucket:
            return False
        target: InteractItem | None = None
        for it in reversed(bucket):
            if it.is_self():
                target = it
                break
        if target is None:
            return False
        changed = False
        new_text = str(text or "").strip()
        if new_text:
            cur = target.text.strip()
            if not cur or (
                new_text != cur and _media_rank(new_text) > _media_rank(cur)
            ):
                target.text = new_text[:200]
                changed = True
        imgs = [str(u) for u in (image_urls or []) if u]
        if imgs:
            merged = list(dict.fromkeys([*target.image_urls, *imgs]))[:2]
            if merged != target.image_urls:
                target.image_urls = merged
                changed = True
        return changed

    def recent(
        self,
        group: str,
        seconds: float | None = None,
        exclude_sender: str = "",
    ) -> list[InteractItem]:
        key = str(group or "")
        explicit = None if seconds is None else max(0.0, float(seconds))
        now = time.time()
        out = []
        for it in self._recent.get(key, []):
            limit = self._item_ttl(it) if explicit is None else explicit
            if now - it.at > limit:
                continue
            if exclude_sender and it.sender == exclude_sender and not it.has_content():
                continue
            if it.has_content():
                out.append(it)
        return out

    def relevant(
        self,
        group: str,
        sender: str,
        seconds: float | None = None,
    ) -> list[InteractItem]:
        """只取「跟这次触发相关」的上下文：同一个人说的 + 我自己刚发的。

        为什么不能直接用 :meth:`recent`：群里一直有人在聊天时窗口永远非空，
        纯艾特/戳一戳就会被别人的闲聊顶成「情景 A」，抢答一句跟眼前无关的话。
        别的群友的发言只有在被本次触发的人自己接上时才有意义，所以这里剔掉。
        """
        who = str(sender or "")
        return [
            it
            for it in self.recent(group, seconds)
            if it.is_self() or (who and it.sender == who)
        ]

    def same_sender(
        self,
        group: str,
        sender: str,
        seconds: float | None = None,
    ) -> list[InteractItem]:
        """只取触发者本人说过的内容（不含机器人自己的发言）。"""
        who = str(sender or "")
        if not who:
            return []
        return [it for it in self.recent(group, seconds) if it.sender == who and not it.is_self()]

    # ---- pending 触发 ----
    def set_pending(self, group: str, kind: str, sender: str) -> None:
        self._pending[str(group or "")] = PendingTrigger(
            kind=str(kind or "poke"), sender=str(sender or ""), group=str(group or "")
        )

    def peek_pending(self, group: str, seconds: float | None = None) -> PendingTrigger | None:
        """看一眼有没有待合并的触发，但不消费它（过期则顺手清掉）。"""
        key = str(group or "")
        p = self._pending.get(key)
        if p is None:
            return None
        limit = self._ttl if seconds is None else max(0.0, float(seconds))
        if time.time() - p.at > limit:
            self._pending.pop(key, None)
            return None
        return p

    def pop_pending(self, group: str, seconds: float | None = None) -> PendingTrigger | None:
        key = str(group or "")
        if self.peek_pending(key, seconds) is None:
            return None
        return self._pending.pop(key)

    def take_pending_for(
        self,
        group: str,
        sender: str,
        seconds: float | None = None,
    ) -> PendingTrigger | None:
        """只有这条消息来自触发者本人时，才把 pending 消费掉。

        pending 是按群存的：别人随便说一句就把它吃掉并触发回复，会出现
        「@ 完等正文，结果别人一句闲聊把机器人勾走」，正文反而漏掉。
        """
        p = self.peek_pending(group, seconds)
        if p is None:
            return None
        who = str(sender or "")
        if p.sender and who and p.sender != who:
            return None
        return self.pop_pending(group, seconds)

    def has_pending(self, group: str) -> bool:
        return str(group or "") in self._pending

    def _expire(self, key: str) -> None:
        now = time.time()
        bucket = self._recent.get(key)
        if bucket:
            kept = [it for it in bucket if now - it.at <= self._item_ttl(it)]
            if kept:
                if len(kept) != len(bucket):
                    self._recent[key] = deque(kept[-self._window :], maxlen=self._window)
            else:
                self._recent.pop(key, None)
        p = self._pending.get(key)
        if p and now - p.at > self._ttl:
            self._pending.pop(key, None)

    def clear(self, group: str | None = None) -> None:
        if group:
            self._recent.pop(str(group), None)
            self._pending.pop(str(group), None)
        else:
            self._recent.clear()
            self._pending.clear()


def _rel_time(delta: float) -> str:
    """把「距今多少秒」渲染成一眼能懂的中文相对时间。"""
    d = max(0.0, float(delta or 0.0))
    if d < 3:
        return "刚刚"
    if d < 60:
        return f"{int(d)}秒前"
    if d < 3600:
        return f"{max(1, int(d // 60))}分钟前"
    if d < 86400:
        return f"{max(1, int(d // 3600))}小时前"
    return f"{max(1, int(d // 86400))}天前"


def _format_item_line(
    it: InteractItem,
    *,
    me: str,
    trigger: PendingTrigger | None = None,
    now: float | None = None,
) -> str:
    """把单条 InteractItem 渲染为上下文行（无内容返回空串）。

    ``now`` 传入时会带上相对时间戳（``[9秒前]``）：没有时间锚点，模型分不清
    「刚刚发生」和「几小时前的旧对话」，会把旧话题硬套到眼前的短句上。
    """
    body = it.text.strip()[:200]
    if not body and it.image_urls:
        body = "[图片]"
    if not body:
        return ""
    stamp = f"[{_rel_time(now - it.at)}] " if now is not None else ""
    if it.is_self():
        return f"- {stamp}{me}：{body}"
    same = bool(trigger and it.sender and it.sender == trigger.sender)
    if it.sender_name:
        tag = f"{it.sender_name}（同一个人）" if same else it.sender_name
        return f"- {stamp}{tag}：{body}"
    prefix = "（同一个人）" if same else ""
    return f"- {stamp}{prefix}{body}"


def build_prompt(
    kind: str,
    trigger: PendingTrigger | None,
    context_items: list[InteractItem],
    bot_name: str = "",
) -> tuple[str, list[str]]:
    """把触发 + 上下文拼成 prompt 与图片列表。

    返回 (prompt, image_urls)。图片去重、保序。

    上下文按时间序排，群友消息以昵称开头，机器人自己刚发的标成「我」——
    否则模型会把自己发过的表情包/欢迎语当成群友说的，接话就串台。
    """
    action = "刚被用户戳了一下" if kind == "poke" else "用户刚 @ 了你但没说别的"
    lines = [
        f"{action}，结合下面的上下文自然回应一句（不要复述，不要说'你刚才说了'）：",
        "（标「我」的是你自己刚才发出去的，别当成群友说的）",
    ]
    images: list[str] = []
    seen: set[str] = set()
    has_context = False
    me = f"我（{bot_name}）" if bot_name and bot_name != "我" else "我"
    for it in context_items[-6:]:
        line = _format_item_line(it, me=me, trigger=trigger)
        if line:
            lines.append(line)
            has_context = True
        for u in it.image_urls:
            if u not in seen:
                seen.add(u)
                images.append(u)
                has_context = True
    if not has_context and not images:
        lines.append("- （没有更多上下文，就自然打个招呼）")
    return "\n".join(lines), images


def collect_recent_self_images(
    context_items: list[InteractItem],
    max_images: int = 2,
) -> list[str]:
    """收集最近几条 ``self`` 项里的图片预览（最新优先、保序去重）。

    只认 ``data:image/...`` 预览与本地图片路径：远程 URL 交给宿主去下载会拖慢
    本轮回复，出站 tap 阶段也根本拿不到。
    """
    cap = max(1, int(max_images or 2))
    out: list[str] = []
    seen: set[str] = set()
    for it in reversed(list(context_items or [])):
        if not it.is_self():
            continue
        for url in it.image_urls:
            s = str(url or "").strip()
            if not s or s in seen:
                continue
            if not (s.startswith("data:image/") or _looks_local_image(s)):
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= cap:
                return out
    return out


def _looks_local_image(url: str) -> bool:
    """看起来像本地图片路径（排除远程 URL）。"""
    low = str(url or "").strip().lower()
    if not low or low.startswith(("http://", "https://", "//", "data:")):
        return False
    return low.endswith(
        (".gif", ".webp", ".png", ".jpg", ".jpeg", ".bmp", ".apng", ".tif", ".tiff")
    )


def build_recent_echo_block(
    context_items: list[InteractItem],
    *,
    bot_name: str = "",
    current_prompt: str = "",
    max_items: int = 6,
    now: float | None = None,
    has_attached_images: bool = False,
) -> str:
    """为群聊正常带字 @ 回复拼出「本群刚才的动态」临时块。

    只在窗口里确实有「机器人自己刚发出的消息」（表情包、插件管线直发、主动
    插话这类不进对话历史的内容）时生成；平时没自己发言返回空串，不给常规
    对话徒增噪音。

    注意（实测踩过的坑）：header 里**不要写任何具体场景举例**。早先写了
    「表情包制作、入群欢迎、退群通知等插件消息不会出现在对话历史里」，低智
    模型直接把「入群欢迎」当成真发生过的群动态，把群友的 `hyw` 猜成「欢迎我」。
    这里只讲通用规则，不给它编故事的素材。
    """
    items = list(context_items or [])
    if not any(it.is_self() for it in items):
        return ""
    cur = " ".join(str(current_prompt or "").split())
    # 末尾如果刚好是当前正在回复的这条用户消息，剔除避免和本次提问重复
    if items and not items[-1].is_self() and cur:
        tail = " ".join(items[-1].text.split())
        if tail and (tail == cur or cur.endswith(tail)):
            items = items[:-1]
    if not any(it.is_self() for it in items):
        return ""
    me = f"我（{bot_name}）" if bot_name and bot_name != "我" else "我"
    cap = max(2, int(max_items))
    now_ts = float(now) if now is not None else time.time()
    rendered: list[str] = []
    for it in items[-cap:]:
        line = _format_item_line(it, me=me, now=now_ts)
        if line:
            rendered.append(line)
    if not rendered:
        return ""
    header = (
        f"{RECENT_ECHO_MARKER}（下面是刚刚在这个群里发生的最新动态，比上面的历史对话更新；"
        f"方括号里是距现在多久，标「{me}」的是你自己刚才发出去的消息。"
        "用户此刻的发言——尤其是短词、缩写、反问或吐槽——优先是在接下面这件事的话，"
        "就顺着这件事回应，不要硬扯回很久以前的旧对话。"
        "只把下面这几条当事实，不要凭空补出没发生过的群动态，也不要复述本块。）"
    )
    lines = [header, *rendered]
    if has_attached_images:
        lines.append(
            f"（附带的图片是上面标「{me}」刚发出的那张，仅作背景参考，"
            "按平时聊天口吻自然接话，切忌像做题家一样逐个细节赏析画面）"
        )
    return "\n".join(lines)


def is_pure_at(messages: list[Any], bot_id: str = "") -> bool:
    """消息组件全是 At（且指向 bot 或全体），没有任何正文/图片。"""
    if not messages:
        return False
    try:
        from astrbot.api.message_components import At, Plain, Image
    except Exception:  # noqa: BLE001
        return False
    has_at = False
    for comp in messages:
        ctype = type(comp).__name__
        if ctype == "At":
            target = str(getattr(comp, "qq", "") or "")
            if bot_id and target and target not in (bot_id, "all"):
                continue
            has_at = True
        elif ctype == "Plain":
            if str(getattr(comp, "text", "") or "").strip():
                return False
        elif ctype in ("Image", "Face", "Reply", "Video"):
            return False
    return has_at
