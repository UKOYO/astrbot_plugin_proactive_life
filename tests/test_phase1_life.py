"""Phase1：生活状态注入（情绪/心事/总字数）+ 情绪轻量落盘回归测试。"""

from __future__ import annotations

import datetime
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.life_inject import (  # noqa: E402
    MARKER, build_life_state_block, inject_life_state,
)
from astrbot_plugin_proactive_life.mood import MoodStore  # noqa: E402


INFO = {
    "date": "2026-11-26", "outfit": "宽松卫衣",
    "current_activity": "摸鱼看番", "schedule": "08:00 起床\n15:00 摸鱼看番",
    "time_desc": "下午",
}


class TestLifeBlockExtra(unittest.TestCase):
    def test_mood_and_motive_included(self):
        mood = MoodStore()
        mood.apply("u1", "ignored", "被冷落", now=time.time())
        block = build_life_state_block(
            {"日程_被动注入": True, "拟人_情绪注入被动": True, "心事_被动注入": True},
            now=datetime.datetime(2026, 11, 26, 15, 30),
            info=INFO, mood=mood, motive="刚看到的新番", session_key="u1",
        )
        self.assertIn(MARKER, block)
        self.assertIn("此刻心情", block)
        self.assertIn("刚看到的新番", block)

    def test_switches_off(self):
        mood = MoodStore()
        mood.apply("u1", "ignored")
        cfg = {"日程_被动注入": True, "拟人_情绪注入被动": False, "心事_被动注入": False}
        block = build_life_state_block(cfg, info=INFO, mood=mood, motive="心事", session_key="u1")
        self.assertNotIn("此刻心情", block)
        self.assertNotIn("心事", block)

    def test_total_cap(self):
        cfg = {"日程_被动注入": True, "生活状态_总字数上限": 100}
        block = build_life_state_block(cfg, info=INFO, motive="很长的心事" * 40)
        self.assertLessEqual(len(block), 110)

    def test_inject_idempotent_and_temp(self):
        class Req:
            extra_user_content_parts = []
            system_prompt = "sys"

        req = Req()
        self.assertTrue(inject_life_state(req, "【当前生活状态】测试"))
        self.assertFalse(inject_life_state(req, "【当前生活状态】再来"))
        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        # TextPart.mark_as_temp 会带 _no_save；dict 降级也会带
        self.assertTrue(
            getattr(part, "_no_save", False) or
            (isinstance(part, dict) and part.get("_no_save"))
        )
        self.assertEqual(req.system_prompt, "sys")


class TestMoodPersistence(unittest.TestCase):
    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mood.json"
            m1 = MoodStore(persist_path=p, save_debounce_sec=0)
            m1.apply("u1", "ignored", "被冷落", now=time.time())
            self.assertTrue(p.is_file())
            m2 = MoodStore(persist_path=p, save_debounce_sec=0)
            self.assertEqual(m2.get("u1").name, m1.get("u1").name)
            self.assertGreater(m2.get("u1").intensity, 0)

    def test_decay_after_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mood.json"
            old = time.time() - 4 * 3600  # 4 小时前
            m1 = MoodStore(persist_path=p, save_debounce_sec=0)
            m1.apply("u1", "ignored", "", now=old)
            m2 = MoodStore(persist_path=p, save_debounce_sec=0)
            self.assertEqual(m2.get("u1").name, "平静")

    def test_no_path_is_pure_memory(self):
        m = MoodStore()
        m.apply("u1", "replied")
        m.save(force=True)  # 无 path 时应安全 no-op
        self.assertEqual(m.count(), 1)


if __name__ == "__main__":
    unittest.main()
