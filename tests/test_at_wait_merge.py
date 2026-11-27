"""纯艾特挂起等待 + 指令回执过滤（v1.5.11）。

修的两个坑：
1. 群里一直有人聊天时上下文窗口永远非空，纯 @ 被别人的闲聊顶成情景 A，
   抢答一句空话，用户随后补的正文反而被当成普通群消息漏掉。
2. pending 是按群存的，别人随便说一句就把它吃掉并触发回复。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.api.message_components import At, Plain  # noqa: E402

from astrbot_plugin_proactive_life.main import ProactiveLifePlugin  # noqa: E402
from astrbot_plugin_proactive_life.modules.interact_context import (  # noqa: E402
    InteractContext,
    is_receipt_text,
)


class TestReceiptFilter(unittest.TestCase):
    """系统指令回执不该进上下文，否则纯 @ 会误判成「用户先说过话」。"""

    def test_receipt_texts(self):
        for text in (
            "/provider",
            "！new",
            "provider",
            "✅ Requested to stop 1 running tasks.",
            "✅ Switched to provider 28",
            "已切换至 xxx",
            "用法：/new 创建新会话",
        ):
            self.assertTrue(is_receipt_text(text), text)

    def test_normal_chat_kept(self):
        for text in (
            "今天天气怎么样",
            "这周的新番看了吗",
            "帮我把 help 那段删了",
            "",
        ):
            self.assertFalse(is_receipt_text(text), text)

    def test_observe_skips_receipt(self):
        ctx = InteractContext()
        ctx.observe("g1", "bot", "✅ Switched to provider 28", role="self")
        ctx.observe("g1", "u1", "/provider")
        self.assertEqual(ctx.recent("g1"), [])
        # 带图的消息不能因为文字像回执被丢掉
        ctx.observe("g1", "u1", "/new", ["x.png"])
        self.assertEqual(len(ctx.recent("g1")), 1)


class TestPendingSenderMatch(unittest.TestCase):
    def test_peek_does_not_consume(self):
        ctx = InteractContext()
        ctx.set_pending("g1", "at", "u1")
        self.assertIsNotNone(ctx.peek_pending("g1"))
        self.assertTrue(ctx.has_pending("g1"))
        self.assertIsNotNone(ctx.pop_pending("g1"))
        self.assertFalse(ctx.has_pending("g1"))

    def test_other_sender_cannot_take(self):
        ctx = InteractContext()
        ctx.set_pending("g1", "at", "u1")
        self.assertIsNone(ctx.take_pending_for("g1", "u2"))
        self.assertTrue(ctx.has_pending("g1"))
        self.assertIsNotNone(ctx.take_pending_for("g1", "u1"))
        self.assertFalse(ctx.has_pending("g1"))

    def test_expired_pending_dropped(self):
        ctx = InteractContext(ttl=20.0)
        ctx.set_pending("g1", "at", "u1")
        ctx._pending["g1"].at -= 100
        self.assertIsNone(ctx.take_pending_for("g1", "u1"))


class TestSameSenderScope(unittest.TestCase):
    """纯 @ 的「前面说过话」只认触发者本人，不算别人的闲聊。"""

    def test_same_sender_only(self):
        ctx = InteractContext()
        ctx.observe("g1", "u2", "别人在聊别的")
        self.assertEqual(ctx.same_sender("g1", "u1"), [])
        ctx.observe("g1", "u1", "我刚说的话")
        self.assertEqual(len(ctx.same_sender("g1", "u1")), 1)

    def test_relevant_includes_self(self):
        ctx = InteractContext()
        ctx.observe("g1", "u2", "别人的闲聊")
        ctx.observe("g1", "u1", "我说的")
        ctx.observe_self("g1", "我发的")
        texts = [it.text for it in ctx.relevant("g1", "u1")]
        self.assertEqual(texts, ["我说的", "我发的"])


def _make_plugin(wait_sec: float) -> ProactiveLifePlugin:
    plugin = object.__new__(ProactiveLifePlugin)
    plugin._stop_event = asyncio.Event()
    plugin.cfg = {
        "模块_戳一戳": True,
        "模块_上下文": False,
        "模块_黑名单": False,
        "戳一戳_纯艾特回应": True,
        "戳一戳_纯艾特概率": 1.0,
        "戳一戳_回应概率": 1.0,
        "戳一戳_后续合并": True,
        "戳一戳_纯艾特等待秒": wait_sec,
        "戳一戳_个人冷却秒": 0,
        "戳一戳_群冷却秒": 0,
    }
    plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
    plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑")
    plugin.interact_ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
    plugin.mood = None
    plugin.blacklist = SimpleNamespace(is_blacklisted_event=lambda _e: False)
    return plugin


def _pure_at_event(sender: str = "u1", group: str = "g1"):
    extras: dict = {}

    def request_llm(prompt: str = "", **_kw):
        return SimpleNamespace(prompt=prompt, image_urls=[])

    return SimpleNamespace(
        message_obj=None,
        message_str="",
        is_private_chat=lambda: False,
        get_self_id=lambda: "bot1",
        get_sender_id=lambda: sender,
        get_sender_name=lambda: "某人",
        get_group_id=lambda: group,
        get_messages=lambda: [At(qq="bot1")],
        is_admin=lambda: False,
        request_llm=request_llm,
        get_extra=lambda k, d=None: extras.get(k, d),
        set_extra=lambda k, v: extras.__setitem__(k, v),
    )


class TestPureAtWaitMerge(unittest.TestCase):
    def test_waits_then_greets_when_no_followup(self):
        plugin = _make_plugin(wait_sec=0.05)
        ev = _pure_at_event()

        async def run():
            out = []
            async for resp in plugin.on_poke_event(ev):
                out.append(resp)
            return out

        out = asyncio.run(run())
        self.assertEqual(len(out), 1)
        self.assertIn("自然打个招呼", out[0].prompt)
        self.assertFalse(plugin.interact_ctx.has_pending("g1"))

    def test_followup_during_wait_wins(self):
        """等待期间用户补了正文（pending 被 on_group_message 消费）→ 本次不能再回。"""
        plugin = _make_plugin(wait_sec=0.2)
        ev = _pure_at_event()

        async def run():
            async def consume():
                await asyncio.sleep(0.05)
                plugin.interact_ctx.observe("g1", "u1", "今天天气怎么样")
                return plugin.interact_ctx.take_pending_for("g1", "u1")

            task = asyncio.create_task(consume())
            out = []
            async for resp in plugin.on_poke_event(ev):
                out.append(resp)
            hit = await task
            return out, hit

        out, hit = asyncio.run(run())
        self.assertIsNotNone(hit)
        self.assertEqual(out, [])  # 挂起被消费，本次静默

    def test_other_sender_followup_does_not_win(self):
        """别人在等待期间说话，不能把纯 @ 的挂起吃掉。"""
        plugin = _make_plugin(wait_sec=0.2)
        ev = _pure_at_event()

        async def run():
            async def other():
                await asyncio.sleep(0.05)
                plugin.interact_ctx.observe("g1", "u2", "别人插一句")
                return plugin.interact_ctx.take_pending_for("g1", "u2")

            task = asyncio.create_task(other())
            out = []
            async for resp in plugin.on_poke_event(ev):
                out.append(resp)
            return out, await task

        out, taken = asyncio.run(run())
        self.assertIsNone(taken)
        self.assertEqual(len(out), 1)
        # 别人那句闲聊不该被当成本次纯 @ 的接话依据
        self.assertNotIn("别人插一句", out[0].prompt)

    def test_own_prior_message_answers_immediately(self):
        plugin = _make_plugin(wait_sec=0.5)
        plugin.interact_ctx.observe("g1", "u1", "我刚看完新番")
        ev = _pure_at_event()

        async def run():
            out = []
            async for resp in plugin.on_poke_event(ev):
                out.append(resp)
            return out

        out = asyncio.run(run())
        self.assertEqual(len(out), 1)
        self.assertIn("我刚看完新番", out[0].prompt)
        self.assertFalse(plugin.interact_ctx.has_pending("g1"))

    def test_wait_zero_keeps_old_behavior(self):
        plugin = _make_plugin(wait_sec=0)
        plugin.interact_ctx.observe("g1", "u2", "别人在聊")
        ev = _pure_at_event()

        async def run():
            out = []
            async for resp in plugin.on_poke_event(ev):
                out.append(resp)
            return out

        out = asyncio.run(run())
        self.assertEqual(len(out), 1)
        self.assertIn("别人在聊", out[0].prompt)


if __name__ == "__main__":
    unittest.main()
