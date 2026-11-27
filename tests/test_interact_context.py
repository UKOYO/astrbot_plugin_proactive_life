"""戳一戳/艾特统一上下文测试（含机器人自己近期发言回溯与带字 @ 临时块注入）。"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.api.message_components import At, Image, Plain  # noqa: E402

from astrbot_plugin_proactive_life.modules.bot_echo import BotEchoLog, BotSendTap  # noqa: E402
from astrbot_plugin_proactive_life.modules.interact_context import (  # noqa: E402
    InteractContext,
    InteractItem,
    PendingTrigger,
    RECENT_ECHO_MARKER,
    build_prompt,
    build_recent_echo_block,
    is_pure_at,
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
        ctx = InteractContext()
        # 塞一条 60 秒前的旧消息，超过默认 20s TTL
        ctx._recent["g1"] = deque([
            InteractItem(sender="u1", text="old", at=time.time() - 60)
        ])
        # 触发清理（observe 会清理过期项）
        ctx.observe("g1", "u2", "new")
        self.assertNotIn("old", [i.text for i in ctx.recent("g1")])

    def test_observe_self_mixed_order_and_self_ttl(self):
        ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
        now = time.time()
        # 机器人 50 秒前发了入群欢迎（超过 user 的 20s，但在 self_ttl 90s 内）
        ctx._recent["g1"] = deque([
            InteractItem(
                sender="u0",
                text="40秒前的闲聊",
                at=now - 40,
                role="user",
                sender_name="路人甲",
            ),
            InteractItem(
                sender="唯笑",
                text="欢迎 @小新 加入本群！",
                at=now - 50,
                role="self",
                sender_name="唯笑",
            ),
        ])
        ctx.observe("g1", "u1", "摸摸头 @小新", sender_name="小明")
        ctx.observe_self("g1", "[表情包]", bot_name="唯笑")

        items = ctx.recent("g1")
        texts = [it.text for it in items]
        self.assertNotIn("40秒前的闲聊", texts)
        self.assertEqual(
            texts,
            ["欢迎 @小新 加入本群！", "摸摸头 @小新", "[表情包]"],
        )

        trigger = PendingTrigger(kind="poke", sender="u1", group="g1")
        prompt, _ = build_prompt("poke", trigger, items, bot_name="唯笑")
        self.assertIn("- 我（唯笑）：欢迎 @小新 加入本群！", prompt)
        self.assertIn("- 小明（同一个人）：摸摸头 @小新", prompt)
        self.assertIn("- 我（唯笑）：[表情包]", prompt)

    def test_build_recent_echo_block_only_when_self_spoke(self):
        ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
        ctx.observe("g1", "u1", "今天吃啥", sender_name="小明")
        # 机器人最近没说过话 → 返回空串，不给普通闲聊塞噪音
        self.assertEqual(
            build_recent_echo_block(ctx.recent("g1"), bot_name="唯笑"),
            "",
        )

        # 机器人通过表情包插件发了一张图，随后群友带字 @ 追问
        ctx.observe_self("g1", "[表情包]", bot_name="唯笑")
        ctx.observe("g1", "u2", "你发的这啥图啊", sender_name="小红")

        block = build_recent_echo_block(
            ctx.recent("g1"),
            bot_name="唯笑",
            current_prompt="你发的这啥图啊",
        )
        self.assertIn(RECENT_ECHO_MARKER, block)
        self.assertIn("- 小明：今天吃啥", block)
        self.assertIn("- 我（唯笑）：[表情包]", block)
        # 末尾与当前 prompt 重复的用户发言已被剔除
        self.assertNotIn("你发的这啥图啊", block)

    def test_end_to_end_plugin_echo_to_at_and_poke(self):
        """端到端：插件管线发图/入群欢迎 → 带字 @ 注入临时块 & 戳一戳不重复注入。"""
        from astrbot_plugin_proactive_life.main import ProactiveLifePlugin

        async def run():
            plugin = object.__new__(ProactiveLifePlugin)
            plugin._stop_event = asyncio.Event()
            plugin.cfg = {
                "模块_戳一戳": True,
                "模块_日程": False,  # 即使关掉日程模块，带字 @ 仍要注入刚才发过的动态
                "戳一戳_收录自己发言": True,
                "戳一戳_上下文条数": 6,
                "戳一戳_上下文秒数": 20,
            }
            plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
            plugin.group_h = SimpleNamespace(_bot_name=lambda: "唯笑")
            plugin.motive_mgr = SimpleNamespace(peek_latest_unused=lambda: "")
            plugin.person_memory = None
            plugin.mood = None
            plugin.context = SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[]),
                get_using_provider=lambda _u=None: None,
            )
            plugin.interact_ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
            plugin.bot_echo = BotEchoLog(on_record=plugin._collect_self_speech)
            plugin.bot_echo_tap = BotSendTap(plugin.bot_echo.record)

            # 模拟协议端（带 api.call_action）
            class _Api:
                async def call_action(self, action: str, **params):
                    return {"ok": True}

            bot = SimpleNamespace(api=_Api())
            plugin._ensure_echo_tap(SimpleNamespace(bot=bot))

            # 1) 某插件直接走 bot.api.call_action 发出退群通知
            await bot.api.call_action(
                "send_group_msg",
                group_id=88888,
                message="成员 张三 离开了群聊",
            )
            # 2) 带字 @ 触发 on_life_state_llm_request → 应注入【本群刚才的动态】
            extras: dict = {}
            ev = SimpleNamespace(
                is_private_chat=lambda: False,
                get_group_id=lambda: "88888",
                get_sender_id=lambda: "10002",
                get_sender_name=lambda: "李四",
                message_str="刚谁退群了",
                unified_msg_origin="aiocqhttp:GroupMessage:88888",
                get_extra=lambda k, d=None: extras.get(k, d),
                set_extra=lambda k, v: extras.__setitem__(k, v),
            )
            req = SimpleNamespace(
                prompt="刚谁退群了",
                extra_user_content_parts=[],
                system_prompt="",
            )
            await plugin.on_life_state_llm_request(ev, req)
            injected_texts = [
                p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
                for p in req.extra_user_content_parts
            ]
            joined = "\n".join(injected_texts)
            self.assertIn(RECENT_ECHO_MARKER, joined)
            self.assertIn("我（唯笑）：成员 张三 离开了群聊", joined)

            # 3) 如果是戳一戳/纯艾特触发（_poke_trigger=True），不重复注入临时块
            extras["_poke_trigger"] = True
            req2 = SimpleNamespace(
                prompt="刚被用户戳了一下...",
                extra_user_content_parts=[],
                system_prompt="",
            )
            await plugin.on_life_state_llm_request(ev, req2)
            injected_texts2 = [
                p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
                for p in req2.extra_user_content_parts
            ]
            self.assertNotIn(RECENT_ECHO_MARKER, "\n".join(injected_texts2))

            plugin.bot_echo_tap.uninstall_all()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
