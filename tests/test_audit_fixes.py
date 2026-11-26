"""审计修复回归测试：覆盖实机排查发现的 5 个问题。"""

from __future__ import annotations

import datetime
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.common import ScheduleReader  # noqa: E402
from astrbot_plugin_proactive_life.life_inject import inject_temp_part  # noqa: E402
from astrbot_plugin_proactive_life.modules.person_memory import PersonMemory  # noqa: E402
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402
from astrbot_plugin_proactive_life.modules.speech_style import (  # noqa: E402
    MARKER as SPEECH_MARKER,
    has_speech_marker,
    speech_style_block,
)


class TestSpeechStyleFixes(unittest.TestCase):
    def test_marker_present_and_idempotent(self):
        for mode in ("strict", "gentle"):
            block = speech_style_block({"拟人_说话方式约束": True, "拟人_说话方式强度": mode}, "gemini-3")
            self.assertIn(SPEECH_MARKER, block)
            req = SimpleNamespace(extra_user_content_parts=[])
            self.assertTrue(inject_temp_part(req, block, SPEECH_MARKER))
            self.assertTrue(has_speech_marker(req.extra_user_content_parts))
            # 同轮重入必须幂等跳过
            self.assertFalse(inject_temp_part(req, block, SPEECH_MARKER))
            self.assertEqual(len(req.extra_user_content_parts), 1)

    def test_technical_exemption_present(self):
        for mode in ("strict", "gentle"):
            block = speech_style_block({"拟人_说话方式约束": True, "拟人_说话方式强度": mode}, "")
            self.assertIn("排障", block)


class TestPersonMemoryFixes(unittest.TestCase):
    def test_current_message_not_echoed_as_last_topic(self):
        pm = PersonMemory()
        pm.touch("u1", "昨天聊了科二练车", name="keyou", scope="private")
        # 模拟同轮 on_private_message(9000) 先写入当前句
        pm.touch("u1", "你优化下", name="keyou", scope="private")
        block = pm.block("u1", scope="private", name="keyou", current_text="你优化下")
        self.assertIn("昨天聊了科二练车", block)
        self.assertNotIn("你优化下", block)

    def test_first_message_has_no_fake_last_topic(self):
        pm = PersonMemory()
        pm.touch("u2", "你好呀", name="newbie", scope="private")
        block = pm.block("u2", scope="private", name="newbie", current_text="你好呀")
        self.assertNotIn("上次说到", block)

    def test_warmth_decays_after_7_days(self):
        pm = PersonMemory()
        p = pm.touch("u3", "嗨", name="old", scope="group")
        p.warmth = 0.8
        p.updated = time.time() - 8 * 86400
        pm.touch("u3", "回来了", name="old", scope="group")
        self.assertLess(p.warmth, 0.8)


class TestScheduleNightActivityFix(unittest.TestCase):
    def test_late_night_before_first_item_returns_empty(self):
        schedule = "10:30 睡醒换上宽松长裙\n12:30 吃午饭\n23:30 钻进被窝躺平"
        late_night = datetime.datetime(2026, 11, 27, 2, 19)
        self.assertEqual(ScheduleReader._current_activity(schedule, late_night), "")

    def test_within_schedule_still_matches(self):
        schedule = "10:30 睡醒换上宽松长裙\n12:30 吃午饭\n23:30 钻进被窝躺平"
        noon = datetime.datetime(2026, 11, 27, 11, 0)
        self.assertEqual(ScheduleReader._current_activity(schedule, noon), "睡醒换上宽松长裙")


class TestOrphanTailCommaFix(unittest.TestCase):
    def test_removes_trailing_orphan_comma(self):
        p = ProsePolisher({"清AI八股": True})
        out, _, _ = p.polish("哈哈这个词你没听过吗，")
        self.assertFalse(out.endswith("，"))
        self.assertEqual(out, "哈哈这个词你没听过吗")

    def test_removes_trailing_comma_after_ending_trimmed(self):
        p = ProsePolisher({"清AI八股": True, "删结尾升华": True})
        out, _, _ = p.polish("不是你今天怎么了，\n让我们一起努力，创造更美好的未来！")
        self.assertFalse(out.endswith("，"))


if __name__ == "__main__":
    unittest.main()
