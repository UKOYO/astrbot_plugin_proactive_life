"""群聊場景感知（从 astrbot_plugin_context_aware 的核心提炼并优化）。

职责：在 LLM 请求前，分析「谁在跟谁说话」与触发类型，生成一段
<conversation_scene> 临时提示，让模型明白它是不是被叫、该不该接话，
避免把别人之间的对话当成在问自己（抢答）。

设计原则：
- 保守推断：不确定就判为「群聊」，绝不轻易判成「在跟 Bot 说」。
- 无副作用：纯函数式分析，不写会话历史、不调用模型、不持久化。
- 低耦合：从 event 用 duck typing 提取，不硬依赖 AstrBot 内部类型。
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

# 触发类型
TRIGGER_AT = "at"
TRIGGER_AT_ALL = "at_all"
TRIGGER_REPLY = "reply"
TRIGGER_MENTION = "mention"
TRIGGER_ACTIVE = "active"
TRIGGER_UNKNOWN = "unknown"

# 轻声回复特征（Bot 刚回过、用户像在回应时才用）
REPLY_STARTERS = (
    "好的", "好", "嗯", "哦", "ok", "okay", "收到", "谢谢", "謝謝", "感谢",
    "行", "可以", "对", "對", "是", "不是", "哈哈", "草", "确实", "確實",
)

# 名字边界：英文/数字名字用词边界，避免 "robot" 命中 "bot"
_WORD_NAME_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class SceneMessage:
    """一条群聊消息的精简记录。"""

    sender_id: str = ""
    sender_name: str = ""
    content: str = ""
    timestamp: float = 0.0
    is_bot: bool = False
    at_bot: bool = False
    at_all: bool = False
    at_targets: list[str] = field(default_factory=list)  # 被 @ 的目标 id/名字
    reply_to_id: str = ""
    talking_to: str = "group"       # "bot" / "group" / 某个 sender_id
    talking_to_name: str = "群聊"
    has_image: bool = False

    @property
    def is_to_bot(self) -> bool:
        return self.talking_to == "bot"


class SceneAnalyzer:
    """从事件提取消息、判定触发类型、推断对话对象。"""

    def __init__(
        self,
        bot_id: str = "",
        bot_names: Iterable[str] | None = None,
        reply_starters: Iterable[str] = REPLY_STARTERS,
    ) -> None:
        self.bot_id = str(bot_id or "")
        self.bot_names = tuple(
            str(n).strip().lower() for n in (bot_names or []) if str(n).strip()
        )
        self._name_patterns: list[tuple[str, re.Pattern[str] | None]] = []
        for n in self.bot_names:
            if _WORD_NAME_RE.fullmatch(n):
                self._name_patterns.append(
                    (n, re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.IGNORECASE))
                )
            else:
                self._name_patterns.append((n, None))
        self.reply_starters = tuple(reply_starters)

    def set_bot_identity(self, bot_id: str = "", bot_names: Iterable[str] | None = None) -> None:
        """运行时更新 Bot 身份（首次拿到 self_id 时调用）。"""
        if bot_id:
            self.bot_id = str(bot_id)
        if bot_names is not None:
            self.bot_names = tuple(str(n).strip().lower() for n in bot_names if str(n).strip())
            self._name_patterns = []
            for n in self.bot_names:
                if _WORD_NAME_RE.fullmatch(n):
                    self._name_patterns.append(
                        (n, re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.IGNORECASE))
                    )
                else:
                    self._name_patterns.append((n, None))

    # ---------------------------------------------------------------- 提取
    def extract(self, event: Any) -> SceneMessage:
        """从 AstrBot event 提取一条 SceneMessage（duck typing）。"""
        sid = str(event.get_sender_id() or "")
        name = str(event.get_sender_name() or sid)
        content = str(getattr(event, "message_str", "") or "").strip()

        msg = SceneMessage(
            sender_id=sid,
            sender_name=name,
            content=content[:300],
            timestamp=time.time(),
            is_bot=bool(sid and sid == self.bot_id),
        )

        # 消息组件：@ / 回复 / 图片
        try:
            comps = event.get_messages()
        except Exception:
            comps = []
        for comp in comps or []:
            ctype = type(comp).__name__
            if ctype == "At":
                qq = str(getattr(comp, "qq", "") or "")
                nm = str(getattr(comp, "name", "") or qq)
                target = qq or nm
                msg.at_targets.append(target)
                if qq and qq == self.bot_id:
                    msg.at_bot = True
                elif nm and self._is_bot_name(nm):
                    msg.at_bot = True
            elif ctype == "AtAll":
                msg.at_all = True
            elif ctype == "Reply":
                rid = getattr(comp, "sender_id", "") or getattr(comp, "qq", "")
                if rid:
                    msg.reply_to_id = str(rid)
            elif ctype == "Image":
                msg.has_image = True

        # 文本里直接出现 bot 名字也算「提及」
        if not msg.at_bot and self.bot_names:
            lower = content.lower()
            if any((pat.search(lower) if pat else (n and n in lower)) for n, pat in self._name_patterns):
                msg.at_bot = True  # 用 at_bot 统一承载「明确叫 Bot」

        # 文本中 @ 的其他目标（形如 @某人），简单提取供行为指导用
        for m in re.finditer(r"@([^\s@#，,。！？]{1,20})", content):
            token = m.group(1)
            if not self._is_bot_name(token):
                msg.at_targets.append(token)

        return msg

    def _is_bot_name(self, text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return False
        for n, pat in self._name_patterns:
            if pat:
                if pat.search(t):
                    return True
            elif n and n in t:
                return True
        return False

    # ---------------------------------------------------------------- 触发
    def infer_addressee(
        self,
        msg: SceneMessage,
        history: Iterable[SceneMessage],
    ) -> None:
        """推断 msg 是跟谁说的，就地写入 talking_to / talking_to_name。

        核心原则（继承并强化原插件）：宁可保守判为「群聊」，不可误判为
        「在跟 Bot 说」。只有明确 @ / 回复 Bot，或高置信的短回应，才判 bot。
        """
        hist = list(history)

        # 规则1：明确 @ Bot
        if msg.at_bot:
            msg.talking_to, msg.talking_to_name = "bot", "你"
            return

        # 规则2：明确 @ 了别人（不是 Bot）
        others = [t for t in msg.at_targets if not self._is_bot_name(t)]
        if others:
            msg.talking_to, msg.talking_to_name = others[0], others[0]
            return

        # 规则3：引用回复
        if msg.reply_to_id:
            if msg.reply_to_id == self.bot_id:
                msg.talking_to, msg.talking_to_name = "bot", "你"
            else:
                name = msg.reply_to_id
                for m in reversed(hist):
                    if m.sender_id == msg.reply_to_id:
                        name = m.sender_name
                        break
                msg.talking_to, msg.talking_to_name = msg.reply_to_id, name
            return

        if not hist:
            return  # 默认群聊

        # 找最近一条别人（非自己）的消息
        recent = [m for m in hist[-5:] if m.sender_id != msg.sender_id]
        if not recent:
            return
        last = recent[-1]
        gap = msg.timestamp - last.timestamp

        # 规则4：Bot 刚回复过当前用户，且是短确认/致谢 —— 中置信
        if last.is_bot and gap < 20:
            stripped = msg.content.strip()
            if stripped and len(stripped) <= 20 and self._looks_like_reply(stripped):
                # 若最近 60s 内有别人也在跟当前用户说话，则更可能是回那个人
                for m in reversed(hist):
                    if msg.timestamp - m.timestamp > 60:
                        break
                    if m.is_bot or m.sender_id == msg.sender_id:
                        continue
                    if m.talking_to == msg.sender_id:
                        msg.talking_to, msg.talking_to_name = m.sender_id, m.sender_name
                        return
                msg.talking_to, msg.talking_to_name = "bot", "你"
                return
            # Bot 刚说话但不是在回当前用户 —— 保持群聊
            return

        # 规则5：A→B→A，上一条明确对当前用户说，且上一条不是 Bot
        if last.talking_to == msg.sender_id and not last.is_bot and gap < 60:
            msg.talking_to, msg.talking_to_name = last.sender_id, last.sender_name
            return

        # 默认：群聊（保守）

    def _looks_like_reply(self, content: str) -> bool:
        s = content.strip()
        return any(s.startswith(k) for k in self.reply_starters)


class SceneGenerator:
    """把分析结果生成 <conversation_scene> 临时提示。"""

    @staticmethod
    def _esc(text: Any) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _describe_target(msg: SceneMessage) -> str:
        if msg.talking_to == "bot":
            return "你"
        if msg.talking_to == "group":
            return "群里所有人（非特定对象）"
        return msg.talking_to_name or msg.talking_to

    def generate(
        self,
        trigger_type: str,
        trigger_desc: str,
        current: SceneMessage,
        flow: Iterable[SceneMessage],
        *,
        show_flow: bool = True,
        summary: str = "",
        member_note: str = "",
    ) -> str:
        esc = self._esc
        parts = ["<conversation_scene>"]
        parts.append(f'  <trigger type="{esc(trigger_type)}">{esc(trigger_desc)}</trigger>')

        target = "你" if current.is_to_bot else self._describe_target(current)
        parts.append(
            "  <current_message>"
            f"\n    <sender>{esc(current.sender_name)}</sender>"
            f"\n    <talking_to>{esc(target)}</talking_to>"
            f"\n    <content>{esc(current.content[:80])}</content>"
            "\n  </current_message>"
        )

        instruction = self._instruction(trigger_type, current, current.is_to_bot,
                                        current.talking_to == "group")
        if instruction:
            parts.append(f"  <instruction>{instruction}</instruction>")

        # 短期总结：跨轮的约束/纠正/决定/待办，比原始流水更像「记得聊过什么」
        if summary:
            parts.append(f"  <short_summary>{esc(summary)}</short_summary>")

        # 当前发言人的印象：让 Bot 对不同人有不同回应
        if member_note:
            parts.append(f"  <member_impression>{esc(member_note)}</member_impression>")

        if show_flow:
            flow_lines: list[str] = []
            for m in list(flow)[-6:]:
                to = "你" if m.talking_to == "bot" else (
                    "群" if m.talking_to == "group" else (m.talking_to_name or m.talking_to)
                )
                sender = "[你]" if m.is_bot else m.sender_name
                content = m.content[:24] + ("…" if len(m.content) > 24 else "")
                if m.has_image and not content:
                    content = "[图片]"
                flow_lines.append(f"    <m>{esc(sender)} → {esc(to)}: {esc(content)}</m>")
            if flow_lines:
                parts.append("  <recent_flow>")
                parts.extend(flow_lines)
                parts.append("  </recent_flow>")

        parts.append("</conversation_scene>")
        return "\n".join(parts)

    @staticmethod
    def _instruction(
        trigger: str, msg: SceneMessage, is_to_bot: bool, is_to_group: bool
    ) -> str:
        """行为指导：解决「误以为在问自己」的关键。"""
        others = [t for t in msg.at_targets if t and t != "bot"]
        if trigger in (TRIGGER_AT, TRIGGER_REPLY, "at_all") and others:
            return (
                f"用户同时在叫你和 {others[0]}，你是被点名的对象之一。"
                "请正常回应，但别当成只针对你的单独提问。"
            )
        if trigger in (TRIGGER_AT, TRIGGER_REPLY, "at_all", TRIGGER_MENTION):
            return "用户在和你对话，请正常回应。"

        if trigger == TRIGGER_ACTIVE:
            if is_to_bot:
                return (
                    "【注意】你是主动加入对话的，没有人明确叫你。"
                    "上下文里对方像是回应你，但只是低置信推断。"
                    "除非内容明确需要你回应，否则保持沉默或只做极简短确认，不要展开长篇。"
                )
            if is_to_group:
                return (
                    "【重要】你是主动加入对话的，这条消息说给群里，不是在问你。"
                    "别当成向你提问。可以发表看法、补充信息，或保持沉默。"
                )
            return (
                f"【重要】你是主动加入对话的！{msg.sender_name} 正在和 "
                f"{msg.talking_to_name} 说话，不是在问你。以旁观者身份自然介入，或保持沉默。"
            )

        return (
            "【谨慎】触发原因不明确。仔细判断这条消息是不是对你说的；"
            "不确定就保持沉默，避免误入他人对话。"
        )


class SceneTracker:
    """按会话维护短期消息窗口，供场景分析使用（内存、有界）。"""

    def __init__(self, max_messages: int = 50) -> None:
        self._max = max(10, int(max_messages))
        self._sessions: dict[str, deque[SceneMessage]] = {}

    def window(self, session: str) -> deque[SceneMessage]:
        return self._sessions.setdefault(session, deque(maxlen=self._max))

    def append(self, session: str, msg: SceneMessage) -> None:
        self.window(session).append(msg)

    def recent(self, session: str, n: int = 6) -> list[SceneMessage]:
        return list(self.window(session))[-max(1, n):]

    def clear(self, session: str) -> None:
        self._sessions.pop(session, None)


# ---------------------------------------------------------------- 短期总结

SUMMARY_KEYS = ("constraints", "corrections", "decisions", "todos")
SUMMARY_LABELS = {
    "constraints": "偏好/约束",
    "corrections": "纠正",
    "decisions": "已定",
    "todos": "待办",
}


@dataclass
class SummaryNote:
    """一个会话的结构化短期笔记。"""

    at: float = 0.0
    constraints: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(getattr(self, k) for k in SUMMARY_KEYS)

    def render(self, max_items: int = 6, max_chars: int = 400) -> str:
        lines: list[str] = []
        for k in SUMMARY_KEYS:
            items = getattr(self, k) or []
            if not items:
                continue
            lines.append(f"{SUMMARY_LABELS[k]}：")
            for it in items[:max_items]:
                lines.append(f"- {it}")
        text = "\n".join(lines)
        return text[:max_chars]


class SummaryStore:
    """按会话存最近一版笔记（内存、有界），供场景提示注入。"""

    def __init__(self, max_sessions: int = 100) -> None:
        self._max = max(10, int(max_sessions))
        self._notes: dict[str, SummaryNote] = {}
        self._touched: dict[str, float] = {}

    def get(self, session: str) -> SummaryNote | None:
        return self._notes.get(session)

    def set(self, session: str, note: SummaryNote) -> None:
        self._notes[session] = note
        self._touched[session] = note.at or time.time()
        # 超限淘汰最久未更新的
        if len(self._notes) > self._max:
            old = sorted(self._touched, key=self._touched.get)[: len(self._notes) - self._max]
            for s in old:
                self._notes.pop(s, None)
                self._touched.pop(s, None)

    def clear(self, session: str) -> None:
        self._notes.pop(session, None)
        self._touched.pop(session, None)

    def count(self) -> int:
        return len(self._notes)


def note_from_data(data: dict | None, at: float = 0.0) -> SummaryNote:
    """把模型返回的 dict 清洗成 SummaryNote（只留字符串、去重、限量）。"""
    data = data if isinstance(data, dict) else {}
    note = SummaryNote(at=at or time.time())
    for k in SUMMARY_KEYS:
        raw = data.get(k) or []
        if isinstance(raw, str):
            raw = [raw]
        seen: set[str] = set()
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s[:120])
            if len(out) >= 8:
                break
        setattr(note, k, out)
    return note


# ---------------------------------------------------------------- 成员印象

@dataclass
class MemberProfile:
    """一个群成员的行为印象（纯统计，不涉隐私）。"""

    name: str = ""
    messages: int = 0
    to_bot: int = 0          # 对 Bot 说话的次数
    at_by_bot: int = 0       # 被 Bot 回复/接话的次数
    questions: int = 0       # 提问次数
    images: int = 0          # 发图次数
    short_msgs: int = 0      # 短句次数
    last_seen: float = 0.0

    def render(self) -> str:
        """给模型看的简短印象，只挑显著的线索，避免刻板印象。"""
        bits: list[str] = []
        if self.messages >= 5:
            if self.to_bot >= max(2, self.messages // 4):
                bits.append("常找你聊")
            elif self.to_bot == 0 and self.messages >= 10:
                bits.append("主要在跟别人聊，不太找你")
        if self.questions >= max(3, self.messages // 3):
            bits.append("爱提问")
        if self.images >= max(2, self.messages // 5):
            bits.append("爱发图")
        if self.messages >= 5 and self.short_msgs >= self.messages * 0.7:
            bits.append("说话简短")
        if self.at_by_bot >= 2:
            bits.append("你最近常接他的话")
        return "、".join(bits) if bits else ""


class MemberStore:
    """按群维护成员印象（内存、有界、可淘汰）。"""

    def __init__(self, max_members: int = 60, ttl_sec: float = 7 * 86400) -> None:
        self._max = max(10, int(max_members))
        self._ttl = ttl_sec
        # session -> {sender_id: MemberProfile}
        self._data: dict[str, dict[str, MemberProfile]] = {}
        self._touched: dict[str, float] = {}

    def observe(self, session: str, msg: SceneMessage) -> None:
        """观察一条消息，累积成员印象。"""
        if msg.is_bot or not msg.sender_id:
            return
        now = msg.timestamp or time.time()
        members = self._data.setdefault(session, {})
        p = members.setdefault(msg.sender_id, MemberProfile(name=msg.sender_name))
        p.name = msg.sender_name or p.name
        p.messages += 1
        p.last_seen = now
        if msg.talking_to == "bot" or msg.at_bot:
            p.to_bot += 1
        if "?" in msg.content or "？" in msg.content:
            p.questions += 1
        if msg.has_image:
            p.images += 1
        if 0 < len(msg.content) <= 8:
            p.short_msgs += 1
        self._touched[session] = now
        # 成员数上限：淘汰最久未出现的
        if len(members) > self._max:
            old = sorted(members, key=lambda k: members[k].last_seen)[: len(members) - self._max]
            for k in old:
                members.pop(k, None)

    def mark_bot_replied(self, session: str, sender_id: str) -> None:
        members = self._data.get(session)
        if members and sender_id in members:
            members[sender_id].at_by_bot += 1

    def profile(self, session: str, sender_id: str) -> MemberProfile | None:
        return (self._data.get(session) or {}).get(sender_id)

    def render(self, session: str, sender_id: str) -> str:
        p = self.profile(session, sender_id)
        if not p:
            return ""
        return p.render()

    def clear(self, session: str) -> None:
        self._data.pop(session, None)
        self._touched.pop(session, None)

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        for s in list(self._data):
            if now - self._touched.get(s, now) > self._ttl:
                self.clear(s)

    def sessions(self) -> int:
        return len(self._data)

    def members(self, session: str) -> dict[str, MemberProfile]:
        return self._data.get(session) or {}
