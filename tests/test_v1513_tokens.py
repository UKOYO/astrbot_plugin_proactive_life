"""v1.5.13 验收：控 Token / 降负载改造的回归护栏。

覆盖：
1. 主动链路出站清洗（agent_runner.clean_agent_text + run_agent 接线）；
2. 群黑话与心事解耦（独立入参、不被 80 字截断、不被心事开关连坐）；
3. `_muted_until` 不再跟着「群不活跃」被清掉，只在到期时清；
4. 节奏间隔配 0 时后续段真连发（不再被拟真下限顶住）；
5. 视觉门控：消息与「刚才那张图」无关时不注入图片部件；
6. 临时注入总预算裁剪（生活状态优先保留）；
7. schedule_data.json 只保留最近 30 天；
8. 作息延迟命中忙碌词才延迟，急事/指令/戳一戳豁免；
9. lite 档把 <conversation_scene> 拍平成两行；模型判闭嘴默认关闭。
"""

from __future__ import annotations

import asyncio
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from astrbot.core.message.components import Plain  # noqa: E402

from astrbot_plugin_proactive_life import agent_runner, life_inject, rhythm_mixin  # noqa: E402
from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.context_scene import SceneGenerator, SceneMessage  # noqa: E402
from astrbot_plugin_proactive_life.group_handler import GroupProactiveHandler  # noqa: E402
from astrbot_plugin_proactive_life.group_mute import GroupMuteMixin  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules import reply_delay  # noqa: E402
from astrbot_plugin_proactive_life.schedule_gen import ScheduleData, ScheduleStore  # noqa: E402


class _Conf:
    def __init__(self, data: dict | None = None):
        self._data = dict(data or {})

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, patch):
        self._data.update(patch)

    def save_config(self):
        pass


def _cfg(**overrides) -> _LiveConfig:
    owner = SimpleNamespace(_conf=_Conf(overrides))
    return _LiveConfig(owner, DEFAULT_CONFIG)


# ---------------------------------------------------------------- 1. 主动链路清洗


def test_clean_agent_text_strips_thinking_and_bagu():
    cfg = _cfg()
    cleaned = agent_runner.clean_agent_text("<think>内部推理</think>你好呀", cfg)
    assert "内部推理" not in cleaned
    assert "你好呀" in cleaned


def test_clean_agent_text_returns_empty_for_thinking_only():
    """只剩思考块时宁可这轮不发，也不要把兜底句当自言自语发出去。"""
    cfg = _cfg()
    assert agent_runner.clean_agent_text("<think>只有推理</think>", cfg) == ""


def test_run_agent_applies_clean_cfg(monkeypatch):
    """run_agent 拿到 completion_text 后必须按 clean_cfg 清洗。"""

    class _Runner:
        def __init__(self, text):
            self._text = text

        async def step_until_done(self, max_steps):
            yield None

        def get_final_llm_resp(self):
            return SimpleNamespace(completion_text=self._text)

    async def fake_build(event=None, plugin_context=None, config=None, req=None):
        return SimpleNamespace(agent_runner=_Runner("<think>x</think>好呀"))

    monkeypatch.setattr(agent_runner, "build_main_agent", fake_build)
    monkeypatch.setattr(agent_runner, "AGENT_AVAILABLE", True)

    outcome = asyncio.run(
        agent_runner.run_agent(
            SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
            build_cfg=object(), max_steps=3, clean_cfg=_cfg(),
        )
    )
    assert outcome.ok is True
    assert outcome.text == "好呀"


# ---------------------------------------------------------------- 2. 黑话解耦


def test_slang_not_gated_by_motive_switch():
    cfg = _cfg(心事_被动注入=False, 日程_被动注入=True)
    block = life_inject.build_life_state_block(
        cfg, info={"schedule": "", "outfit": "", "current_activity": ""},
        slang="群里最近常说的话（可自然带出）：电焊、破防",
    )
    assert "电焊" in block and "破防" in block


def test_slang_not_truncated_to_80_chars():
    cfg = _cfg(日程_被动注入=True)
    long_slang = "群里最近常说的话（可自然带出）：" + "、".join(f"词{i}" for i in range(20))
    block = life_inject.build_life_state_block(
        cfg, info={"schedule": "", "outfit": "", "current_activity": ""},
        slang=long_slang,
    )
    assert "词19" in block


# ---------------------------------------------------------------- 3. 静默表淘汰


def _prune_host():
    host = object.__new__(GroupProactiveHandler)
    noop = SimpleNamespace(clear=lambda *a, **k: None)
    host._active_stamp = {}
    host.recent_context_cache = {}
    host.active_reply_stacks = {}
    host.model_choice_histories = {}
    host._last_spoke_stamp = {}
    host._last_roll_stamp = {}
    host._keyword_last_spoke = {}
    host._recent_bot_spoke = {}
    host._miss_streak = {}
    host._user_spoke_after = {}
    host._diag_stamp = {}
    host._mute_judge_at = {}
    host._muted_until = {}
    host._scene = noop
    host._last_scene = {}
    host._summary_pending = {}
    host._summary_last_at = {}
    host._summary = noop
    host._members = noop
    return host


def test_muted_until_survives_inactive_prune():
    """群 24h 没消息也不许把还没到期的闭嘴记录清掉。"""
    import time

    host = _prune_host()
    now = time.time()
    host._active_stamp["aiocqhttp:GroupMessage:111"] = now - 90000  # 25 小时前
    host._muted_until["111"] = now + 600  # 还在静默期

    host._prune_origin_caches(now)
    assert host._muted_until.get("111") == now + 600


def test_muted_until_expired_is_pruned():
    import time

    host = _prune_host()
    now = time.time()
    host._muted_until["222"] = now - 1
    host._prune_origin_caches(now)
    assert "222" not in host._muted_until


def test_mute_judge_switch_defaults_off():
    class _Host(GroupMuteMixin):
        def __init__(self):
            self.cfg = {}
            self._recent_bot_spoke = {"g": {"time": __import__("time").time()}}

    host = _Host()
    assert host._should_judge_mute("g", "你好烦") is False
    host.cfg = {"群聊插话_模型判闭嘴": True}
    assert host._should_judge_mute("g", "你好烦") is True


# ---------------------------------------------------------------- 4. 连发 gap=0


def test_zero_gap_skips_pause_for_rest_segments(monkeypatch):
    calls: list[str] = []

    def fake_pause(gap, seg, rng=None):
        calls.append(seg)
        return 0.01

    monkeypatch.setattr(rhythm_mixin, "human_pause", fake_pause)
    monkeypatch.setattr(rhythm_mixin, "_FIRST_SEG_MIN_GAP", 0.0)

    from astrbot_plugin_proactive_life.main import ProactiveLifePlugin

    plugin = object.__new__(ProactiveLifePlugin)
    plugin.cfg = _cfg(
        节奏_启用=True, 节奏_处理方式="split", 节奏_每段字数=45, 节奏_最多几条=5,
        节奏_去句末标点=False, 节奏_打印处理详情=False, 节奏_间隔秒数=0,
    )
    plugin._module_on = lambda name: True
    plugin._rhythm_last = {}

    sent: list = []

    class _Event:
        def __init__(self):
            self.extras = {}
            self.result = SimpleNamespace(
                chain=[Plain("第一句\n第二句\n第三句")],
                is_llm_result=lambda: True,
            )

        def set_extra(self, k, v):
            self.extras[k] = v

        def get_extra(self, k=None, d=None):
            return self.extras.get(k, d)

        def get_result(self):
            return self.result

        def plain_result(self, text):
            return text

        async def send(self, message):
            sent.append(message)

    event = _Event()

    async def run():
        await plugin.reshape(event)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    # 只有首段走拟真停顿，后续段 gap=0 直接连发
    assert len(calls) == 1
    assert len(sent) == 2


# ---------------------------------------------------------------- 5. 视觉门控


def test_image_worth_injecting_rules():
    assert life_inject.image_worth_injecting("") is False
    assert life_inject.image_worth_injecting("好看") is True          # 纯短句
    assert life_inject.image_worth_injecting("刚那张图呢") is True     # 含「图」
    assert life_inject.image_worth_injecting("明天下午三点开会别忘了带材料") is False


def _inject_plugin(**overrides):
    from astrbot_plugin_proactive_life.modules.interact_context import InteractContext

    plugin = SimpleNamespace(
        cfg=_cfg(戳一戳_收录自己发言=True, 心事_被动注入=False, **overrides),
        context=SimpleNamespace(get_using_provider=lambda *a, **k: None),
        mood=None,
        motive_mgr=SimpleNamespace(peek_passive_hint=lambda: ""),
        person_memory=None,
        interact_ctx=InteractContext(window=6, ttl=60.0, self_ttl=120.0),
        group_h=SimpleNamespace(_bot_name=lambda: "唯笑"),
        _slang=SimpleNamespace(hint=lambda gid, cfg: ""),
    )
    plugin._module_on = lambda name: True
    plugin.interact_ctx.observe_self("88888", "[表情包]", bot_name="唯笑")
    return plugin


def test_image_part_skipped_for_unrelated_message(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        life_inject, "inject_temp_images",
        lambda req, urls, **kw: (calls.append(list(urls)), 1)[1],
    )
    monkeypatch.setattr(
        "astrbot_plugin_proactive_life.modules.interact_context.collect_recent_self_images",
        lambda items, max_images=2: ["data:image/jpeg;base64,AAA"],
    )
    monkeypatch.setattr(life_inject, "read_schedule", lambda *a, **k: {})

    plugin = _inject_plugin()
    req = SimpleNamespace(prompt="", extra_user_content_parts=[], system_prompt="")
    event = SimpleNamespace(
        is_private_chat=lambda: False,
        get_group_id=lambda: "88888",
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "阿岚",
        message_str="明天下午三点开会别忘了带材料",
        unified_msg_origin="aiocqhttp:GroupMessage:88888",
        get_extra=lambda k=None, d=None: d,
    )
    life_inject.inject_passive_context(plugin, event, req)
    assert calls == [], "与图片无关的消息不该再喂视觉 Token"

    # 相关消息才会注入
    event.message_str = "刚那张图呢"
    req.extra_user_content_parts.clear()
    life_inject.inject_passive_context(plugin, event, req)
    assert calls and calls[0] == ["data:image/jpeg;base64,AAA"]


# ---------------------------------------------------------------- 6. 预算裁剪


def test_budget_trims_style_before_dropping_echo():
    """预算不够时：说话方式只裁尾巴保核心，群动态整块让路，生活状态不动。"""
    parts = [
        {"priority": 1, "marker": "L", "text": "生活" * 50, "name": "life"},
        {"priority": 3, "marker": "E", "text": "动态" * 50, "name": "echo",
         "elastic": True},
        {"priority": 2, "marker": "S", "text": "【说话方式】\n" + "规范" * 200,
         "name": "style", "min_keep": 150, "elastic": True},
    ]
    out = life_inject._fit_budget(parts, 300)
    total = sum(len(p["text"]) for p in out)
    assert total <= 300
    life = next(p for p in out if p["name"] == "life")
    style = next(p for p in out if p["name"] == "style")
    echo = next(p for p in out if p["name"] == "echo")
    assert life["text"], "生活状态（时间/心情）必须保留"
    assert style["text"].startswith("【说话方式】"), "说话规范的核心不能整块丢"
    assert len(style["text"]) >= 150
    assert echo["text"] == "", "群动态是最先让路的一块"


def test_budget_hard_cap_even_when_all_parts_are_kept():
    """极端预算（lite 档 300）下合计仍不超预算。"""
    parts = [
        {"priority": 1, "marker": "G", "text": "身份" * 100, "name": "guard"},
        {"priority": 2, "marker": "S", "text": "【说话方式】" + "规范" * 150,
         "name": "style", "min_keep": 150, "elastic": True},
        {"priority": 3, "marker": "E", "text": "动态" * 60, "name": "echo",
         "elastic": True},
        {"priority": 1, "marker": "L", "text": "状态" * 40, "name": "life"},
    ]
    out = life_inject._fit_budget(parts, 300)
    assert sum(len(p["text"]) for p in out) <= 300
    assert next(p for p in out if p["name"] == "style")["text"].startswith("【说话方式】")


def test_budget_lite_tier_smaller():
    assert life_inject.inject_budget(_cfg(拟人_模型档位="lite")) == 300
    assert life_inject.inject_budget(_cfg(拟人_模型档位="auto")) == 600


def test_group_trigger_skips_duplicate_speech_style(monkeypatch):
    """群聊主动接话的 prompt 自带 _GUARDRAIL，不能再叠一份 speech_style。"""
    from astrbot_plugin_proactive_life.modules.speech_style import MARKER as SPEECH_MARKER

    monkeypatch.setattr(life_inject, "read_schedule", lambda *a, **k: {})

    def run(triggered: bool):
        plugin = _inject_plugin()
        req = SimpleNamespace(prompt="", extra_user_content_parts=[], system_prompt="")
        extras = {"_group_active_message_triggered": True} if triggered else {}
        event = SimpleNamespace(
            is_private_chat=lambda: False,
            get_group_id=lambda: "88888",
            get_sender_id=lambda: "10001",
            get_sender_name=lambda: "阿岚",
            message_str="在吗",
            unified_msg_origin="aiocqhttp:GroupMessage:88888",
            get_extra=lambda k=None, d=None: extras.get(k, d),
        )
        life_inject.inject_passive_context(plugin, event, req)
        return [getattr(p, "text", "") for p in req.extra_user_content_parts]

    assert not any(SPEECH_MARKER in t for t in run(True))
    assert any(SPEECH_MARKER in t for t in run(False))


# ---------------------------------------------------------------- 7. 日程裁剪


def test_schedule_store_keeps_last_30_days(tmp_path):
    store = ScheduleStore(tmp_path / "schedule_data.json")
    base = datetime.date(2026, 1, 1)
    for i in range(40):
        day = (base + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        store.set(ScheduleData(date=day, outfit="", outfit_style="", schedule="做事", status="ok"))

    reloaded = ScheduleStore(tmp_path / "schedule_data.json")
    assert len(reloaded._data) == ScheduleStore.KEEP_DAYS
    assert reloaded.get("2026-01-01") is None
    assert reloaded.get("2026-02-09") is not None  # 第 40 天


# ---------------------------------------------------------------- 8. 作息延迟


def test_reply_delay_busy_and_exemptions():
    cfg = _cfg(作息延迟_启用=True, 作息延迟_最多秒=12)
    assert reply_delay.compute_delay(cfg, "在吗", "在洗澡") == 12.0
    assert reply_delay.compute_delay(cfg, "在吗", "出门买菜") == pytest.approx(7.2)
    # 急事豁免
    assert reply_delay.compute_delay(cfg, "救命快回我", "在洗澡") == 0.0
    # 指令豁免
    assert reply_delay.compute_delay(cfg, "/help", "在洗澡") == 0.0
    # 没命中忙碌词
    assert reply_delay.compute_delay(cfg, "在吗", "写代码") == 0.0
    # 开关关闭
    assert reply_delay.compute_delay(_cfg(作息延迟_启用=False), "在吗", "在洗澡") == 0.0


# ---------------------------------------------------------------- 9. lite 档 / 步数


def test_lite_tier_flattens_scene():
    gen = SceneGenerator()
    msg = SceneMessage(
        sender_id="1", sender_name="阿岚", content="在吗", is_bot=False,
        talking_to="bot", talking_to_name="你",
    )
    full = gen.generate("active", "你是主动加入的", msg, [], show_flow=False)
    assert full.startswith("<conversation_scene>")
    flat = gen.generate("active", "你是主动加入的", msg, [], show_flow=False, flat=True)
    assert "<conversation_scene>" not in flat
    assert len(flat.splitlines()) <= 2


def test_max_steps_from_config():
    cfg = _cfg(主动_最大步数=3, 摸鱼_最大步数=9)
    assert agent_runner.max_steps_from(cfg, "主动_最大步数", 5) == 3
    assert agent_runner.max_steps_from(cfg, "摸鱼_最大步数", 6) == 9
    assert agent_runner.max_steps_from({}, "摸鱼_最大步数", 6) == 6
    assert agent_runner.max_steps_from({"摸鱼_最大步数": "坏值"}, "摸鱼_最大步数", 6) == 6


def test_casual_cap_defaults_off():
    """闲聊限长默认关闭：多群并发时不该悄悄改共享的 provider 参数。"""
    from astrbot_plugin_proactive_life.modules import token_guard

    assert DEFAULT_CONFIG["拟人_闲聊限长启用"] is False

    token_guard._active.clear()
    provider = SimpleNamespace(provider_config={"custom_extra_body": {"temperature": 0.8}})
    plugin = SimpleNamespace(
        cfg={},  # 缺键：代码兜底默认也必须是不启用
        context=SimpleNamespace(get_using_provider=lambda *a, **k: provider),
    )
    event = SimpleNamespace(message_str="在吗", unified_msg_origin="aiocqhttp:FriendMessage:1")
    assert token_guard.apply_casual_cap(plugin, event, SimpleNamespace()) is False
    assert provider.provider_config["custom_extra_body"] == {"temperature": 0.8}
    assert token_guard._active == {}
