"""v1.5.18 验收：收口"抢人设" + 控 Token + 修 v1.5.17 残留 Bug。

覆盖：
1. 私聊问「你是谁呀」不触发身份锚定（交给人设）；问「什么模型」才触发；
2. 私聊人物档案用配置称呼 + UID，而不是裸 QQ 昵称；
3. 主动私聊 / 摸鱼的请求体不再重复拼接 persona_block；
4. 命中词判定前先做 @Bot / 链接过滤，不白锁冷却、不清零饥饿计数；
5. 不主动开口的群喊「闭嘴」也能写入静默（让跟读停下）；
6. 主动私聊中间气泡不留尾逗号；
7. auto 档对 flash/mini 自动切 lite；
8. 主动链路清洗遵守模块总开关；
9. 低负载预设与心事列表缓存、日程人设截断。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life import agent_runner, life_inject, schedule_gen  # noqa: E402
from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.group_handler import GroupProactiveHandler  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules.identity_guard import (  # noqa: E402
    MARKER as GUARD_MARKER,
    guard_for_text,
    needs_guard,
)
from astrbot_plugin_proactive_life.modules.person_memory import PersonMemory  # noqa: E402
from astrbot_plugin_proactive_life.motive_pool import MotiveManager  # noqa: E402
from astrbot_plugin_proactive_life.panel_actions import do_action  # noqa: E402
from astrbot_plugin_proactive_life.panel_config import EDITABLE  # noqa: E402
from astrbot_plugin_proactive_life.rhythm_trimmer import strip_trailing_punct  # noqa: E402

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


# ---------------------------------------------------------------- 1. 身份锚定范围


def test_private_social_probe_is_handed_to_persona():
    """私聊「你是谁呀」不注入锚定（交给人设）；「什么模型」才注入。"""
    assert needs_guard("你是谁呀", private=True) is False
    assert needs_guard("你是谁", private=False) is True
    assert needs_guard("你用的什么模型", private=True) is True
    assert needs_guard("你是混元吗", private=True) is True
    assert guard_for_text(_cfg(), "大魔王", "你是谁呀", private=True) == ""
    assert GUARD_MARKER in guard_for_text(_cfg(), "大魔王", "什么模型", private=True)


# ---------------------------------------------------------------- 2. 私聊称呼


def test_private_person_block_uses_configured_name(tmp_path):
    pm = PersonMemory(path=tmp_path / "person.json")
    pm.touch("10001", "今天好累，想早点睡", name="keyou", scope="private")
    block = pm.block(
        "10001", scope="private", name="keyou",
        current_text="在吗", display_name="优可",
    )
    assert "优可" in block
    assert "UID:10001" in block

    # 没配称呼时退回昵称 + UID，仍然不是裸 QQ 昵称
    fallback = pm.block("10001", scope="private", name="keyou")
    assert fallback.startswith("- 关于keyou（UID:10001）") or "UID:10001" in fallback


def _private_plugin(**overrides):
    plugin = SimpleNamespace(
        cfg=_cfg(角色_对方称呼="优可", 心事_被动注入=False, 戳一戳_收录自己发言=False, **overrides),
        context=SimpleNamespace(get_using_provider=lambda *a, **k: None),
        mood=None,
        motive_mgr=SimpleNamespace(peek_passive_hint=lambda: ""),
        interact_ctx=None,
        group_h=SimpleNamespace(_bot_name=lambda: "大魔王"),
    )
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    return plugin


def test_private_passive_injection_uses_persona_name(tmp_path):
    pm = PersonMemory(path=tmp_path / "person.json")
    pm.touch("10001", "今天好累", name="keyou", scope="private")
    plugin = _private_plugin()
    plugin.person_memory = pm
    req = SimpleNamespace(prompt="", extra_user_content_parts=[], system_prompt="")
    event = SimpleNamespace(
        is_private_chat=lambda: True,
        get_group_id=lambda: "",
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "keyou",
        message_str="你是谁呀",
        unified_msg_origin="aiocqhttp:FriendMessage:10001",
        get_extra=lambda k=None, d=None: d,
    )
    life_inject.inject_passive_context(plugin, event, req)
    texts = [getattr(p, "text", "") for p in req.extra_user_content_parts]
    joined = "\n".join(texts)
    assert "优可" in joined and "UID:10001" in joined
    assert GUARD_MARKER not in joined, "私聊普通问句不该被插件拦截"


# ---------------------------------------------------------------- 3. 不重复拼人设


def test_prompts_no_longer_embed_persona_block():
    from astrbot_plugin_proactive_life import private_handler, worker_handler

    assert "{persona_block}" not in private_handler._WAKE_PROMPT
    assert "{persona_block}" not in private_handler._NUDGE_PROMPT
    assert "{persona_block}" not in worker_handler._WORKER_PROMPT
    # 宿主 system_prompt 已有完整人设，插件 prompt 不该再复述
    assert "短碎" not in private_handler._WAKE_PROMPT
    assert "严禁动作括号" not in private_handler._NUDGE_PROMPT


# ---------------------------------------------------------------- 4/5. 群聊时序


def _host(**overrides) -> GroupProactiveHandler:
    import time

    host = object.__new__(GroupProactiveHandler)
    host.cfg = _cfg(**overrides)
    host.motive_mgr = None
    for name in (
        "_active_stamp", "_user_spoke_after", "_miss_streak", "_recent_bot_spoke",
        "_diag_stamp", "_muted_until", "_mute_judge_at", "_last_spoke_stamp",
        "_last_roll_stamp", "_keyword_last_spoke", "active_reply_stacks",
        "model_choice_histories", "_last_scene", "recent_context_cache",
        "_summary_pending", "_summary_last_at",
    ):
        setattr(host, name, {})
    host._last_prune = time.time()
    host._bg_tasks = set()
    host._scene = SimpleNamespace(recent=lambda *a, **k: [], append=lambda *a, **k: None)
    host._members = SimpleNamespace(observe=lambda *a, **k: None, prune=lambda *a, **k: None)
    host._save_muted = lambda *a, **k: None
    host._diag = lambda *a, **k: None
    return host


def _group_event(text: str = "唯笑在吗", *, at: bool = False, gid: str = GROUP):
    return SimpleNamespace(
        unified_msg_origin=f"aiocqhttp:GroupMessage:{gid}",
        message_str=text,
        is_at_or_wake_command=at,
        get_group_id=lambda: gid,
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "阿岚",
        get_self_id=lambda: BOT,
        get_messages=lambda: [],
        set_extra=lambda k, v: None,
    )


def _drain(gen):
    async def run():
        return [r async for r in gen]

    return asyncio.run(run())


def test_keyword_cooldown_not_locked_by_at_or_link():
    host = _host(
        群聊命中词_启用=True, 群聊命中词_关键词=["唯笑"], 群聊命中词_接话概率=1.0,
        群聊命中词_最短间隔秒=20, 群聊插话_启用=False,
    )
    # @Bot：交给原生链路，不许锁命中词冷却
    _drain(host.handle_group_message_passive(_group_event(at=True)))
    assert host._keyword_last_spoke == {}
    # 带链接：过滤掉，也不许锁
    _drain(host.handle_group_message_passive(_group_event(text="唯笑看这个 https://x.cn/a")))
    assert host._keyword_last_spoke == {}
    # 正常命中词：唤醒并锁冷却
    _drain(host.handle_group_message_passive(_group_event(text="唯笑在吗")))
    assert f"aiocqhttp:GroupMessage:{GROUP}" in host._keyword_last_spoke


def test_quiet_group_mute_still_recorded():
    """只开了跟读、不会被主动开口的群，喊闭嘴也要能写入静默。"""
    host = _host(模块_跟读=True)
    assert host._group_engaged(GROUP) is False
    host.on_group_message_received(_group_event(text="闭嘴"))
    assert host._muted_until, "静默记录为空，跟读不会被阻断"


# ---------------------------------------------------------------- 6. 中间段尾逗号


def test_private_segments_strip_trailing_comma(monkeypatch, tmp_path):
    from astrbot_plugin_proactive_life import private_handler as ph

    sent: list[str] = []

    class _Ctx:
        async def send_message(self, session, chain):
            sent.append(chain.chain[0].text)

        def get_config(self, umo=None):
            return {}

    async def fake_run_agent(*args, **kwargs):
        return agent_runner.AgentOutcome(True, text="第一句，\n第二句；\n第三句，")

    monkeypatch.setattr(ph, "run_agent", fake_run_agent)
    monkeypatch.setattr(ph, "build_agent_config", lambda *a, **k: object())

    handler = ph.PrivateProactiveHandler(
        context=_Ctx(),
        cfg={
            "模拟分段打字": True, "分段发送延迟": 0, "节奏_每段字数": 45,
            "节奏_最多几条": 5,
        },
        motive_mgr=None,
        state_file=tmp_path / "state.json",
    )
    handler._resolve_umo = lambda uid: "aiocqhttp:FriendMessage:10001"
    ok = asyncio.run(handler._execute_proactive_agent("10001", "prompt", "test"))
    assert ok is True
    assert sent == ["第一句", "第二句", "第三句"], sent
    assert strip_trailing_punct("第二句；")[0] == "第二句"


# ---------------------------------------------------------------- 7. auto 档


def test_resolve_model_tier_auto():
    assert life_inject.resolve_model_tier(_cfg(), "gemini-2.5-flash") == "lite"
    assert life_inject.resolve_model_tier(_cfg(), "gpt-4o-mini") == "lite"
    assert life_inject.resolve_model_tier(_cfg(), "qwen-7b") == "lite"
    assert life_inject.resolve_model_tier(_cfg(), "gpt-4o") == "full"
    assert life_inject.resolve_model_tier(_cfg(拟人_模型档位="full"), "flash") == "full"
    assert life_inject.resolve_model_tier(_cfg(拟人_模型档位="lite"), "gpt-4o") == "lite"
    assert life_inject.inject_budget(_cfg(), "flash") == 300
    assert life_inject.inject_budget(_cfg(), "gpt-4o") == 600


# ---------------------------------------------------------------- 8. 清洗开关


def test_clean_agent_text_respects_module_switches():
    text = "<think>推理</think>好的"
    off = _cfg(模块_思考过滤=False, 模块_八股清洗=False)
    assert agent_runner.clean_agent_text(text, off) == text
    on = _cfg()
    assert "<think>" not in agent_runner.clean_agent_text(text, on)


# ---------------------------------------------------------------- 9. 负载项


def test_lowload_preset():
    conf = _Conf()
    plugin = SimpleNamespace(
        cfg=dict(DEFAULT_CONFIG), _conf=conf,
        private_h=None, group_h=None, worker_h=None,
    )
    plugin._sync_runtime_state = lambda: None
    plugin._sync_identity = lambda: None
    result = asyncio.run(do_action(plugin, {"action": "preset_apply", "name": "lowload"}))
    assert result["ok"] is True, result
    assert set(result["changed"]) == {
        "场景感知_总结启用", "拟人_跨会话记忆", "黑话_自动解析", "出站_清洗档位",
    }
    assert set(result["changed"]) <= set(EDITABLE)
    assert conf.get("场景感知_总结启用") is False


def test_motive_list_is_cached(tmp_path, monkeypatch):
    from astrbot_plugin_proactive_life import safe_io

    path = tmp_path / "motive_pool.json"
    path.write_text('{"motives": [{"id": "m1", "content": "想找人聊聊"}]}', encoding="utf-8")
    calls = {"n": 0}
    real = safe_io.sync_read_json

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(safe_io, "sync_read_json", counted)
    mgr = MotiveManager(path)
    first = mgr.list_motives()
    second = mgr.list_motives()
    assert first and second == first
    # 构造函数读一次就够；两次 list 不应再读盘
    assert calls["n"] <= 1


def test_schedule_persona_is_truncated():
    gen = object.__new__(schedule_gen.ScheduleGenerator)
    gen.identity = SimpleNamespace(
        life_bg="", bot_name="大魔王", persona_block=lambda: "设" * 10000
    )
    gen.context = None
    text = asyncio.run(gen._persona())
    assert len(text) <= schedule_gen.ScheduleGenerator._PERSONA_LIMIT + 1
    assert text.endswith("…")
