"""Phase3：天气降级 / 群黑话 / 冲突检测回归测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.slang import SlangMiner  # noqa: E402
from astrbot_plugin_proactive_life.modules.weather import get_weather  # noqa: E402
from astrbot_plugin_proactive_life import schedule_gen  # noqa: E402


class TestSlang(unittest.TestCase):
    def test_mine_and_hint(self):
        m = SlangMiner(min_hits=3)
        for _ in range(4):
            m.observe("g1", "今天好耶 好耶 好耶")
        words = m.top("g1")
        self.assertIn("好耶", words)
        cfg = {"模块_黑话": True, "黑话_最多条数": 4, "黑话_只注入已解析": True}
        # 新逻辑：严格模式下未解析的词不注入
        self.assertEqual(m.hint("g1", cfg), "")
        # 解析确认后注入
        m.set_analysis([{"word": "好耶", "meaning": "开心", "is_slang": True}])
        hint = m.hint("g1", cfg)
        self.assertIn("好耶", hint)
        self.assertEqual(m.hint("g1", {"模块_黑话": False}), "")

    def test_blocked_words_not_observed(self):
        m = SlangMiner(min_hits=2)
        m.observe("g2", "")
        self.assertEqual(m.top("g2"), [])


class TestWeatherFallback(unittest.TestCase):
    def test_offline_returns_empty(self):
        import astrbot_plugin_proactive_life.modules.weather as w

        old = w.urllib.request.urlopen

        def fail(*a, **k):
            raise OSError("offline")

        w.urllib.request.urlopen = fail
        try:
            result = get_weather("不存在的城市XYZ")
            self.assertIsInstance(result, dict)
        finally:
            w.urllib.request.urlopen = old

    def test_schedule_weather_placeholder_safe(self):
        cfg = {"模块_天气": False}
        g = schedule_gen.ScheduleGenerator.__new__(schedule_gen.ScheduleGenerator)
        g.cfg = cfg
        self.assertEqual(g._weather_text(), "")


class TestConflictDetection(unittest.TestCase):
    def test_merged_plugins_detected(self):
        from astrbot_plugin_proactive_life.panel_status import _conflicts_block

        class FakeCtx:
            def get_registered_star(self, name):
                if name in ("astrbot_plugin_global_blacklist", "astrbot_plugin_reread"):
                    return SimpleNamespace(activated=True)
                return None

        class FakePlugin:
            context = FakeCtx()
            cfg = {"日程_自动生成": False, "场景感知_让位contextaware": True}
            def _module_on(self, name):
                return True

        conflicts = _conflicts_block(FakePlugin())
        texts = " ".join(c["text"] for c in conflicts)
        self.assertIn("global_blacklist", texts)
        self.assertIn("reread", texts)


if __name__ == "__main__":
    unittest.main()
