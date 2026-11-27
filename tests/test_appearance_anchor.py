"""固定外观锚点（``日程_外观锚点``）的回归测试。

覆盖：
1. ``schedule_gen.ScheduleGenerator.build_prompt``：配置了外观时注入前提与硬约束，
   没配置时整行删掉，不留空标题；自定义模板缺占位符也不炸；
2. ``life_inject.build_life_state_block``：外观行随配置出现/消失，且与穿搭分开；
3. 配置默认值为空，确保公开用户不填就完全保持原有行为。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life import config_defaults, life_inject  # noqa: E402
from astrbot_plugin_proactive_life.life_inject import build_life_state_block  # noqa: E402
from astrbot_plugin_proactive_life.schedule_gen import ScheduleGenerator  # noqa: E402

NOW = datetime.datetime(2026, 11, 27, 17, 30)

BASE_CFG = {
    "模块_日程": True,
    "日程_被动注入": True,
    "日程_被动注入字数": 300,
}

INFO = {
    "date": "2026-11-27",
    "outfit": "米白衬衫 + 橄榄绿长裙",
    "outfit_style": "日系森女风",
    "schedule": "10:30 睡醒\n11:00 喝红茶\n15:00 涂鸦",
    "current_activity": "听音乐",
    "time_desc": "下午",
}

CTX = {
    "date_str": "2026年11月27日",
    "weekday": "星期五",
    "holiday": "",
    "persona_desc": "示例人设",
    "daily_theme": "宅家日",
    "mood_color": "清新",
    "outfit_style": "日系森女风",
    "schedule_type": "独处充电型",
    "history_schedules": "",
    "recent_chats": "",
    "weather": "",
}


def _gen() -> ScheduleGenerator:
    return ScheduleGenerator(None, {}, None)


# ------------------------------------------------------------------ 提示词


class TestBuildPrompt:
    def test_no_appearance_keeps_prompt_clean(self):
        prompt = _gen().build_prompt(dict(CTX))
        assert "固定外观" not in prompt
        assert "不得改写发色" not in prompt
        # 原有内容不能被误删
        assert "## Tasks" in prompt
        assert "outfit" in prompt

    def test_appearance_injects_premise_and_rule(self):
        prompt = _gen().build_prompt({**CTX, "appearance": "黑色长直发、琥珀色眼睛"})
        assert "- 固定外观（不可改写）：黑色长直发、琥珀色眼睛" in prompt
        assert "不得改写发色、瞳色、脸型、体型" in prompt
        # 空占位符残留不能出现在成品里
        assert "{appearance" not in prompt

    def test_appearance_whitespace_only_counts_as_empty(self):
        prompt = _gen().build_prompt({**CTX, "appearance": "   "})
        assert "固定外观" not in prompt
        assert "不得改写发色" not in prompt

    def test_custom_template_without_placeholder_is_safe(self):
        gen = ScheduleGenerator(
            None, {"日程_提示词模板": "自定义模板 {date_str} {outfit_style}"}, None
        )
        prompt = gen.build_prompt({**CTX, "appearance": "黑色长直发"})
        assert "自定义模板" in prompt
        assert "2026年11月27日" in prompt


# --------------------------------------------------------------- 生活状态注入


class TestLifeStateBlock:
    def test_block_has_no_appearance_line_by_default(self):
        block = build_life_state_block(BASE_CFG, now=NOW, info=INFO)
        assert "固定外观" not in block
        assert "米白衬衫" in block

    def test_block_carries_appearance_when_configured(self):
        cfg = {**BASE_CFG, "日程_外观锚点": "黑色长直发、琥珀色眼睛"}
        block = build_life_state_block(cfg, now=NOW, info=INFO)
        assert "- 固定外观（不会随穿搭变化）：黑色长直发、琥珀色眼睛" in block
        # 外观行必须在穿搭行之前，语义上作为前提
        assert block.index("固定外观") < block.index("今日穿搭")

    def test_appearance_alone_still_injects(self):
        """没有日程但有外观时，外观仍然该带出去。"""
        cfg = {**BASE_CFG, "日程_外观锚点": "黑色长直发"}
        empty = {"outfit": "", "current_activity": "", "schedule": ""}
        block = build_life_state_block(cfg, now=NOW, info=empty)
        assert "黑色长直发" in block


# ------------------------------------------------------------------ 默认配置


def test_default_appearance_is_empty():
    """公开用户的默认值必须是空串，不填就零行为变化。"""
    assert config_defaults.DEFAULT_CONFIG["日程_外观锚点"] == ""
    assert life_inject is not None
