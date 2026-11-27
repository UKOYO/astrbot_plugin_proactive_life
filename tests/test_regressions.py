"""回归修复：钩子优先级、双回复互斥、复读截断、配置热更新、心事缓存。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestHookPriority(unittest.TestCase):
    """AstrBot 按 -priority 排序（数值大先执行），八股清洗必须早于节奏修剪。"""

    def test_polish_runs_before_reshape(self):
        from astrbot.core.star.star_handler import star_handlers_registry
        import astrbot_plugin_proactive_life.main  # noqa: F401  触发注册

        pri = {
            md.handler_name: md.extras_configs.get("priority")
            for md in star_handlers_registry
            if md.handler_name in ("on_outbound_polish", "reshape")
        }
        self.assertIn("on_outbound_polish", pri)
        self.assertIn("reshape", pri)
        # 数值越大越先执行：polish 必须大于 reshape
        self.assertGreater(
            pri["on_outbound_polish"], pri["reshape"],
            f"八股清洗({pri['on_outbound_polish']}) 必须先于节奏修剪({pri['reshape']})",
        )


class TestPokeNoDoubleReply(unittest.TestCase):
    """情景 A（有上下文立即回）与情景 B（无上下文等后续）必须互斥。"""

    def test_pending_only_when_no_context(self):
        from astrbot_plugin_proactive_life.modules.interact_context import InteractContext

        ctx = InteractContext()
        # 情景 B：没有任何上下文 -> 应该埋 pending，且本次不回复
        ctx.set_pending("g1", "poke", "u1")
        self.assertTrue(ctx.has_pending("g1"))
        self.assertEqual(ctx.recent("g1"), [])  # 没有上下文可带

        # 情景 A：有上下文 -> 不应该再埋 pending
        ctx2 = InteractContext()
        ctx2.observe("g2", "u2", "前面说的话")
        items = ctx2.recent("g2")
        self.assertTrue(items)
        self.assertFalse(ctx2.has_pending("g2"))


class TestRereadStopsEvent(unittest.TestCase):
    """复读命中后必须截断事件，避免同一条消息再触发插话。"""

    def test_source_calls_stop_event_after_yield(self):
        import inspect

        from astrbot_plugin_proactive_life.modules import interact_handler

        src = inspect.getsource(interact_handler.handle_group_reread)
        self.assertIn("chain_result", src)
        self.assertIn("event.stop_event()", src)
        # 复读 yield 之后必须截断事件
        self.assertGreater(src.index("event.stop_event()"), src.index("chain_result"))


class TestRuntimeConfigHotReload(unittest.TestCase):
    """面板改参数后，已创建的状态机要能同步，不用重载插件。"""

    def test_interact_context_update(self):
        from astrbot_plugin_proactive_life.modules.interact_context import InteractContext

        ctx = InteractContext(window=6, ttl=20)
        ctx.observe("g", "u", "a")
        ctx.update(window=3, ttl=45)
        self.assertEqual(ctx._window, 3)
        self.assertEqual(ctx._ttl, 45)

    def test_slang_update_config(self):
        from astrbot_plugin_proactive_life.modules.slang import SlangMiner

        m = SlangMiner(min_hits=3)
        m.update_config(min_hits=7)
        self.assertEqual(m._min_hits, 7)

    def test_reread_update_config(self):
        from astrbot_plugin_proactive_life.modules.reread import RereadState

        st = RereadState(threshold=2, min_interval=8)
        st.update_config(threshold=4, min_interval=2.5)
        self.assertEqual(st.threshold, 4)
        self.assertEqual(st.min_interval, 2.5)

    def test_poke_cooldown_update_config(self):
        from astrbot_plugin_proactive_life.modules.poke import PokeCooldown

        cd = PokeCooldown(30, 60)
        cd.update_config(personal_sec=5, group_sec=6)
        self.assertEqual(cd.personal, 5)
        self.assertEqual(cd.group, 6)


class TestMotivePeekCache(unittest.TestCase):
    """热路径读心事走带缓存的只读方法，避免每次读盘。"""

    def test_peek_returns_latest_unused(self):
        import tempfile

        from astrbot_plugin_proactive_life.motive_pool import MotiveManager

        with tempfile.TemporaryDirectory() as d:
            mgr = MotiveManager(Path(d) / "motive_pool.json")
            mgr.add_motive("第一条心事")
            mgr.add_motive("第二条心事")
            self.assertEqual(mgr.peek_latest_unused(), "第二条心事")

    def test_peek_skips_used(self):
        import tempfile

        from astrbot_plugin_proactive_life.motive_pool import MotiveManager

        with tempfile.TemporaryDirectory() as d:
            mgr = MotiveManager(Path(d) / "motive_pool.json")
            first = mgr.add_motive("旧的心事")
            mgr.add_motive("新的心事")
            mgr.mark_private_used(str(first.get("id")))
            self.assertEqual(mgr.peek_latest_unused(), "新的心事")

    def test_peek_empty_when_no_file(self):
        import tempfile

        from astrbot_plugin_proactive_life.motive_pool import MotiveManager

        with tempfile.TemporaryDirectory() as d:
            mgr = MotiveManager(Path(d) / "not_exist.json")
            self.assertEqual(mgr.peek_latest_unused(), "")


class TestConfigDefaultsExtracted(unittest.TestCase):
    """DEFAULT_CONFIG 已抽到 config_defaults.py，且与 schema 严格一致。"""

    def test_schema_and_default_config_aligned(self):
        import json

        from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG

        base = Path(__file__).resolve().parent.parent
        schema = json.loads((base / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema), set(DEFAULT_CONFIG))
        for key, spec in schema.items():
            self.assertEqual(
                spec.get("default"), DEFAULT_CONFIG[key],
                f"{key} 的 schema 默认值与 DEFAULT_CONFIG 不一致",
            )


if __name__ == "__main__":
    unittest.main()
