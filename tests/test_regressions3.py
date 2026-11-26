"""回归修复（第三批）：追问冷却/黑名单、摸鱼情绪目标、MCP 越权、umo、气泡顺序、落盘节流。"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestNudgeCooldown(unittest.TestCase):
    """多次追问要按「上次追问时间」算冷却，不能连发。"""

    def test_uses_last_nudged_at(self):
        import astrbot_plugin_proactive_life.private_handler as ph

        src = inspect.getsource(ph.PrivateProactiveHandler.check_and_nudge)
        self.assertIn("last_nudged_at", src)
        # 已追问过时以 last_nudged_at 为基准
        self.assertIn("nudged_count > 0", src)


class TestNudgeBlacklist(unittest.TestCase):
    """追问目标和主动开口一样要过黑名单。"""

    def test_nudge_checks_blacklist(self):
        import astrbot_plugin_proactive_life.private_handler as ph

        src = inspect.getsource(ph.PrivateProactiveHandler.check_and_nudge)
        self.assertIn("is_target_blocked", src)
        # 必须在取 inquiry 之前就拦住
        self.assertLess(src.index("is_target_blocked"), src.index("pending_inquiries"))


class TestWorkerMoodTarget(unittest.TestCase):
    """摸鱼情绪不能写进 system_worker 死键。"""

    def test_mood_written_to_real_target(self):
        import astrbot_plugin_proactive_life.worker_handler as wh

        src = inspect.getsource(wh.AutonomousWorkerHandler._run_work)
        self.assertNotIn('_mood_apply(_WORKER_VIRTUAL_UID', src)
        self.assertIn("_mood_target_keys", src)

    def test_mood_target_keys_reads_config(self):
        import astrbot_plugin_proactive_life.worker_handler as wh

        class Fake(wh.AutonomousWorkerHandler):
            def __init__(self):
                self.cfg = {"对谁主动": ["123456"]}

        self.assertEqual(Fake()._mood_target_keys(), ["123456"])


class TestWorkerToolsetSafety(unittest.TestCase):
    """MCP 工具不能全量放行，危险能力必须挡住。"""

    def test_dangerous_keywords_defined(self):
        import astrbot_plugin_proactive_life.worker_handler as wh

        kw = wh._DANGEROUS_TOOL_KW
        for word in ("shell", "exec", "write", "delete", "file", "cmd"):
            self.assertIn(word, kw)

    def test_mcp_tool_filtered_by_keyword(self):
        import astrbot_plugin_proactive_life.worker_handler as wh

        src = inspect.getsource(wh.AutonomousWorkerHandler._build_worker_toolset)
        self.assertIn("_DANGEROUS_TOOL_KW", src)
        # 过滤必须在 add_tool 之前
        self.assertLess(
            src.index("_DANGEROUS_TOOL_KW"), src.index("tools.add_tool(tool)")
        )

    def test_umo_passed_as_full_string(self):
        import astrbot_plugin_proactive_life.worker_handler as wh

        src = inspect.getsource(wh.AutonomousWorkerHandler._run_work)
        self.assertNotIn("session.session_id, tool_call_timeout", src)
        self.assertIn("str(session)", src)


class TestRhythmBubbleOrder(unittest.TestCase):
    """补发首条要有保底延迟，且不能留空 Plain 组件。"""

    def test_first_segment_min_gap(self):
        import astrbot_plugin_proactive_life.rhythm_mixin as rm

        self.assertGreaterEqual(rm._FIRST_SEG_MIN_GAP, 0.5)
        src = inspect.getsource(rm.RhythmMixin._rhythm_send_rest)
        self.assertIn("_FIRST_SEG_MIN_GAP", src)

    def test_no_empty_plain_left_in_chain(self):
        import astrbot_plugin_proactive_life.rhythm_mixin as rm

        src = inspect.getsource(rm.RhythmMixin.reshape)
        self.assertIn("del result.chain[i]", src)


class TestStateSaveThrottle(unittest.TestCase):
    """私聊状态写盘要节流，并能 flush。"""

    def test_throttle_and_flush(self):
        import tempfile

        from astrbot_plugin_proactive_life.private_handler import PrivateProactiveHandler

        with tempfile.TemporaryDirectory() as d:
            h = PrivateProactiveHandler.__new__(PrivateProactiveHandler)
            h.state_file = Path(d) / "state.json"
            h.state = {"last_user_msg": {}, "last_active": {}, "daily": {},
                       "pending_inquiries": {}}
            h._last_save_at = 0.0
            h._state_dirty = False
            h._save_state()              # 首次落盘
            self.assertTrue(h.state_file.is_file())
            h._save_state()              # 立刻再存 -> 被节流
            self.assertTrue(h._state_dirty)
            h.flush_state()              # 强制写出
            self.assertFalse(h._state_dirty)


class TestScheduleGenFixes(unittest.TestCase):
    """schedule_gen：去掉重复判断，LLM 调用有超时。"""

    def test_no_duplicate_isinstance(self):
        import astrbot_plugin_proactive_life.schedule_gen as sg

        src = inspect.getsource(sg.ScheduleStore.load)
        self.assertEqual(src.count("if not isinstance(raw, dict):"), 1)

    def test_llm_call_has_timeout(self):
        import astrbot_plugin_proactive_life.schedule_gen as sg

        src = inspect.getsource(sg.ScheduleGenerator._call_llm)
        self.assertIn("asyncio.wait_for", src)
        self.assertIn("超时", src)


if __name__ == "__main__":
    unittest.main()
