"""跟戳（凑热闹）：群里好几个人戳同一个人时，机器人按概率也戳一下。

真人看到群里开始互相戳，会顺手跟一脚。这里只做「跟随」，不主动挑事：

- 用滑动窗口统计**不同的人**戳同一个目标的人数，达到阈值（默认 2 人）才有资格；
- 达到之后每次又有新人加入时掷一次概率骰，命中就真发一次 poke；
- 同一目标有冷却（默认 10 分钟），避免一直逮着一个人戳；
- 戳的对象是群友、不是机器人自己（戳自己的走正常回应链路）；
- 黑名单用户、被要求闭嘴的群、以及机器人自己发起的戳都不跟；
- 协议端不支持 poke（没 ``call_action`` 或该实现没有 send_poke）时静默放弃。

零模型成本：纯内存计数 + 一次协议端调用。
"""

from __future__ import annotations

import random
import time
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 跟戳"

# 记忆上限：防止被拉进上千个群后表无限增长
_MAX_KEYS = 2000


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _cfg_num(cfg: Any, key: str, default: float, lo: float, hi: float) -> float:
    try:
        val = float(_cfg(cfg, key, default) or default)
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


class FollowPokeTracker:
    """按 (群, 目标) 维护「谁在什么时候戳过他」，决定要不要跟一下。"""

    def __init__(self) -> None:
        # key -> {poker_id: 最近一次戳的时间}
        self._pokers: dict[tuple[str, str], dict[str, float]] = {}
        # key -> 上次跟戳时间（冷却）
        self._last: dict[tuple[str, str], float] = {}

    def clear(self) -> None:
        self._pokers.clear()
        self._last.clear()

    def observe(
        self,
        group: str,
        target: str,
        poker: str,
        cfg: Any = None,
        *,
        now: float | None = None,
        rng: Any = None,
        enabled: bool | None = None,
        threshold: int | None = None,
        window: float | None = None,
        probability: float | None = None,
        cooldown: float | None = None,
    ) -> bool:
        """记录一次「别人戳了别人」；返回是否该由机器人跟一下。

        参数既可直接传（测试用），也可传 cfg 让函数自己读配置。
        """
        group = str(group or "")
        target = str(target or "")
        poker = str(poker or "")
        if not group or not target or not poker:
            return False
        if enabled is None:
            enabled = bool(_cfg(cfg, "戳一戳_跟戳", True))
        if not enabled:
            return False
        if threshold is None:
            threshold = int(_cfg_num(cfg, "戳一戳_跟戳人数", 2, 1, 20))
        if window is None:
            window = _cfg_num(cfg, "戳一戳_跟戳窗口秒", 60, 5, 3600)
        if probability is None:
            probability = _cfg_num(cfg, "戳一戳_跟戳概率", 0.3, 0.0, 1.0)
        if cooldown is None:
            cooldown = _cfg_num(cfg, "戳一戳_跟戳冷却秒", 600, 0, 86400)

        now = float(now if now is not None else time.time())
        key = (group, target)
        bucket = self._pokers.setdefault(key, {})
        # 只保留窗口内的人：过期的 poke 不计入「几个人」
        cutoff = now - float(window)
        for pid in [p for p, ts in bucket.items() if ts < cutoff]:
            bucket.pop(pid, None)
        bucket[poker] = now

        if len(bucket) < max(1, int(threshold)):
            return False
        # 冷却只对「真的跟过戳」的目标生效：没戳过时 last 不存在，
        # 不能拿 0 当上次时间（否则小时间戳下会一直判成冷却中）
        last = self._last.get(key)
        if last is not None and now - last < float(cooldown):
            return False
        dice = rng.random() if rng is not None else random.random()
        if dice > float(probability):
            return False
        self._last[key] = now
        # 命中后清空该目标的计数：下一次需要重新攒人，避免每来一个人就戳一下
        self._pokers.pop(key, None)
        self._prune(now, window)
        return True

    def _prune(self, now: float, window: float) -> None:
        if len(self._pokers) + len(self._last) <= _MAX_KEYS:
            return
        cutoff = now - max(window * 2, 600.0)
        for key in [k for k, bucket in self._pokers.items()
                    if not bucket or max(bucket.values()) < cutoff]:
            self._pokers.pop(key, None)
        for key in [k for k, ts in self._last.items() if ts < cutoff]:
            self._last.pop(key, None)


def _resolve_api(event: Any) -> Any:
    """取协议端上能 call_action 的对象（aiocqhttp: bot.api / bot._api）。"""
    try:
        from .bot_echo import BotSendTap

        api = BotSendTap._resolve_target(getattr(event, "bot", None))
        if api is not None and hasattr(api, "call_action"):
            return api
    except Exception:  # noqa: BLE001
        pass
    bot = getattr(event, "bot", None)
    return bot if hasattr(bot, "call_action") else None


async def send_poke(event: Any, group: str, target: str) -> bool:
    """真发一次戳；协议端不支持就返回 False（不抛）。"""
    api = _resolve_api(event)
    if api is None:
        return False
    payload: dict[str, Any] = {"user_id": target}
    if group:
        payload["group_id"] = group
    # 不同 OneBot 实现动作名不一样：标准 send_poke，部分实现用 group_poke
    for action in ("send_poke", "group_poke"):
        try:
            await api.call_action(action, **payload)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | %s 失败: %s", _LOG, action, e)
    return False


async def maybe_follow_poke(plugin: Any, event: Any, group: str, target: str, poker: str) -> bool:
    """跟戳入口：黑名单/闭嘴/自身戳 都跳过；返回是否真戳了。"""
    if not group or not target or not poker:
        return False
    # 机器人自己的戳不跟（否则会被自己的通知再触发一次）
    try:
        if str(event.get_self_id() or "") == target:
            return False
    except Exception:  # noqa: BLE001
        pass
    # 被要求闭嘴的群，连戳都别凑
    try:
        if plugin.group_h._is_muted(group):
            return False
    except Exception:  # noqa: BLE001
        pass
    # 黑名单用户不跟
    try:
        if plugin._module_on("黑名单") and str(target) in plugin.blacklist.ids():
            return False
    except Exception:  # noqa: BLE001
        pass

    tracker = getattr(plugin, "_follow_poke", None)
    if tracker is None:
        tracker = FollowPokeTracker()
        plugin._follow_poke = tracker
    if not tracker.observe(group, target, poker, plugin.cfg):
        return False
    ok = await send_poke(event, group, target)
    if ok:
        try:
            plugin.log_activity("poke", f"跟戳 {target}", group)
        except Exception:  # noqa: BLE001
            pass
        logger.debug("%s | 跟戳成功 group=%s target=%s", _LOG, group, target)
    return ok
