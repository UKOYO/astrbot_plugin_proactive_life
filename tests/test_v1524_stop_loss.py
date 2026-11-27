"""v1.5.24 验收：出站止损档 + 上游按模型精准止痒 + 正样本与历史防污染。

背景：`polish_rules` / `polish_bagu` 那套词表本质是「网文八股清洗」，
拿来洗 QQ 日常聊天会误伤正常词。方向改为——
出站只做死板格式兜底；口癖靠上游提示词；脏历史不回喂。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules.history_guard import (  # noqa: E402
    boilerplate_score,
    drop_dirty_turn,
)
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402
from astrbot_plugin_proactive_life.modules.speech_style import (  # noqa: E402
    speech_style_block,
)


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


# ---------------------------------------------------------------- 1. 止损档默认


def test_default_polisher_is_minimal():
    p = ProsePolisher({})
    assert p.config.get("清洗档位") == "minimal"
    # 三个高风险改写默认关闭
    assert p.config.get("换词") is False
    assert p.config.get("删八股短语") is False
    assert p.config.get("改句式") is False


def test_minimal_keeps_normal_chat_untouched():
    """日常聊天里的文学词/人名/正常词一律不动。"""
    normal = [
        "群里有人叫涟漪，还有个叫微风。",
        "今天穿了多巴胺穿搭，挺显眼。",
        "我刚泡了杯咖啡，泡沫有点多。",
        "他微微一笑，说了句悄悄话。",
        "昨晚一直忙着改代码，我几乎没睡。",
        "水有点浑浊，屏幕也突然泛白了。",
    ]
    p = ProsePolisher({})
    for text in normal:
        out, _hits, _ids = p.polish(text)
        assert out == text, f"{text} -> {out}"


def test_minimal_still_cleans_format_and_service_tail():
    p = ProsePolisher({})
    out, hits, _ids = p.polish("**重点**：\n1. 先备份\n2. 再升级\n希望这些对你有帮助。")
    assert "**" not in out and "1." not in out
    assert "希望这些对你有帮助" not in out
    assert "先备份" in out and hits > 0


# ---------------------------------------------------------------- 2. 按模型精准止痒


def test_model_hints_are_family_specific():
    deepseek = speech_style_block(_cfg(), "deepseek-chat")
    assert "不是…而是…" in deepseek and "破折号" in deepseek

    gpt = speech_style_block(_cfg(), "gpt-4o")
    assert "自动分点" in gpt and "反问" in gpt

    gemini = speech_style_block(_cfg(), "gemini-2.5-pro")
    assert "客服式寒暄" in gemini

    claude = speech_style_block(_cfg(), "claude-3-5-sonnet")
    assert "比喻" in claude


def test_light_model_gets_extra_hint_but_at_most_two():
    block = speech_style_block(_cfg(), "gemini-2.5-flash")
    assert "客服式寒暄" in block      # 家族
    assert "一两句说完" in block      # 小模型补充
    lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
    # 家族 1 句 + 小模型 1 句，别堆成一坨
    assert sum(1 for ln in lines if "一两句说完" in ln or "客服式寒暄" in ln) == 2


# ---------------------------------------------------------------- 3. 正样本


def test_speech_anchors_injected_only_when_configured():
    cfg = _cfg(拟人_口语锚点=["行吧，那我先去洗澡", "刚吃完，有点撑", "先睡了，明天说"])
    block = speech_style_block(cfg, "")
    assert "这样说最像" in block
    assert "行吧，那我先去洗澡" in block
    assert "先睡了，明天说" in block

    # 默认空：不注入，零开销
    assert "这样说最像" not in speech_style_block(_cfg(), "")


def test_speech_anchors_are_capped():
    cfg = _cfg(拟人_口语锚点=[f"第{i}句口语" for i in range(6)])
    block = speech_style_block(cfg, "")
    assert "第0句口语" in block and "第2句口语" in block
    assert "第3句口语" not in block


# ---------------------------------------------------------------- 4. 历史防污染


def test_boilerplate_score():
    assert boilerplate_score("今天加班到九点，我先睡了。") == 0
    # 格式噪音（加粗 + 列表）= 2
    assert boilerplate_score("**重点**\n1. 第一点") >= 2
    # 客服尾 +1
    assert boilerplate_score("希望这些对你有帮助。") >= 1


def _plug_with_history(history, **cfg):
    saved: dict = {}

    class _CM:
        async def get_curr_conversation_id(self, umo):
            return "cid"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps(history, ensure_ascii=False))

        async def update_conversation(self, umo, cid, history=None):
            saved["history"] = history

    plugin = SimpleNamespace(
        cfg=_cfg(**cfg),
        context=SimpleNamespace(conversation_manager=_CM()),
    )
    return plugin, saved


def test_dirty_turn_is_dropped_from_history():
    history = [
        {"role": "user", "content": "帮我整理下"},
        {"role": "assistant", "content": "**整理如下**\n1. 先备份\n希望这些对你有帮助。"},
    ]
    plugin, saved = _plug_with_history(history)
    event = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:1")
    ok = asyncio.run(
        drop_dirty_turn(plugin, event, "**整理如下**\n1. 先备份\n希望这些对你有帮助。")
    )
    assert ok is True
    assert saved["history"] == [{"role": "user", "content": "帮我整理下"}]


def test_clean_turn_stays():
    history = [
        {"role": "user", "content": "晚上吃啥"},
        {"role": "assistant", "content": "随便煮点面吧，懒得动。"},
    ]
    plugin, saved = _plug_with_history(history)
    event = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:1")
    assert asyncio.run(drop_dirty_turn(plugin, event, "随便煮点面吧，懒得动。")) is False
    assert saved == {}


def test_history_guard_switch_off():
    history = [{"role": "assistant", "content": "**重点**\n希望这些对你有帮助。"}]
    plugin, saved = _plug_with_history(history, 出站_历史防污染=False)
    event = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:1")
    assert asyncio.run(
        drop_dirty_turn(plugin, event, "**重点**\n希望这些对你有帮助。")
    ) is False
    assert saved == {}
