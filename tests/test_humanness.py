"""拟人四维度回归测试：主体性/摩擦/克制/连续。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.speech_style import (  # noqa: E402
    MARKER, speech_style_block,
)


def _cfg(**over):
    cfg = {"拟人_说话方式约束": True, "拟人_说话方式强度": "strict"}
    cfg.update(over)
    return cfg


class TestSubjectivity(unittest.TestCase):
    """维度1：有自己的世界，不以用户为中心。"""

    def test_has_own_world(self):
        block = speech_style_block(_cfg(), "")
        self.assertIn("自己日子", block)
        self.assertIn("不是待命工具", block)
        # 明确反对围着用户转
        self.assertIn("别把对话当成存在的全部理由", block)


class TestFriction(unittest.TestCase):
    """维度2：平等感与适度的摩擦力。"""

    def test_allows_friction(self):
        block = speech_style_block(_cfg(), "")
        for kw in ("不知道", "敷衍", "不同意", "走神"):
            self.assertIn(kw, block, f"缺少摩擦力要素：{kw}")

    def test_no_flattery(self):
        block = speech_style_block(_cfg(), "")
        self.assertIn("禁止吹捧奉承", block)
        self.assertIn("一针见血", block)


class TestRestraint(unittest.TestCase):
    """维度3：克制与不完整，拒绝强行闭环。"""

    def test_no_forced_closure(self):
        block = speech_style_block(_cfg(), "")
        self.assertIn("别每轮都总结、升华", block)
        self.assertIn("只接半句", block)

    def test_ending_elevation_cleaned(self):
        """行为层也要配合：结尾升华会被裁掉。"""
        from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher

        p = ProsePolisher({"清AI八股": True})
        out, _, _ = p.polish("改一行就行。让我们一起努力，创造更美好的未来！")
        self.assertNotIn("让我们一起", out)
        self.assertIn("改一行就行", out)


class TestContinuity(unittest.TestCase):
    """维度4：状态连续性。"""

    def test_mood_reaches_private_reply(self):
        """私聊情绪写入用 uid，注入也必须用 uid，否则永远读不到。"""
        from astrbot_plugin_proactive_life.life_inject import build_life_state_block
        from astrbot_plugin_proactive_life.mood import MoodStore

        import time

        mood = MoodStore()
        mood.apply("100010001", "ignored", "被晾着", now=time.time())

        # 用 uid 作为 session_key（修复后的行为）
        block = build_life_state_block(
            {"日程_被动注入": True, "拟人_情绪注入被动": True},
            info={}, mood=mood, session_key="100010001",
        )
        self.assertIn("此刻心情", block)

        # 用 umo 作 key 时读不到（这正是修复前的 bug）
        block2 = build_life_state_block(
            {"日程_被动注入": True, "拟人_情绪注入被动": True},
            info={}, mood=mood, session_key="aiocqhttp:FriendMessage:100010001",
        )
        self.assertNotIn("此刻心情", block2)

    def test_main_uses_sender_id_for_private(self):
        """确认 main.py 私聊分支取的是 sender_id 而不是 umo。"""
        import inspect

        import astrbot_plugin_proactive_life.main as m

        src = inspect.getsource(m.ProactiveLifePlugin.on_life_state_llm_request)
        self.assertIn("get_sender_id()", src)


class TestPersonalitySliders(unittest.TestCase):
    """话多/沉默滑杆要真正作用于被动回复（此前只影响群聊主动开口）。"""

    def test_talk_low_gives_short(self):
        block = speech_style_block(_cfg(拟人_话多程度=10), "")
        self.assertIn("话少", block)

    def test_talk_high_allows_more(self):
        block = speech_style_block(_cfg(拟人_话多程度=90), "")
        self.assertIn("话可以多一点", block)

    def test_quiet_high_restrained(self):
        block = speech_style_block(_cfg(拟人_沉默倾向=90), "")
        self.assertIn("不硬凑", block)

    def test_gentle_mode_shorter(self):
        block = speech_style_block(_cfg(拟人_说话方式强度="gentle"), "")
        self.assertNotIn("不是待命工具", block)   # 温和模式不带完整四维度
        self.assertTrue(block.strip())


if __name__ == "__main__":
    unittest.main()
