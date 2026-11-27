"""v1.5.26 验收：复审抓到的 3 个 bug。

1. `history_guard` 会误删上一轮正常历史（没确认是 LLM 产物 / 内容对不上）
   + 算分时序倒挂（发送前已洗掉格式噪音，事后算分永远是 0）
2. 全角减号 `－－` 被标点归一反手合成破折号 `——`
3. `polish_rules` 的裸词 `|涟漪`、`玩味(…)?` 在 full 档下会改坏正常词
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig, ProactiveLifePlugin  # noqa: E402
from astrbot_plugin_proactive_life.modules.history_guard import (  # noqa: E402
    boilerplate_score,
    drop_dirty_turn,
)
from astrbot_plugin_proactive_life.modules.polish_bagu import (  # noqa: E402
    DASH_RUN,
    downgrade_dash,
)
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402


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


def _plug(history, **overrides):
    saved: dict = {}

    class _CM:
        async def get_curr_conversation_id(self, umo):
            return "cid"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps(history, ensure_ascii=False))

        async def update_conversation(self, umo, cid, history=None):
            saved["history"] = history

    return SimpleNamespace(
        cfg=_cfg(**overrides), context=SimpleNamespace(conversation_manager=_CM())
    ), saved


_EVENT = SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:1")


# ---------------------------------------------------------------- 1. 历史防污染


def test_raw_score_is_preferred_over_cleaned_text():
    """清洗后格式噪音/客服尾都没了，必须用清洗前的原始分。"""
    raw = "**整理如下**\n1. 先备份\n希望这些对你有帮助。"
    cleaned, _h, _i = ProsePolisher({}).polish(raw)
    assert boilerplate_score(raw) >= 3
    assert boilerplate_score(cleaned) == 0

    history = [
        {"role": "user", "content": "帮我整理"},
        {"role": "assistant", "content": cleaned},
    ]
    # 用洗后文本现算 → 不动历史
    plugin, saved = _plug(history)
    assert asyncio.run(drop_dirty_turn(plugin, _EVENT, cleaned)) is False
    assert saved == {}

    # 传原始分 → 摘掉
    plugin2, saved2 = _plug(history)
    ok = asyncio.run(
        drop_dirty_turn(plugin2, _EVENT, cleaned, raw_score=boilerplate_score(raw))
    )
    assert ok is True
    assert saved2["history"] == [{"role": "user", "content": "帮我整理"}]


def test_history_content_must_match_before_pop():
    """历史末条对不上（命令回包/别的回合）就不能动。"""
    history = [
        {"role": "user", "content": "晚饭吃啥"},
        {"role": "assistant", "content": "随便煮点面吧，懒得动。"},
    ]
    plugin, saved = _plug(history)
    ok = asyncio.run(drop_dirty_turn(plugin, _EVENT, "**完全不同的另一条**", raw_score=9))
    assert ok is False
    assert saved == {}


def test_only_llm_results_trigger_guard():
    class _Res:
        def __init__(self, llm):
            self._llm = llm

        def is_llm_result(self):
            return self._llm

    assert ProactiveLifePlugin._is_llm_result(_Res(True)) is True
    assert ProactiveLifePlugin._is_llm_result(_Res(False)) is False
    # 老版本没有该方法 → 按「不是 LLM」处理，宁可不删历史
    assert ProactiveLifePlugin._is_llm_result(SimpleNamespace()) is False


# ---------------------------------------------------------------- 2. 全角减号


def test_fullwidth_minus_downgraded_not_merged_into_dash():
    assert DASH_RUN.search("真的吗－－那算了")
    out, hits, _ids = ProsePolisher({}).polish("真的吗－－那算了")
    assert "—" not in out, out
    assert out == "真的吗，那算了"
    assert hits > 0


def test_downgrade_dash_handles_ascii_style_runs():
    for text in ("真的吗——那算了", "他说――别走", "a－－b"):
        new, n = downgrade_dash(text)
        assert n == 1, text
        assert "—" not in new and "－" not in new, new


# ---------------------------------------------------------------- 3. full 档裸词


def _full() -> ProsePolisher:
    return ProsePolisher({
        "清洗档位": "full", "换词": True, "改句式": True,
        "删八股短语": True, "水词配额": 1,
    })


def test_ripple_needs_context_not_bare_word():
    for text in ("群里有个叫涟漪的老哥", "这事引发了涟漪效应"):
        out, _h, _ids = _full().polish(text)
        assert out == text, f"{text} -> {out}"
    # 真八股仍然清得掉
    out, hits, _ids = _full().polish("她笑了一下，像一颗石子投入湖心，荡起涟漪。")
    assert "涟漪" not in out or "石子" not in out
    assert hits > 0


def test_playful_arc_requires_noun():
    out, _h, _ids = _full().polish("这事有点玩味")
    assert out == "这事有点玩味"
    out2, hits2, _ids2 = _full().polish("他露出玩味的弧度")
    assert "玩味的弧度" not in out2
    assert hits2 > 0
