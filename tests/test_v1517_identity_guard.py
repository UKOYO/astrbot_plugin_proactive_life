"""v1.5.17 验收：身份锚定（不自曝模型/厂商）+ 气泡末尾逗号清理。

背景：群里有人 @ 机器人问「你是谁」，它回了「我是混元，/ Tencent Hunyuan Hy，/
腾讯那边的模型」——既自曝了底层模型，又逐行报家门，一眼就是机器人。

覆盖：
1. `needs_guard` 能认出盘问身份的句子，普通闲聊不触发；
2. `拟人_身份锚定` 关掉时不注入；
3. 被动回复链路：问「你是谁」才注入身份块，普通消息不注入（零额外 Token）；
4. 群接话 prompt 同样只在被问时补；
5. 气泡末尾的「，」被清掉（按行拆条时不再出现「我是混元，」）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life import life_inject  # noqa: E402
from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules.identity_guard import (  # noqa: E402
    MARKER,
    guard_block,
    guard_for_text,
    needs_guard,
)
from astrbot_plugin_proactive_life.rhythm_trimmer import strip_trailing_punct  # noqa: E402


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


# ---------------------------------------------------------------- 识别


def test_needs_guard_matches_identity_questions():
    for text in (
        "你是谁",
        "大魔王你是谁啊",
        "@大魔王 你是谁",
        "你是什么模型",
        "用的什么模型",
        "是不是 AI",
        "你是机器人吗",
        "腾讯那边做的？",
        "你是混元吗",
        "底层模型是啥",
        "Who are you",
    ):
        assert needs_guard(text) is True, text


def test_needs_guard_ignores_normal_chat():
    for text in (
        "今天好累啊",
        "这个游戏好玩吗",
        "帮我看看这段代码为什么报错",
        "在吗",
        "哈哈哈哈哈",
        "明天几点开会",
    ):
        assert needs_guard(text) is False, text


def test_guard_block_respects_switch_and_bot_name():
    block = guard_block(_cfg(), "大魔王")
    assert block.startswith(MARKER)
    assert "大魔王" in block
    # 只陈述边界，不给台词模板（语气交还人设）
    assert "不提及底层模型" in block
    assert "用你自己人设的口吻" in block
    assert guard_block(_cfg(拟人_身份锚定=False), "大魔王") == ""
    # 没有自称时用中性说法，不要出现「你就是「我」」
    assert "「我」" not in guard_block(_cfg(), "")


def test_guard_for_text_only_when_asked():
    assert guard_for_text(_cfg(), "大魔王", "你是谁") != ""
    assert guard_for_text(_cfg(), "大魔王", "今天吃什么") == ""


# ---------------------------------------------------------------- 注入


def _plugin(**overrides):
    plugin = SimpleNamespace(
        cfg=_cfg(戳一戳_收录自己发言=False, 心事_被动注入=False, **overrides),
        context=SimpleNamespace(get_using_provider=lambda *a, **k: None),
        mood=None,
        motive_mgr=SimpleNamespace(peek_passive_hint=lambda: ""),
        person_memory=None,
        interact_ctx=None,
        group_h=SimpleNamespace(_bot_name=lambda: "大魔王"),
        _slang=SimpleNamespace(hint=lambda gid, cfg: ""),
    )
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    return plugin


def _run(plugin, message: str):
    req = SimpleNamespace(prompt="", extra_user_content_parts=[], system_prompt="")
    event = SimpleNamespace(
        is_private_chat=lambda: False,
        get_group_id=lambda: "123456",
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "keyou",
        message_str=message,
        unified_msg_origin="aiocqhttp:GroupMessage:123456",
        get_extra=lambda k=None, d=None: d,
    )
    life_inject.inject_passive_context(plugin, event, req)
    return [getattr(p, "text", "") for p in req.extra_user_content_parts]


def test_passive_reply_injects_guard_only_when_asked():
    texts = _run(_plugin(), "你是谁")
    assert any(MARKER in t for t in texts), texts
    assert not any("今天" in t for t in texts)

    plain = _run(_plugin(), "今天好累啊")
    assert not any(MARKER in t for t in plain), plain


def test_guard_switch_off_does_not_inject():
    texts = _run(_plugin(拟人_身份锚定=False), "你是谁")
    assert not any(MARKER in t for t in texts), texts


# ---------------------------------------------------------------- 标点


def test_trailing_comma_is_stripped():
    text, removed = strip_trailing_punct("我是混元，")
    assert text == "我是混元" and removed == 1
    assert strip_trailing_punct("Tencent Hunyuan Hy,")[0] == "Tencent Hunyuan Hy"
    assert strip_trailing_punct("你好、")[0] == "你好"
    # 问号仍然保留
    assert strip_trailing_punct("你是谁？")[0] == "你是谁？"
