"""戳一戳/纯艾特统一上下文测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.api.message_components import At, Image, Plain  # noqa: E402

from astrbot_plugin_proactive_life.modules.interact_context import (  # noqa: E402
    InteractContext, build_prompt, is_pure_at,
)


class TestPureAt(unittest.TestCase):
    def test_pure_at_detected(self):
        self.assertTrue(is_pure_at([At(qq="123")], bot_id="123"))
        self.assertTrue(is_pure_at([At(qq="all")], bot_id="123"))

    def test_with_text_not_pure(self):
        self.assertFalse(is_pure_at([At(qq="123"), Plain("你好")], bot_id="123"))

    def test_with_image_not_pure(self):
        self.assertFalse(is_pure_at([At(qq="123"), Image(file="x.png")], bot_id="123"))

    def test_at_others_not_pure(self):
        self.assertFalse(is_pure_at([At(qq="999")], bot_id="123"))


class TestInteractContext(unittest.TestCase):
    def test_scenario_a_previous_context(self):
        ctx = InteractContext()
        ctx.observe("g1", "u1", "刚看了个新番", ["img1.png"])
        items = ctx.recent("g1")
        prompt, images = build_prompt("poke", None, items)
        self.assertIn("刚看了个新番", prompt)
        self.assertEqual(images, ["img1.png"])

    def test_scenario_b_pending_followup(self):
        ctx = InteractContext()
        ctx.set_pending("g1", "at", "u1")
        self.assertTrue(ctx.has_pending("g1"))
        ctx.observe("g1", "u1", "补个图", ["img2.gif"])
        pending = ctx.pop_pending("g1")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.sender, "u1")
        prompt, images = build_prompt(pending.kind, pending, ctx.recent("g1"))
        self.assertIn("补个图", prompt)
        self.assertIn("img2.gif", images)
        # 再 pop 应为空
        self.assertIsNone(ctx.pop_pending("g1"))

    def test_dedup_images(self):
        ctx = InteractContext()
        ctx.observe("g1", "u1", "a", ["x.png"])
        ctx.observe("g1", "u1", "b", ["x.png", "y.gif"])
        _, images = build_prompt("poke", None, ctx.recent("g1"))
        self.assertEqual(images, ["x.png", "y.gif"])

    def test_expire(self):
        import time
        from astrbot_plugin_proactive_life.modules.interact_context import InteractItem

        ctx = InteractContext()
        # 塞一条 60 秒前的旧消息，超过默认 20s TTL
        from collections import deque
        ctx._recent["g1"] = deque([
            InteractItem(sender="u1", text="old", at=time.time() - 60)
        ])
        # 触发清理（observe 会清理过期项）
        ctx.observe("g1", "u2", "new")
        self.assertNotIn("old", [i.text for i in ctx.recent("g1")])


if __name__ == "__main__":
    unittest.main()
