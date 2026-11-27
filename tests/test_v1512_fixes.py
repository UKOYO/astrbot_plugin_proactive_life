"""v1.5.12 验收测试：8 个实锤 Bug + 拟人化改造的回归护栏。

覆盖方案里的验收清单：
1. 节奏拆成 3 段且问句在第 3 段时，on_after_message_sent 仍能注册私聊追问；
2. 群闭嘴（裸群号 Key）下跟读不复读，被动注入能读到该群情绪；
3. 私聊被戳会回复；群纯 @ 埋 pending 时截断事件（不触发原生双回复）；
4. 日程_被动按需注入：闲聊不带日程/穿搭，问到才给；
5. panel_status 的 gap_left 与「最短间隔 * 60」一致，preset_apply 全部键有效。
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# 插件目录本身不是包，测试统一把 data/plugins 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.core.message.components import At, Plain  # noqa: E402

from astrbot_plugin_proactive_life import life_inject, panel_actions, panel_status, rhythm_mixin
from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG
from astrbot_plugin_proactive_life.handler_base import HandlerContextMixin
from astrbot_plugin_proactive_life.main import ProactiveLifePlugin, _LiveConfig
from astrbot_plugin_proactive_life.modules.interact_context import InteractContext
from astrbot_plugin_proactive_life.modules.interact_handler import (
    handle_group_reread,
    handle_poke_event,
)
from astrbot_plugin_proactive_life.mood import MoodStore

# ------------------------------------------------------------------ 通用桩


class _Conf:
    """冒充 AstrBotConfig：支持 get/in/[]/update/save_config。"""

    def __init__(self, data: dict | None = None):
        self._data = dict(data or {})
        self.saved = 0

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, patch):
        self._data.update(patch)

    def save_config(self):
        self.saved += 1


class _Owner:
    def __init__(self, data: dict | None = None):
        self._conf = _Conf(data)


def _cfg(**overrides) -> _LiveConfig:
    return _LiveConfig(_Owner(overrides), DEFAULT_CONFIG)


def _plugin(**cfg_overrides) -> ProactiveLifePlugin:
    """只装够用的插件实例（不跑真实平台/不落盘）。"""
    plugin = object.__new__(ProactiveLifePlugin)
    plugin._stop_event = asyncio.Event()
    plugin.cfg = _cfg(**cfg_overrides)
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    plugin._ensure_echo_tap = lambda event=None: None
    plugin.blacklist = SimpleNamespace(is_blacklisted_event=lambda ev: False)
    plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑", _is_muted=lambda gid: False)
    plugin.mood = MoodStore()
    plugin.motive_mgr = SimpleNamespace(peek_passive_hint=lambda: "")
    plugin.interact_ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
    plugin._rhythm_last = {}
    return plugin


class _Result:
    def __init__(self, text: str):
        self.chain = [Plain(text)]

    def is_llm_result(self) -> bool:
        return True

    def get_plain_text(self) -> str:
        return "".join(str(getattr(c, "text", "") or "") for c in self.chain)


class _Event:
    """私聊消息事件桩（节奏 / after_message_sent 共用）。"""

    def __init__(self, text: str, uid: str = "10001"):
        self.extras: dict = {}
        self.sent: list = []
        self.result = _Result(text)
        self.message_str = text
        self.unified_msg_origin = f"aiocqhttp:FriendMessage:{uid}"
        self._uid = uid
        self.stopped = False

    # 事件 extras
    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return dict(self.extras)
        return self.extras.get(key, default)

    # 结果
    def get_result(self):
        return self.result

    def plain_result(self, text):
        return ("plain", text)

    async def send(self, message):
        self.sent.append(message)

    # 会话
    def is_private_chat(self):
        return True

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return "阿岚"

    def get_group_id(self):
        return ""

    def stop_event(self):
        self.stopped = True


# ------------------------------------------------------------------ 1. 拆条后完整回复


def test_split_reply_keeps_full_text_for_inquiry(monkeypatch, tmp_path):
    """问句落在第 3 段时，追问注册仍然命中（以前只看到第 1 段，整条漏判）。"""
    monkeypatch.setattr(rhythm_mixin, "human_pause", lambda gap, seg, rng=None: 0.01)
    monkeypatch.setattr(rhythm_mixin, "_FIRST_SEG_MIN_GAP", 0.01)

    from astrbot_plugin_proactive_life.private_handler import (
        PrivateProactiveHandler,
        _is_inquiry,
    )

    text = "我在楼下买了杯咖啡\n店里人还挺多的\n你要不要也来一杯？"
    plugin = _plugin(
        节奏_启用=True, 节奏_处理方式="split", 节奏_每段字数=45,
        节奏_最多几条=5, 节奏_去句末标点=False, 节奏_打印处理详情=False,
    )
    handler = PrivateProactiveHandler(
        context=SimpleNamespace(),
        cfg={"对谁主动": ["10001"]},
        motive_mgr=None,
        state_file=tmp_path / "private_state.json",
    )
    plugin.private_h = handler
    plugin.debounce = SimpleNamespace(get_if_exists=lambda origin: None)

    event = _Event(text)

    async def run():
        await plugin.reshape(event)
        # 等补发任务跑完（human_pause 已被压到 0.01s）
        await asyncio.sleep(0.2)
        await plugin.on_after_message_sent(event)

    asyncio.run(run())

    # 拆条：chain 里只剩第一段，其余走补发
    assert len(event.result.chain) == 1
    assert event.result.chain[0].text == "我在楼下买了杯咖啡"
    assert len(event.sent) == 2
    # 完整回复被暂存下来
    assert event.extras["_pl_full_reply"] == text.replace("\n", "\n")
    assert "你要不要也来一杯？" in event.extras["_pl_full_reply"]
    # 追问按完整回复判定：第 1 段不构成追问，第 3 段构成
    assert not _is_inquiry(event.result.chain[0].text)
    assert _is_inquiry(event.extras["_pl_full_reply"])
    assert "10001" in handler.state["pending_inquiries"]


def test_after_message_sent_falls_back_without_full_reply(tmp_path):
    """没有 _pl_full_reply（未拆条）时回退 get_plain_text，行为不变。"""
    from astrbot_plugin_proactive_life.private_handler import PrivateProactiveHandler

    plugin = _plugin()
    handler = PrivateProactiveHandler(
        context=SimpleNamespace(),
        cfg={"对谁主动": ["10001"]},
        motive_mgr=None,
        state_file=tmp_path / "private_state.json",
    )
    plugin.private_h = handler
    plugin.debounce = SimpleNamespace(get_if_exists=lambda origin: None)
    event = _Event("在吗？吃了吗")
    asyncio.run(plugin.on_after_message_sent(event))
    assert handler.state["pending_inquiries"]["10001"]["question"] == "在吗？吃了吗"


# ------------------------------------------------------------------ 2. 闭嘴 / 情绪 Key


class _MuteHost(HandlerContextMixin):
    """只用 GroupMuteMixin + 情绪混入，避免真落盘。"""

    def __init__(self):
        from astrbot_plugin_proactive_life.group_mute import GroupMuteMixin

        self.cfg = {"拟人_情绪启用": True}
        self.mood_store = MoodStore()
        self._muted_until: dict[str, float] = {}
        self._mute_judge_at: dict[str, float] = {}
        self._recent_bot_spoke: dict[str, dict] = {}
        self.saved = 0
        self.mixin = GroupMuteMixin()

    # 借用 GroupMuteMixin 的实现，但把落盘/诊断换成计数
    def _save_muted(self, raise_on_error=False):
        self.saved += 1

    def _diag(self, origin, msg):
        return None


def _mute_host():
    host = _MuteHost()
    for name in ("_mute_group", "_is_muted", "_load_muted"):
        setattr(host, name, getattr(host.mixin, name).__get__(host))
    return host


def test_mute_key_is_canonical_group_id():
    """写端拿完整 origin、读端拿裸群号，也必须命中同一份静默记录。"""
    host = _mute_host()
    host._mute_group("aiocqhttp:GroupMessage:123456", 15, "群友")
    assert "123456" in host._muted_until
    assert "aiocqhttp:GroupMessage:123456" not in host._muted_until
    assert host._is_muted("123456") is True
    # 旧的完整 origin 写法仍然能查到（兼容面板/旧调用点）
    assert host._is_muted("aiocqhttp:GroupMessage:123456") is True


def test_group_mood_written_and_read_with_same_key():
    """群情绪写端用 origin、读端用裸群号：归一后必须能读到。"""
    host = _mute_host()
    host._mute_group("aiocqhttp:GroupMessage:123456", 15, "群友")
    assert host.mood_store.snapshot("123456")["name"] == "闷闷的"
    assert host.mood_store.snapshot("aiocqhttp:GroupMessage:123456")["name"] == "平静"

    block = life_inject.build_life_state_block(
        _cfg(拟人_情绪注入被动=True),
        info={"schedule": "", "outfit": "", "current_activity": ""},
        mood=host.mood_store,
        session_key="123456",
    )
    assert "此刻心情" in block


def test_reread_skips_muted_group():
    """闭嘴期（按裸群号记）不许跟读；没闭嘴时同一条复读能正常触发。"""
    msg = [Plain("笑死")]
    event = SimpleNamespace(
        is_at_or_wake_command=False,
        message_str="笑死",
        get_group_id=lambda: "123456",
        get_messages=lambda: msg,
        chain_result=lambda chain: ("chain", chain),
    )
    plugin = _plugin(跟读_阈值=2, 跟读_最小间隔秒=0, 跟读_概率=1.0, 跟读_屏蔽词=[])

    def _collect():
        async def run():
            return [r async for r in handle_group_reread(plugin, event)]

        return asyncio.run(run())

    plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑", _is_muted=lambda gid: True)
    assert _collect() == []

    plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑", _is_muted=lambda gid: False)
    plugin._reread_state = None
    # 阈值 2：第一次只记指纹，第二次才复读
    assert _collect() == []
    assert len(_collect()) == 1


# ------------------------------------------------------------------ 3. 戳一戳 / 纯艾特


class _PokeEvent:
    def __init__(self, *, private: bool, messages=None, raw=None, group: str = ""):
        self.extras: dict = {}
        self.stopped = False
        self._private = private
        self._group = group
        self._messages = list(messages or [])
        self.message_obj = SimpleNamespace(raw_message=raw or {"post_type": "message"})
        self.message_str = ""
        self.unified_msg_origin = (
            "aiocqhttp:FriendMessage:10001" if private else f"aiocqhttp:GroupMessage:{group}"
        )

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return dict(self.extras)
        return self.extras.get(key, default)

    def get_self_id(self):
        return "999"

    def get_sender_id(self):
        return "10001"

    def get_group_id(self):
        return self._group

    def get_messages(self):
        return self._messages

    def is_private_chat(self):
        return self._private

    def is_admin(self):
        return False

    def request_llm(self, prompt=""):
        self.requested_prompt = prompt
        return SimpleNamespace(prompt=prompt, image_urls=[])

    def stop_event(self):
        self.stopped = True


def _poke_plugin():
    return _plugin(
        戳一戳_纯艾特回应=True,
        戳一戳_后续合并=True,
        戳一戳_纯艾特等待秒=0,
        戳一戳_回应概率=1.0,
        戳一戳_纯艾特概率=1.0,
        戳一戳_个人冷却秒=0,
        戳一戳_群冷却秒=0,
        戳一戳_收录自己发言=True,
    )


def test_private_poke_gets_reply():
    """私聊被戳：没有群上下文也必须回一句（以前 group 为空直接哑火）。"""
    plugin = _poke_plugin()
    raw = {
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "self_id": 999, "target_id": 999, "user_id": 10001,
    }
    event = _PokeEvent(private=True, raw=raw)

    async def run():
        return [r async for r in handle_poke_event(plugin, event)]

    results = asyncio.run(run())
    assert len(results) == 1
    assert "戳" in results[0].prompt
    assert event.stopped is False


def test_group_pure_at_pending_stops_event():
    """群纯 @ 埋 pending 后必须截断事件，否则原生链路会再回一次（双回复）。"""
    plugin = _poke_plugin()
    event = _PokeEvent(private=False, messages=[At(qq="999")], group="123456")

    async def run():
        return [r async for r in handle_poke_event(plugin, event)]

    assert asyncio.run(run()) == []
    assert event.stopped is True
    assert plugin.interact_ctx.has_pending("123456") is True


# ------------------------------------------------------------------ 4. 按需注入


_INFO = {
    "outfit": "米色针织衫 + 深灰长裤",
    "outfit_style": "日系森女风",
    "current_activity": "写代码",
    "schedule": "09:00 写代码\n12:00 吃饭",
    "time_desc": "下午",
}


def test_on_demand_injection_hides_schedule_for_casual_chat():
    cfg = _cfg(日程_被动注入=True, 日程_被动按需注入=True, 日程_外观锚点="黑色长直发")
    now = datetime.datetime(2026, 11, 27, 15, 30)
    block = life_inject.build_life_state_block(cfg, now=now, info=_INFO, user_text="好累啊")
    assert "现在：15:30" in block
    assert "今日日程" not in block
    assert "今日穿搭" not in block
    assert "固定外观" not in block


def test_on_demand_injection_adds_state_when_asked():
    cfg = _cfg(日程_被动注入=True, 日程_被动按需注入=True, 日程_外观锚点="黑色长直发")
    now = datetime.datetime(2026, 11, 27, 15, 30)
    block = life_inject.build_life_state_block(cfg, now=now, info=_INFO, user_text="你现在在干嘛")
    assert "此刻在做：写代码" in block
    assert "今日日程" in block
    # 没问外观，就不给外观/穿搭
    assert "今日穿搭" not in block
    assert "固定外观" not in block


def test_on_demand_injection_adds_look_when_asked():
    cfg = _cfg(日程_被动注入=True, 日程_被动按需注入=True, 日程_外观锚点="黑色长直发")
    now = datetime.datetime(2026, 11, 27, 15, 30)
    block = life_inject.build_life_state_block(cfg, now=now, info=_INFO, user_text="穿什么呀")
    assert "今日穿搭：米色针织衫" in block
    assert "固定外观（不会随穿搭变化）：黑色长直发" in block
    assert "今日日程" not in block


def test_on_demand_switch_off_keeps_legacy_full_block():
    cfg = _cfg(日程_被动注入=True, 日程_被动按需注入=False)
    now = datetime.datetime(2026, 11, 27, 15, 30)
    block = life_inject.build_life_state_block(cfg, now=now, info=_INFO, user_text="好累啊")
    assert "今日日程" in block and "今日穿搭" in block


# ------------------------------------------------------------------ 5. 面板


def test_private_gap_left_uses_minutes():
    """「最短间隔」是分钟，倒计时必须乘 60（以前少乘，3 小时显示成 3 分钟）。"""
    now = time.time()
    plugin = SimpleNamespace(
        cfg={"最短间隔": 180, "每天最多几次": 3, "安静多久才找": 0,
             "追问等待秒数": 300, "追问过期秒数": 1800, "追问最多次数": 1,
             "对谁主动": ["10001"], "私聊主动_启用": True, "私聊追问_启用": True,
             "触发概率": 0.5},
        private_h=SimpleNamespace(state={
            "last_user_msg": {"10001": now},
            "last_active": {"10001": now - 60},
            "daily": {},
            "pending_inquiries": {},
        }),
    )
    block = panel_status._private_block(plugin, now, datetime.datetime.now())
    user = block["users"][0]
    assert user["gap_left"] == pytest.approx(180 * 60 - 60, abs=3)


def test_preset_apply_writes_every_key():
    """档位预设的键名必须都在 EDITABLE 里，不能被静默丢弃。"""
    from astrbot_plugin_proactive_life.panel_config import EDITABLE, apply_config

    conf = _Conf({"模块_八股清洗": True})
    plugin = SimpleNamespace(
        cfg=dict(DEFAULT_CONFIG),
        _conf=conf,
        private_h=None, group_h=None, worker_h=None,
    )
    plugin._sync_runtime_state = lambda: None
    plugin._sync_identity = lambda: None

    expected = {
        "restrained": {
            "触发概率", "群聊自发破冰概率", "群聊插话_概率", "摸鱼干活概率",
            "跟读_概率", "戳一戳_回应概率", "出站_清洗档位", "安静多久才找",
        },
        "balanced": {
            "触发概率", "群聊自发破冰概率", "群聊插话_概率", "摸鱼干活概率",
            "跟读_概率", "戳一戳_回应概率", "出站_清洗档位",
        },
        "active": {
            "触发概率", "群聊自发破冰概率", "群聊插话_概率", "摸鱼干活概率",
            "跟读_概率", "戳一戳_回应概率", "出站_清洗档位",
        },
    }
    for name, keys in expected.items():
        result = asyncio.run(panel_actions.do_action(plugin, {"action": "preset_apply", "name": name}))
        assert result["ok"] is True, result
        assert set(result["changed"]) == keys, (name, result)
        assert set(result["changed"]) <= set(EDITABLE)

    # 真写进活配置（而不是只回显）：apply_config 优先写 _conf 并落盘
    assert conf.get("出站_清洗档位") == "minimal"
    assert conf.get("安静多久才找") == 90
    assert conf.saved >= 3

    # 直接走 apply_config 校验：一个键都不该被白名单拒掉
    direct = apply_config(plugin, {k: 1 for k in expected["restrained"]})
    assert direct["rejected"] == [], direct
    assert direct["ok"] is True
