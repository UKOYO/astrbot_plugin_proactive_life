"""v1.5.19 验收：终审扫出的高危缺陷与两处边界。

覆盖：
1. 作息延迟的模块路径（`..common`）——以前 ImportError 被吞，功能 100% 失效；
2. lowload 关掉跨会话记忆后，私聊仍用配置称呼（不再退回裸 QQ 昵称）；
3. 群聊档位解析透传 origin（按群指定轻量模型时也能拍平场景）；
4. 八股清洗的「演练模式」真的只统计不改写；
5. 高风险句式在动手清洗前、针对正文统计（破折号能检出，代码块不误报）；
6. 职场黑话替换池不再含「…」模板（避免产出病句）；
7. 思考标签过滤不再动代码围栏内的内容。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life import life_inject  # noqa: E402
from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules import reply_delay  # noqa: E402
from astrbot_plugin_proactive_life.modules.bare_thinking import strip_bare_thinking  # noqa: E402
from astrbot_plugin_proactive_life.modules.polish_ai_rules import (  # noqa: E402
    CORPORATE_REPL,
)
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402
from astrbot_plugin_proactive_life.modules.think_filtering import (  # noqa: E402
    filter_reasoning_text,
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


# ---------------------------------------------------------------- 1. 作息延迟


def test_reply_delay_reads_schedule_via_package(monkeypatch):
    """`modules/reply_delay.py` 必须用 `..common` 导入；以前写错路径被吞，永远 0 秒。"""
    import astrbot_plugin_proactive_life.common as common

    monkeypatch.setattr(
        common, "read_schedule",
        lambda *a, **k: {"current_activity": "在洗澡"},
    )
    cfg = _cfg(作息延迟_启用=True, 作息延迟_最多秒=12)
    assert reply_delay.current_activity(cfg) == "在洗澡"
    # 端到端：忙碌词 → 真的算出延迟
    assert reply_delay.compute_delay(cfg, "在吗", reply_delay.current_activity(cfg)) == 12.0


# ---------------------------------------------------------------- 2. 私聊称呼


def _private_plugin(**overrides):
    plugin = SimpleNamespace(
        cfg=_cfg(角色_对方称呼="优可", 心事_被动注入=False, 戳一戳_收录自己发言=False, **overrides),
        context=SimpleNamespace(get_using_provider=lambda *a, **k: None),
        mood=None,
        motive_mgr=SimpleNamespace(peek_passive_hint=lambda: ""),
        interact_ctx=None,
        group_h=SimpleNamespace(_bot_name=lambda: "大魔王"),
        person_memory=None,
    )
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    plugin._get_person_memory = lambda: None  # 模拟 lowload 关掉跨会话记忆
    return plugin


def test_private_name_survives_lowload(monkeypatch):
    """关掉跨会话记忆（lowload 预设）后，私聊仍要带上配置称呼 + UID。"""
    import astrbot_plugin_proactive_life.life_inject as li

    monkeypatch.setattr(li, "read_schedule", lambda *a, **k: {})
    plugin = _private_plugin()
    req = SimpleNamespace(prompt="", extra_user_content_parts=[], system_prompt="")
    event = SimpleNamespace(
        is_private_chat=lambda: True,
        get_group_id=lambda: "",
        get_sender_id=lambda: "10001",
        get_sender_name=lambda: "keyou",
        message_str="在吗",
        unified_msg_origin="aiocqhttp:FriendMessage:10001",
        get_extra=lambda k=None, d=None: d,
    )
    li.inject_passive_context(plugin, event, req)
    joined = "\n".join(getattr(p, "text", "") for p in req.extra_user_content_parts)
    assert "当前私聊对象：优可（UID:10001）" in joined
    assert "keyou" not in joined


# ---------------------------------------------------------------- 3. 档位 origin


def test_model_tier_passes_origin(monkeypatch):
    from astrbot_plugin_proactive_life import common
    from astrbot_plugin_proactive_life.group_prompt import GroupPromptMixin

    seen: list[str] = []

    def fake_provider(ctx, umo=None):
        seen.append(str(umo or ""))
        return SimpleNamespace(get_model=lambda: "gemini-2.5-flash")

    monkeypatch.setattr(common, "get_chat_provider", fake_provider)
    host = SimpleNamespace(
        context=SimpleNamespace(),
        cfg=_cfg(),
        _scene_cfg=lambda k, d: d,
    )
    assert GroupPromptMixin._model_tier(host, "aiocqhttp:GroupMessage:88888") == "lite"
    assert seen[-1] == "aiocqhttp:GroupMessage:88888"


# ---------------------------------------------------------------- 4. 演练模式


def test_dry_run_keeps_original_text():
    polisher = ProsePolisher({"演练模式": True})
    raw = "你说得太对了，这个思路一针见血，我们可以一起努力。"
    out, hits, _rules = polisher.polish(raw)
    assert out == raw, "演练模式不该改写出站正文"
    assert hits > 0, "仍要统计命中数供面板展示"


def test_normal_mode_still_rewrites():
    polisher = ProsePolisher({})
    raw = "你说得太对了，这个思路一针见血。"
    out, hits, _rules = polisher.polish(raw)
    assert hits > 0
    assert out != raw


# ---------------------------------------------------------------- 5. 高风险统计


def test_high_risk_counts_body_dash_before_cleaning():
    polisher = ProsePolisher({})
    raw = "我们先这样——然后再看情况。"
    polisher.polish(raw)
    assert polisher.last_risk.get("dash_heavy") == 1, polisher.last_risk


def test_high_risk_ignores_code_fence():
    polisher = ProsePolisher({})
    raw = "看这段 SQL：\n```sql\nSELECT 1 -- 注释 -- 再来一个\n```\n就这样。"
    polisher.polish(raw)
    assert not polisher.last_risk.get("dash_heavy"), polisher.last_risk


# ---------------------------------------------------------------- 6. 替换池


def test_corporate_replacements_have_no_template_placeholder():
    for word, pool in CORPORATE_REPL.items():
        for item in pool:
            assert "…" not in item, f"{word} 的替换词含模板占位符: {item}"


# ---------------------------------------------------------------- 7. 代码围栏


def test_think_tag_inside_code_fence_is_preserved():
    text = "示例：\n```html\n<thinking>这里只是示例</thinking>\n```\n看完就懂。"
    cleaned, removed = filter_reasoning_text(text, ["thinking"])
    assert "<thinking>这里只是示例</thinking>" in cleaned
    assert removed == 0

    # 围栏外的真思考块照样删
    cleaned2, removed2 = filter_reasoning_text("正文<thinking>偷偷推理</thinking>继续", ["thinking"])
    assert "偷偷推理" not in cleaned2
    assert removed2 == 1


def test_bare_thinking_keeps_fence_content():
    text = "解释一下这个标签：\n```xml\n<thinking>示例内容</thinking>\n```\n就是这样。"
    out = strip_bare_thinking(text)
    assert "<thinking>示例内容</thinking>" in out
