"""纯逻辑模块单元测试：情绪、日程时间、身份、命中词、文本工具。"""

from __future__ import annotations

import asyncio
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.mood import (  # noqa: E402
    CALM, HAPPY, WRONGED, MoodStore,
)
from astrbot_plugin_proactive_life.common import (  # noqa: E402
    is_llm_error_text, parse_quiet_hours, read_schedule,
)
from astrbot_plugin_proactive_life.schedule_gen import extract_json_obj  # noqa: E402
from astrbot_plugin_proactive_life.identity import (  # noqa: E402
    Identity, from_config, read_astrbot_persona, synthesize_plugin_prompt,
)
from astrbot_plugin_proactive_life.rhythm_trimmer import (  # noqa: E402
    split_message, strip_trailing_punct,
)


class TestMood(unittest.TestCase):
    def test_decay_to_calm(self):
        s = MoodStore(decay_hours=3.0)
        t0 = 1_000_000.0
        s.apply("u", "ignored", "test", t0)
        self.assertEqual(s.get("u", t0).name, WRONGED)
        self.assertLess(s.get("u", t0 + 5400).intensity, s.get("u", t0).intensity)
        self.assertEqual(s.get("u", t0 + 14400).name, CALM)

    def test_same_event_stack_capped(self):
        s = MoodStore()
        t0 = 1_000_000.0
        s.apply("u", "ignored", "", t0)
        s.apply("u", "ignored", "", t0 + 10)
        self.assertLessEqual(s.get("u", t0 + 10).intensity, 0.9)

    def test_hint_present(self):
        s = MoodStore()
        s.apply("u", "ignored")
        # 情绪提示只陈述内在状态，不再带"带点别扭"这类表演指令
        self.assertIn("在意", s.hint("u"))
        self.assertEqual(s.hint("nobody"), "")

    def test_prune_bound(self):
        # MoodStore 内部下限为 10，测试 50 个不同 key 应被 prune 到 10
        s = MoodStore(max_keys=10)
        for i in range(50):
            s.apply(f"u{i}", "replied")
        self.assertLessEqual(s.count(), 10)


class TestCommon(unittest.TestCase):
    def test_llm_error_markers(self):
        self.assertTrue(is_llm_error_text("All Chat Models Failed"))
        self.assertTrue(is_llm_error_text("the requested resource was not found"))
        self.assertFalse(is_llm_error_text("今天天气不错"))

    def test_quiet_hours(self):
        self.assertEqual(parse_quiet_hours([2, 3, "4", "x", 99]), [2, 3, 4])
        self.assertEqual(parse_quiet_hours(None), [2, 3, 4, 5, 6])
        self.assertEqual(parse_quiet_hours(5), [5])

    def test_business_date_boundary(self):
        from astrbot_plugin_proactive_life.common import _business_date

        now = datetime.datetime(2026, 11, 24, 6, 30)
        self.assertEqual(_business_date("07:00", now), "2026-11-23")
        self.assertEqual(_business_date("07:00", now.replace(hour=7)), "2026-11-24")

    def test_current_activity(self):
        reader_type = read_schedule.__globals__["ScheduleReader"]
        schedule = "08:00 起床\n12:30 吃午饭\n15:00 摸鱼看番\n20:00 打游戏"
        now = datetime.datetime(2026, 11, 24, 16, 0)
        self.assertEqual(
            reader_type._current_activity(schedule, now), "摸鱼看番"
        )

    def test_extract_json(self):
        raw = '```json\n{"outfit_style":"休闲风","outfit":"风格：休闲风","schedule":"08:00 起床"}\n```'
        data = extract_json_obj(raw)
        self.assertIsNotNone(data)
        self.assertEqual(data["outfit_style"], "休闲风")
        self.assertIsNone(extract_json_obj("没有 JSON"))


class TestIdentity(unittest.IsolatedAsyncioTestCase):
    def test_neutral_defaults(self):
        i = from_config({})
        self.assertEqual(i.bot_name, "")
        self.assertEqual(i.user_name, "")
        self.assertEqual(i.all_names, [])
        self.assertEqual(i.persona_block(), "")

    def test_custom_identity(self):
        i = from_config({
            "角色_Bot自称": "小助手",
            "角色_备用称呼": ["小助", "助手"],
            "角色_兴趣关键词": "音乐, 摄影",
            "角色_生活背景": "喜欢散步",
        }, "原始人设")
        self.assertEqual(i.all_names, ["小助手", "小助", "助手"])
        self.assertEqual(i.interests, ["音乐", "摄影"])
        self.assertIn("原始人设", i.persona_block())
        self.assertIn("喜欢散步", i.persona_block())

    def test_synth_prompt_priority(self):
        i = from_config({"角色_插件专用提示词": "专用版", "角色_额外人设": "补充"})
        i.persona_text = "原版"
        self.assertIn("专用版", i.persona_block())
        self.assertNotIn("原版", i.persona_block())

    async def test_read_persona_by_id(self):
        class P:
            def __init__(self, pid, sp):
                self.persona_id = pid
                self.system_prompt = sp

        class Mgr:
            async def get_all_personas(self):
                return [P("甲", "甲的人设"), P("乙", "")]

            def get_persona_v3_by_id(self, pid):
                return {"name": pid, "prompt": {"甲": "甲的人设", "乙": ""}.get(pid, "")}

            def get_default_persona_v3(self):
                async def d():
                    return {"prompt": "默认人设"}
                return d()

            personas_v3 = [{"name": "甲", "prompt": "甲的人设"}, {"name": "乙", "prompt": ""}]

        class Ctx:
            persona_manager = Mgr()

        self.assertEqual(await read_astrbot_persona(Ctx()), "默认人设")
        self.assertEqual(
            await asyncio.wait_for(
                __import__("astrbot_plugin_proactive_life.identity", fromlist=["read_persona_by_id"]).read_persona_by_id(Ctx(), "甲"),
                1,
            ),
            "甲的人设",
        )
        # 空人设自动回退默认
        self.assertEqual(
            await __import__("astrbot_plugin_proactive_life.identity", fromlist=["read_persona_by_id"]).read_persona_by_id(Ctx(), "乙"),
            "默认人设",
        )


class TestTextTools(unittest.TestCase):
    def test_strip_trailing_punct(self):
        # strip_trailing_punct 返回 (文本, 去掉的标点数)
        text, removed = strip_trailing_punct("你好。")
        self.assertEqual(text, "你好")
        self.assertEqual(removed, 1)

    def test_split_message(self):
        segs = split_message("这是一句很短的话。" * 20, target=18, max_segments=3)
        # split_message 的 max_segments 是软上限，至少 1 段
        self.assertGreaterEqual(len(segs), 1)
        self.assertGreaterEqual(len(segs), 1)
        self.assertTrue(all(isinstance(s, str) for s in segs))


if __name__ == "__main__":
    unittest.main()
