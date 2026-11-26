"""戳一戳/纯艾特的统一交互上下文（两种情景）。

情景 A：用户先说话/发图（忘记 @），随后补一个 @ 或戳一戳
        → 触发时把「前面那几条」当上下文一起带进 LLM。
情景 B：用户先 @ / 戳一戳，随后才发文字或图
        → 先记一个 pending 触发，等后续消息到了再合并发出。

设计：纯状态机，不注册事件钩子、不直接调 LLM；由 on_poke_event 驱动。
图片同时带 url（gif/webp 都支持，交给宿主模型处理）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InteractItem:
    sender: str
    text: str
    image_urls: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    def has_content(self) -> bool:
        return bool(self.text.strip() or self.image_urls)


@dataclass
class PendingTrigger:
    kind: str = "poke"          # "poke" / "at"
    sender: str = ""
    group: str = ""
    at: float = field(default_factory=time.time)


class InteractContext:
    """按群维护最近消息 + 待合并触发；有界、带过期。"""

    def __init__(self, window: int = 6, ttl: float = 20.0) -> None:
        self._window = max(2, int(window))
        self._ttl = max(3.0, float(ttl))
        self._recent: dict[str, deque[InteractItem]] = {}
        self._pending: dict[str, PendingTrigger] = {}

    # ---- 配置热更新（面板改完立即生效，不用重载） ----
    def update(self, *, window: int | None = None, ttl: float | None = None) -> None:
        if window is not None:
            w = max(2, int(window))
            if w != self._window:
                self._window = w
                for key in list(self._recent):
                    self._recent[key] = deque(list(self._recent[key])[-w:], maxlen=w)
        if ttl is not None:
            self._ttl = max(3.0, float(ttl))

    # ---- 最近消息 ----
    def observe(self, group: str, sender: str, text: str,
                image_urls: list[str] | None = None) -> None:
        key = str(group or "")
        if not key:
            return
        bucket = self._recent.setdefault(key, deque(maxlen=self._window))
        bucket.append(InteractItem(
            sender=str(sender or ""), text=str(text or ""),
            image_urls=[str(u) for u in (image_urls or []) if u],
        ))
        self._expire(key)

    def recent(self, group: str, seconds: float | None = None,
               exclude_sender: str = "") -> list[InteractItem]:
        key = str(group or "")
        limit = self._ttl if seconds is None else max(0.0, float(seconds))
        now = time.time()
        out = []
        for it in self._recent.get(key, []):
            if now - it.at > limit:
                continue
            if exclude_sender and it.sender == exclude_sender and not it.has_content():
                continue
            if it.has_content():
                out.append(it)
        return out

    # ---- pending 触发 ----
    def set_pending(self, group: str, kind: str, sender: str) -> None:
        self._pending[str(group or "")] = PendingTrigger(
            kind=str(kind or "poke"), sender=str(sender or ""), group=str(group or "")
        )

    def pop_pending(self, group: str, seconds: float | None = None) -> PendingTrigger | None:
        key = str(group or "")
        p = self._pending.get(key)
        if p is None:
            return None
        limit = self._ttl if seconds is None else max(0.0, float(seconds))
        if time.time() - p.at > limit:
            self._pending.pop(key, None)
            return None
        return self._pending.pop(key)

    def has_pending(self, group: str) -> bool:
        return str(group or "") in self._pending

    def _expire(self, key: str) -> None:
        now = time.time()
        bucket = self._recent.get(key)
        if bucket:
            while bucket and now - bucket[0].at > self._ttl:
                bucket.popleft()
        if not bucket:
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


def build_prompt(kind: str, trigger: PendingTrigger | None,
                 context_items: list[InteractItem]) -> tuple[str, list[str]]:
    """把触发 + 上下文拼成 prompt 与图片列表。

    返回 (prompt, image_urls)。图片去重、保序。
    """
    action = "刚被用户戳了一下" if kind == "poke" else "用户刚 @ 了你但没说别的"
    lines = [f"{action}，结合下面的上下文自然回应一句（不要复述，不要说'你刚才说了'）："]
    images: list[str] = []
    seen: set[str] = set()
    for it in context_items[-6:]:
        prefix = "（同一个人）" if trigger and it.sender == trigger.sender else ""
        if it.text.strip():
            lines.append(f"- {prefix}{it.text.strip()[:200]}")
        for u in it.image_urls:
            if u not in seen:
                seen.add(u)
                images.append(u)
    if len(lines) == 1 and not images:
        lines.append("- （没有更多上下文，就自然打个招呼）")
    return "\n".join(lines), images


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
