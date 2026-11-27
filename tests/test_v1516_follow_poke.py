"""v1.5.16 验收：跟戳（群里多人戳同一人时凑一脚）。

覆盖：
1. 人数阈值：不同的人达到阈值才有资格，同一个人戳多次只算一人；
2. 窗口过期：超出窗口的戳不计入人数；
3. 概率与冷却：概率未中不戳，戳过后同目标进入冷却；
4. 开关/配置读取；
5. `parse_poke` 返回 target，`is_poke_event` 仍只认「戳机器人」；
6. 端到端：别人互相戳 → 真发 poke；戳机器人 → 走正常回应链路，不跟戳；
7. 协议端动作名兼容（send_poke → group_poke 回退）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules import follow_poke  # noqa: E402
from astrbot_plugin_proactive_life.modules.follow_poke import (  # noqa: E402
    FollowPokeTracker,
    send_poke,
)
from astrbot_plugin_proactive_life.modules.interact_context import InteractContext  # noqa: E402
from astrbot_plugin_proactive_life.modules.interact_handler import handle_poke_event  # noqa: E402
from astrbot_plugin_proactive_life.modules.poke import is_poke_event, parse_poke  # noqa: E402

GROUP = "123456"
BOT = "999"


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


def _raw(target: str, user: str, group: str | None = GROUP) -> dict:
    raw = {
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "self_id": int(BOT), "target_id": int(target), "user_id": int(user),
    }
    # 私聊 poke 的原始报文里没有 group_id
    if group:
        raw["group_id"] = int(group)
    return raw


class _Api:
    def __init__(self, fail_actions=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = set(fail_actions)

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action in self.fail:
            raise RuntimeError(f"{action} unsupported")
        return {"ok": True}


class _PokeEvent:
    def __init__(self, raw, *, private=False, group=GROUP, api=None):
        self.extras: dict = {}
        self.message_obj = SimpleNamespace(raw_message=raw)
        self.message_str = ""
        self._private = private
        self._group = group
        self.bot = SimpleNamespace(api=api or _Api())
        self.unified_msg_origin = (
            f"aiocqhttp:FriendMessage:10001" if private else f"aiocqhttp:GroupMessage:{group}"
        )

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return dict(self.extras)
        return self.extras.get(key, default)

    def get_self_id(self):
        return BOT

    def get_sender_id(self):
        return str(self.message_obj.raw_message.get("user_id") or "10001")

    def get_sender_name(self):
        return "阿岚"

    def get_group_id(self):
        return self._group

    def get_messages(self):
        return []

    def is_private_chat(self):
        return self._private

    def is_admin(self):
        return False

    def request_llm(self, prompt=""):
        self.requested_prompt = prompt
        return SimpleNamespace(prompt=prompt, image_urls=[])

    def stop_event(self):
        self.stopped = True


def _plugin(**overrides):
    plugin = SimpleNamespace(
        cfg=_cfg(**overrides),
        mood=SimpleNamespace(apply=lambda *a, **k: None),
        interact_ctx=InteractContext(window=6, ttl=20.0, self_ttl=90.0),
        blacklist=SimpleNamespace(
            ids=lambda: frozenset(), is_blacklisted_event=lambda ev: False
        ),
        group_h=SimpleNamespace(_bot_name=lambda: "唯笑", _is_muted=lambda gid: False),
        log_activity=lambda *a, **k: None,
    )
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    plugin._ensure_echo_tap = lambda event=None: None
    plugin._stop_event = asyncio.Event()
    plugin._follow_poke = FollowPokeTracker()
    return plugin


# ---------------------------------------------------------------- 计数器


def _rng(value: float):
    return SimpleNamespace(random=lambda: value)


def test_requires_multiple_distinct_pokers():
    tracker = FollowPokeTracker()
    # 同一个人连戳两次：只算一人，达不到阈值 2
    assert tracker.observe(GROUP, "b", "u1", None, now=100.0, rng=_rng(0.0), threshold=2, window=60) is False
    assert tracker.observe(GROUP, "b", "u1", None, now=101.0, rng=_rng(0.0), threshold=2, window=60) is False
    # 第二个人加入才够
    assert tracker.observe(GROUP, "b", "u2", None, now=102.0, rng=_rng(0.0), threshold=2, window=60) is True


def test_window_expiry_drops_old_pokers():
    tracker = FollowPokeTracker()
    tracker.observe(GROUP, "b", "u1", None, now=100.0, rng=_rng(0.0), threshold=2, window=60)
    # u1 的记录已过期：只剩 u2 一人，不够阈值
    assert tracker.observe(GROUP, "b", "u2", None, now=200.0, rng=_rng(0.0), threshold=2, window=60) is False


def test_probability_and_cooldown():
    tracker = FollowPokeTracker()
    tracker.observe(GROUP, "b", "u1", None, now=1.0, rng=_rng(0.0), threshold=2, window=60)
    # 概率没中：不戳，且不影响后续
    assert tracker.observe(GROUP, "b", "u2", None, now=2.0, rng=_rng(0.9), threshold=2,
                           window=60, probability=0.3) is False
    assert tracker.observe(GROUP, "b", "u3", None, now=3.0, rng=_rng(0.0), threshold=2,
                           window=60, probability=0.3, cooldown=0) is True
    # 冷却期内再来一轮也不戳
    tracker.observe(GROUP, "b", "u1", None, now=4.0, rng=_rng(0.0), threshold=2, window=60)
    assert tracker.observe(GROUP, "b", "u2", None, now=5.0, rng=_rng(0.0), threshold=2,
                           window=60, probability=1.0, cooldown=600) is False


def test_switch_and_config_defaults():
    tracker = FollowPokeTracker()
    assert tracker.observe(GROUP, "b", "u1", _cfg(戳一戳_跟戳=False), now=1.0, rng=_rng(0.0)) is False
    # 默认阈值 2、概率 0.3：rng 0.0 必中
    tracker.observe(GROUP, "b", "u1", _cfg(), now=1.0, rng=_rng(0.0))
    assert tracker.observe(GROUP, "b", "u2", _cfg(), now=2.0, rng=_rng(0.0)) is True


# ---------------------------------------------------------------- 解析


def test_parse_poke_returns_target():
    ok, bot_id, sender, target, group = parse_poke(_PokeEvent(_raw("888", "10001")))
    assert (ok, bot_id, sender, target, group) == (True, BOT, "10001", "888", GROUP)
    # 戳机器人：is_poke_event 仍为真
    hit, bot_id2, sender2, group2 = is_poke_event(_PokeEvent(_raw(BOT, "10001")))
    assert hit is True and sender2 == "10001"
    # 戳别人：is_poke_event 为假（老语义不变）
    hit2, *_ = is_poke_event(_PokeEvent(_raw("888", "10001")))
    assert hit2 is False


# ---------------------------------------------------------------- 端到端


def test_follow_poke_sends_poke():
    plugin = _plugin(戳一戳_跟戳=True, 戳一戳_跟戳概率=1.0, 戳一戳_跟戳人数=2)
    api = _Api()
    # 第一个人戳 888
    asyncio.run(list_handle(handle_poke_event(plugin, _PokeEvent(_raw("888", "10001"), api=api))))
    assert api.calls == []
    # 第二个人接着戳 888 → 凑够人数，机器人跟一下
    asyncio.run(list_handle(handle_poke_event(plugin, _PokeEvent(_raw("888", "10002"), api=api))))
    assert len(api.calls) == 1
    action, params = api.calls[0]
    assert action == "send_poke"
    assert params["user_id"] == "888" and params["group_id"] == GROUP


def test_poke_to_bot_still_replies_not_follows():
    plugin = _plugin(戳一戳_跟戳=True, 戳一戳_跟戳概率=1.0, 戳一戳_回应概率=1.0,
                     戳一戳_个人冷却秒=0, 戳一戳_群冷却秒=0, 模块_上下文=False)
    api = _Api()
    event = _PokeEvent(_raw(BOT, "10001", group=None), private=True, group="", api=api)
    results = asyncio.run(list_handle(handle_poke_event(plugin, event)))
    assert len(results) == 1  # 正常回应
    assert api.calls == []    # 没有跟戳


def test_follow_poke_respects_mute_and_blacklist():
    plugin = _plugin(戳一戳_跟戳=True, 戳一戳_跟戳概率=1.0, 戳一戳_跟戳人数=2)
    plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑", _is_muted=lambda gid: True)
    api = _Api()
    for poker in ("10001", "10002"):
        asyncio.run(list_handle(handle_poke_event(plugin, _PokeEvent(_raw("888", poker), api=api))))
    assert api.calls == []

    plugin2 = _plugin(戳一戳_跟戳=True, 戳一戳_跟戳概率=1.0, 戳一戳_跟戳人数=2,
                      模块_黑名单=True)
    plugin2.blacklist = SimpleNamespace(ids=lambda: frozenset({"888"}))
    api2 = _Api()
    for poker in ("10001", "10002"):
        asyncio.run(list_handle(handle_poke_event(plugin2, _PokeEvent(_raw("888", poker), api=api2))))
    assert api2.calls == []


def test_send_poke_falls_back_to_group_poke():
    api = _Api(fail_actions=("send_poke",))
    event = _PokeEvent(_raw("888", "10001"), api=api)
    assert asyncio.run(send_poke(event, GROUP, "888")) is True
    assert [c[0] for c in api.calls] == ["send_poke", "group_poke"]


async def list_handle(gen):
    return [r async for r in gen]
