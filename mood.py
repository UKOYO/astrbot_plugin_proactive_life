"""跨轮情绪状态：让开口有连续性，而不是每次从零开始。

纯规则、无模型调用、无磁盘写；按 key（私聊 uid / 群 origin）存，内存有界，
情绪强度随时间自然衰减回「平静」。

设计原则：
- 情绪只影响「怎么说」，不决定「说不说」——是否开口仍由概率/冷却决定，
  避免情绪把调度逻辑搅乱。
- 强度有上限、会衰减，避免一次事件永久改变人格。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# ---- 情绪名 ----
CALM = "平静"
HAPPY = "开心"
EXCITED = "兴致高"
DOWN = "闷闷的"
WRONGED = "有点委屈"
BORED = "无聊"

# 每个情绪给出「强 / 弱」两档提示词，按强度选用
MOOD_HINTS: dict[str, tuple[str, str]] = {
    CALM: ("", ""),
    HAPPY: (
        "你现在心情不错，语气轻快，愿意多聊两句。",
        "你心情还算可以，语气自然点就行。",
    ),
    EXCITED: (
        "你现在兴致很高，有点想分享刚碰到的东西，话可以多一点。",
        "你现在有点兴趣，顺口提一句也行。",
    ),
    DOWN: (
        "你现在情绪有点低，话少、语气淡，不想硬撑热闹。",
        "你现在不太提得起劲，回得简短些。",
    ),
    WRONGED: (
        "你心里还有点小情绪（被冷落/被打断），说话带点别扭，别装作没事。",
        "你有点在意刚才被晾着，语气稍微收一点。",
    ),
    BORED: (
        "你现在有点无聊，想找点话说，但别显得刻意。",
        "你现在闲着，可以主动递个话头。",
    ),
}

# 事件 -> (情绪, 基础强度)
EVENT_MOOD: dict[str, tuple[str, float]] = {
    "replied": (HAPPY, 0.60),     # 对方回了 / 接话被回应
    "chat_ok": (HAPPY, 0.45),     # 顺利聊完一轮
    "ignored": (WRONGED, 0.60),   # 追问没回 / 被晾着
    "muted": (DOWN, 0.70),        # 被喊闭嘴 / 被嫌烦
    "snubbed": (DOWN, 0.50),      # 接话被打断或无视
    "explored": (EXCITED, 0.65),  # 摸鱼摸到有意思的东西
    "idle_long": (BORED, 0.50),   # 长时间没有互动
}

INTENSITY_CAP = 0.9
INTENSITY_FLOOR = 0.15


@dataclass
class MoodState:
    name: str = CALM
    intensity: float = 0.0
    updated: float = 0.0
    reason: str = ""

    def hint(self, strong_at: float = 0.55) -> str:
        """返回注入 prompt 的情绪提示；平静或强度过低返回空串。"""
        if self.name == CALM or self.intensity < INTENSITY_FLOOR:
            return ""
        pair = MOOD_HINTS.get(self.name)
        if not pair:
            return ""
        strong, mild = pair
        return strong if self.intensity >= strong_at else mild


class MoodStore:
    """按 key 维护当前情绪（内存、有界、随时间衰减）。"""

    def __init__(self, max_keys: int = 120, decay_hours: float = 3.0) -> None:
        self._max = max(10, int(max_keys))
        self._decay = max(0.1, float(decay_hours))
        self._states: dict[str, MoodState] = {}

    # ---- 内部：衰减 ----
    def _decayed(self, key: str, now: float) -> MoodState:
        st = self._states.get(key)
        if st is None:
            return MoodState()
        if not st.updated:
            return st
        elapsed_h = (now - st.updated) / 3600.0
        if elapsed_h <= 0:
            return st
        factor = max(0.0, 1.0 - elapsed_h / self._decay)
        eff = st.intensity * factor
        if eff < INTENSITY_FLOOR:
            return MoodState()
        return MoodState(
            name=st.name, intensity=eff, updated=st.updated, reason=st.reason
        )

    # ---- 对外 ----
    def get(self, key: str, now: float | None = None) -> MoodState:
        return self._decayed(str(key or ""), now or time.time())

    def apply(
        self, key: str, event: str, reason: str = "", now: float | None = None
    ) -> MoodState:
        """按事件更新情绪；未知事件忽略。"""
        key = str(key or "")
        if not key:
            return MoodState()
        spec = EVENT_MOOD.get(str(event or ""))
        if spec is None:
            return self.get(key, now)
        name, base = spec
        now = now or time.time()
        cur = self._decayed(key, now)
        if cur.name == name:
            # 同一情绪叠加，封顶
            intensity = min(INTENSITY_CAP, max(x for x in (cur.intensity, base)) + 0.1)
        else:
            # 换情绪时直接按新事件的强度，不叠加旧情绪，避免一次事件被放大
            intensity = min(INTENSITY_CAP, base)
        st = MoodState(
            name=name, intensity=round(intensity, 3), updated=now, reason=str(reason or "")[:40]
        )
        self._states[key] = st
        self._prune(now)
        return st

    def hint(self, key: str, now: float | None = None) -> str:
        return self.get(key, now).hint()

    def snapshot(self, key: str, now: float | None = None) -> dict:
        st = self.get(key, now)
        return {
            "name": st.name,
            "intensity": round(st.intensity, 2),
            "reason": st.reason,
        }

    def clear(self, key: str) -> None:
        self._states.pop(str(key or ""), None)

    def count(self) -> int:
        return len(self._states)

    def _prune(self, now: float) -> None:
        if len(self._states) <= self._max:
            return
        # 先清掉已衰减到平静的
        for k in [k for k in self._states if not self._decayed(k, now).intensity]:
            self._states.pop(k, None)
        if len(self._states) <= self._max:
            return
        # 再按最后更新时间淘汰最旧的
        old = sorted(self._states, key=lambda k: self._states[k].updated)
        for k in old[: len(self._states) - self._max]:
            self._states.pop(k, None)
