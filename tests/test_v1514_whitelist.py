"""v1.5.14 验收：白名单概率与默认概率分离 + 不开口的群不进日志。

核心诉求：
1. 白名单群有**自己的**破冰/插话概率，不再乘默认概率——所以「默认概率填 0、
   只让白名单群开口」这种配置能真正生效；
2. 不会主动开口的群不建上下文缓存、不攒短期总结、不写诊断日志、不进面板
   群列表，避免把真正会开口的群从有界日志里刷掉。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.group_handler import GroupProactiveHandler  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402

GID = "123456"


class _Conf:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, patch):
        self._data.update(patch)


def _cfg(**overrides) -> _LiveConfig:
    return _LiveConfig(SimpleNamespace(_conf=_Conf(overrides)), DEFAULT_CONFIG)


def _host(**overrides) -> GroupProactiveHandler:
    """只装够用的 handler：不读盘、不建后台任务。"""
    import time

    host = object.__new__(GroupProactiveHandler)
    host.cfg = _cfg(**overrides)
    host.motive_mgr = None
    host._active_stamp = {}
    host._user_spoke_after = {}
    host._miss_streak = {}
    host._recent_bot_spoke = {}
    host._diag_stamp = {}
    host._muted_until = {}
    host._mute_judge_at = {}
    host._last_spoke_stamp = {}
    host._last_roll_stamp = {}
    host._keyword_last_spoke = {}
    host.active_reply_stacks = {}
    host.model_choice_histories = {}
    host._last_scene = {}
    # 跳过 30 分钟一次的冷群清理（那条路径要全套缓存表，与本用例无关）
    host._last_prune = time.time()
    host._bg_tasks = set()
    host.recent_context_cache = {}
    host._scene = SimpleNamespace(recent=lambda *a, **k: [], append=lambda *a, **k: None)
    host._members = SimpleNamespace(observe=lambda *a, **k: None, prune=lambda *a, **k: None)
    host._summary_pending = {}
    host._summary_last_at = {}
    return host


def _event(gid: str = GID, text: str = "在吗"):
    return SimpleNamespace(
        unified_msg_origin=f"aiocqhttp:GroupMessage:{gid}",
        message_str=text,
        get_group_id=lambda: gid,
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "阿岚",
        get_self_id=lambda: "999",
        get_messages=lambda: [],
    )


# ---------------------------------------------------------------- 概率分离


def test_whitelist_has_own_icebreak_probability():
    """默认破冰概率 0 + 白名单 0.6：白名单群必须还能破冰（旧实现会乘成 0）。"""
    host = _host(
        群聊白名单=[GID],
        群聊自发破冰概率=0.0,
        群聊白名单_破冰概率=0.6,
    )
    assert host._icebreak_prob_for(GID) == 0.6
    assert host._icebreak_prob_for("999999") == 0.0


def test_whitelist_has_own_chitchat_probability():
    host = _host(
        群聊白名单=[f"aiocqhttp:GroupMessage:{GID}"],
        群聊插话_概率=0.0,
        群聊白名单_插话概率=0.15,
    )
    assert host._chitchat_prob_for(GID) == 0.15
    assert host._chitchat_prob_for("999999") == 0.0


def test_partition_override_beats_whitelist():
    host = _host(
        群聊白名单=[GID],
        群聊白名单_破冰概率=0.6,
        群聊白名单_插话概率=0.15,
        群聊独立分区配置=[{"group_id": GID, "icebreak_prob": 0.9, "chitchat_prob": 0.4}],
    )
    assert host._icebreak_prob_for(GID) == 0.9
    assert host._chitchat_prob_for(GID) == 0.4


def test_default_group_probability_still_used():
    host = _host(群聊自发破冰概率=0.2, 群聊插话_概率=0.07)
    assert host._icebreak_prob_for("888888") == 0.2
    assert host._chitchat_prob_for("888888") == 0.07


# ---------------------------------------------------------------- 开口资格


def test_quiet_group_is_not_engaged():
    """默认配置（插话关、无白名单、无命中词）：任何群都不主动开口。"""
    host = _host()
    assert host._group_engaged(GID) is False


def test_whitelisted_group_is_engaged():
    host = _host(群聊白名单=[GID], 群聊白名单_破冰概率=0.6)
    assert host._group_engaged(GID) is True


def test_whitelisted_but_zero_probability_is_not_engaged():
    host = _host(
        群聊白名单=[GID],
        群聊自发破冰概率=0.0,
        群聊白名单_破冰概率=0.0,
        群聊插话_概率=0.0,
        群聊白名单_插话概率=0.0,
    )
    assert host._group_engaged(GID) is False


def test_chitchat_enabled_makes_group_engaged():
    host = _host(群聊插话_启用=True, 群聊插话_概率=0.05)
    assert host._group_engaged(GID) is True


def test_keyword_hit_makes_group_engaged():
    host = _host(群聊命中词_关键词=["唯笑"])
    assert host._group_engaged(GID) is True


# ---------------------------------------------------------------- 不进日志


def test_quiet_group_skips_context_tracking():
    host = _host(模块_上下文=False)
    host.on_group_message_received(_event())
    assert host._active_stamp == {}
    assert host.recent_context_cache == {}
    assert host._miss_streak == {}


def test_engaged_group_still_tracks_context():
    host = _host(模块_上下文=False, 群聊白名单=[GID], 群聊白名单_破冰概率=0.6)
    host.on_group_message_received(_event(text="在吗"))
    assert f"aiocqhttp:GroupMessage:{GID}" in host._active_stamp
    assert host.recent_context_cache[f"aiocqhttp:GroupMessage:{GID}"]


def test_diag_silent_for_quiet_group():
    host = _host()
    host._diag(f"aiocqhttp:GroupMessage:{GID}", "掷骰未中")
    assert host._diag_stamp == {}

    engaged = _host(群聊白名单=[GID], 群聊白名单_破冰概率=0.6)
    engaged._diag(f"aiocqhttp:GroupMessage:{GID}", "掷骰未中")
    assert engaged._diag_stamp


def test_idle_check_skips_zero_probability_without_registering():
    """白名单概率填 0 的群连活跃时间戳都不登记（不进面板群列表）。"""
    host = _host(
        群聊白名单=[GID],
        群聊自发破冰概率=0.0,
        群聊白名单_破冰概率=0.0,
    )
    assert asyncio.run(host.check_idle_groups()) is False
    assert host._active_stamp == {}
