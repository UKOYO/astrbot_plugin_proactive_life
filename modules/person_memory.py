"""跨会话「人」档案：让同一个人在群聊和私聊里被当成同一个人记住。

为什么不直接共享对话历史：
- token 爆炸：两边历史都塞进去，等于双倍上下文；
- 串味：群里的梗、私聊里的私密话，互相泄露一眼假。

所以只共享**提炼后的结论**（每人 ≤ 2 行），不共享原文。
分层规则：
  公开字段（称呼、兴趣、最近状态）  群聊和私聊都能用
  私密字段（关系温度、欠着的事）    只有私聊注入

写入全部走规则（零 LLM 调用），避免额外 token 与延迟。
"""

from __future__ import annotations

import re
import time
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 人物档案"
_MAX_TEXT = 60          # 单条摘要最长字数
_MAX_INTERESTS = 3
_PROFILE_TTL_DAYS = 90

_INTEREST_HINTS = (
    ("游戏", ("游戏", "开黑", "上分", "副本", "steam", "switch", "老头环")),
    ("番剧", ("番", "动画", "新番", "漫画", "galgame", "视觉小说")),
    ("吃", ("吃", "外卖", "火锅", "烧烤", "奶茶", "做饭")),
    ("工作", ("上班", "加班", "开会", "打工", "上班摸鱼", "项目")),
    ("学习", ("考试", "复习", "作业", "论文", "背单词")),
    ("运动", ("跑步", "健身", "打球", "游泳", "骑车")),
)

_QUESTION_TAIL = re.compile(r"[？?]\s*$")


def _clean(text: str, limit: int = _MAX_TEXT) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t[:limit]


class PersonProfile:
    """一个联系人的极简档案。"""

    __slots__ = ("uid", "name", "aliases", "interests", "last_topic",
                 "prev_topic", "pending", "warmth", "updated", "seen_groups")

    def __init__(self, uid: str = "") -> None:
        self.uid = str(uid or "")
        self.name = ""
        self.aliases: list[str] = []
        self.interests: list[str] = []
        self.last_topic = ""       # 最近聊的事（公开）
        self.prev_topic = ""       # 上一轮聊的事（用于避免同轮把当前消息说成「上次」）
        self.pending = ""          # 欠着没答/没做完的事（私密）
        self.warmth = 0.5          # 关系温度 0~1
        self.updated = 0.0
        self.seen_groups: list[str] = []

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "name": self.name, "aliases": self.aliases,
            "interests": self.interests, "last_topic": self.last_topic,
            "prev_topic": self.prev_topic,
            "pending": self.pending, "warmth": self.warmth,
            "updated": self.updated, "seen_groups": self.seen_groups,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonProfile":
        p = cls(str(data.get("uid") or ""))
        p.name = str(data.get("name") or "")[:24]
        p.aliases = [str(a)[:24] for a in (data.get("aliases") or [])][:5]
        p.interests = [str(a)[:8] for a in (data.get("interests") or [])][:_MAX_INTERESTS]
        p.last_topic = str(data.get("last_topic") or "")[:_MAX_TEXT]
        p.prev_topic = str(data.get("prev_topic") or "")[:_MAX_TEXT]
        p.pending = str(data.get("pending") or "")[:_MAX_TEXT]
        try:
            p.warmth = max(0.0, min(1.0, float(data.get("warmth", 0.5))))
        except (TypeError, ValueError):
            p.warmth = 0.5
        try:
            p.updated = float(data.get("updated") or 0)
        except (TypeError, ValueError):
            p.updated = 0.0
        p.seen_groups = [str(g)[:64] for g in (data.get("seen_groups") or [])][:20]
        return p


class PersonMemory:
    """按 uid 维护联系人档案；群聊/私聊共用同一份，注入时按可见域裁剪。"""

    def __init__(self, path=None, max_people: int = 300) -> None:
        self._path = path
        self._max = max(20, int(max_people))
        self._people: dict[str, PersonProfile] = {}
        if path is not None:
            self.load()

    # ---- 持久化 ----
    def load(self) -> None:
        if self._path is None:
            return
        try:
            from ..safe_io import sync_read_json

            raw = sync_read_json(self._path, {}, log_prefix="proactive_life | 人物档案读取失败")
            if not isinstance(raw, dict):
                return
            for uid, item in raw.items():
                if isinstance(item, dict):
                    self._people[str(uid)] = PersonProfile.from_dict(item)
            self._prune()
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 读取失败，按空档案启动: %s", _LOG, e)

    def save(self) -> None:
        if self._path is None:
            return
        try:
            from ..safe_io import sync_write_json

            sync_write_json(
                self._path,
                {uid: p.to_dict() for uid, p in self._people.items()},
                log_prefix="proactive_life | 人物档案落盘失败",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 落盘失败: %s", _LOG, e)

    # ---- 写入（纯规则，不调 LLM）----
    def touch(
        self,
        uid: str,
        text: str = "",
        *,
        name: str = "",
        group: str = "",
        scope: str = "group",
    ) -> PersonProfile | None:
        """收到一条消息时更新档案。scope: private / group。"""
        uid = str(uid or "").strip()
        if not uid:
            return None
        now = time.time()
        p = self._people.get(uid)
        if p is None:
            p = PersonProfile(uid)
            self._people[uid] = p
        prev_updated = p.updated
        p.updated = now

        if name and not p.name:
            p.name = _clean(name, 24)
        if group:
            g = str(group)[:64]
            if g not in p.seen_groups:
                p.seen_groups.append(g)
                del p.seen_groups[:-20]

        body = _clean(text)
        if body:
            if p.last_topic and body != p.last_topic:
                p.prev_topic = p.last_topic
            p.last_topic = body
            for label, keys in _INTEREST_HINTS:
                if any(k in body for k in keys) and label not in p.interests:
                    p.interests.append(label)
                    del p.interests[:-_MAX_INTERESTS]
                    break
            # 问句且没答完 → 记为欠着的事（只在私聊里用）
            if scope == "private" and _QUESTION_TAIL.search(str(text or "").strip()):
                p.pending = body

        # 关系温度：长期不聊先降温，再按私聊互动升温（用旧时间戳判断，修复死代码）
        if prev_updated > 0 and now - prev_updated > 7 * 86400:
            p.warmth = max(0.0, p.warmth - 0.1)
        if scope == "private":
            p.warmth = min(1.0, p.warmth + 0.05)

        self._prune()
        return p

    def resolve_pending(self, uid: str) -> None:
        """对方回了消息/事情办完了，清掉「欠着的事」。"""
        p = self._people.get(str(uid or ""))
        if p is not None and p.pending:
            p.pending = ""

    def _prune(self) -> None:
        if len(self._people) <= self._max:
            return
        # 淘汰最久没更新的
        ordered = sorted(self._people.values(), key=lambda x: x.updated)
        for p in ordered[: len(self._people) - self._max]:
            self._people.pop(p.uid, None)

    # ---- 读取 ----
    def get(self, uid: str) -> PersonProfile | None:
        return self._people.get(str(uid or ""))

    def block(
        self,
        uid: str,
        *,
        scope: str = "group",
        name: str = "",
        current_text: str = "",
        display_name: str = "",
    ) -> str:
        """生成给 prompt 用的 1~2 行摘要；没有档案或有串味风险时返回空串。

        ``display_name``：私聊时传配置里的「对方称呼」（如「优可」）。给了就以它
        标识对方并附上 ``UID:xxx`` —— 只塞裸 QQ 昵称时，`/new` 清空记忆后模型
        会把恋人当成陌生网友怼回去。
        """
        p = self.get(uid)
        if p is None:
            return ""
        lines: list[str] = []
        who = p.name or _clean(name, 24)
        if scope == "private":
            label = _clean(display_name, 24) or who
            if label and uid:
                who = f"{label}（UID:{uid}）"
            elif uid:
                who = f"UID:{uid}"

        bits: list[str] = []
        if p.interests:
            bits.append("平时爱聊" + "、".join(p.interests))
        if scope == "private":
            # 若本轮 on_private_message 刚把当前句写进 last_topic，退回展示 prev_topic，
            # 避免把用户刚发的这句话错报成「上次说到」。
            cur_clean = _clean(current_text) if current_text else ""
            topic = p.prev_topic if (cur_clean and p.last_topic == cur_clean) else p.last_topic
            if topic:
                bits.append(f"上次说到「{topic[:24]}」")
        if scope == "private":
            if p.pending:
                bits.append(f"还欠着他「{p.pending[:24]}」")
            if p.warmth >= 0.7:
                bits.append("跟他已经很熟了，说话可以随意些")

        if bits:
            head = f"- 关于{who}： " if who else "- 关于这个人： "
            lines.append(head + "；".join(bits))
        elif who:
            # 只知道称呼时给一条最省的信息（称呼本身是公开的，不串味）
            lines.append(f"- 这个人叫{who}")
        return "\n".join(lines)
