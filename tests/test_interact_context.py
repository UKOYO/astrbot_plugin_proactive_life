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
    collect_recent_self_images,
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
        self.assertIn("- [刚刚] 小明：今天吃啥", block)
        self.assertIn("- [刚刚] 我（唯笑）：[表情包]", block)
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


class TestRelativeTimeAndHeaderHygiene(unittest.TestCase):
    """相对时间锚点 + header 不再举例（防止模型把例子当真实群动态）。"""

    def _items(self, now: float) -> list[InteractItem]:
        return [
            InteractItem(sender="u1", text="早上那个冷场", at=now - 3600 * 9, sender_name="小明"),
            InteractItem(
                sender="唯笑",
                text="[表情包]",
                at=now - 9,
                role="self",
                sender_name="唯笑",
                image_urls=["data:image/jpeg;base64,AAA"],
            ),
        ]

    def test_relative_time_rendered_per_item(self):
        now = time.time()
        block = build_recent_echo_block(
            self._items(now), bot_name="唯笑", now=now, max_items=6
        )
        self.assertIn("[9小时前] 小明：早上那个冷场", block)
        self.assertIn("[9秒前] 我（唯笑）：[表情包]", block)

    def test_relative_time_just_now_and_minutes(self):
        now = time.time()
        items = [
            InteractItem(sender="唯笑", text="刚发的", at=now - 1, role="self", sender_name="唯笑"),
            InteractItem(sender="唯笑", text="两分钟前", at=now - 120, role="self", sender_name="唯笑"),
        ]
        block = build_recent_echo_block(items, bot_name="唯笑", now=now)
        self.assertIn("[刚刚] 我（唯笑）：刚发的", block)
        self.assertIn("[2分钟前] 我（唯笑）：两分钟前", block)

    def test_header_contains_no_scenario_examples(self):
        now = time.time()
        block = build_recent_echo_block(self._items(now), bot_name="唯笑", now=now)
        # 这些词一旦出现在 header，低智模型会当成「真的发生过」的群动态
        for word in ("入群欢迎", "退群通知", "表情包制作", "欢迎我"):
            self.assertNotIn(word, block)
        self.assertIn("不要硬扯回很久以前的旧对话", block)

    def test_attached_images_note(self):
        now = time.time()
        block = build_recent_echo_block(
            self._items(now), bot_name="唯笑", now=now, has_attached_images=True
        )
        self.assertIn("附带的图片是上面标「我（唯笑）」刚发出的那张", block)
        # 不能再逼模型「直接结合图的内容来回应」——看图说话味儿太重
        self.assertNotIn("直接结合图的内容来回应", block)
        self.assertIn("仅作背景参考", block)
        without = build_recent_echo_block(self._items(now), bot_name="唯笑", now=now)
        self.assertNotIn("附带的图片", without)


class TestCollectRecentSelfImages(unittest.TestCase):
    def test_collects_only_self_data_images_newest_first(self):
        now = time.time()
        items = [
            InteractItem(
                sender="唯笑", text="[图片]", at=now - 30, role="self", sender_name="唯笑",
                image_urls=["data:image/jpeg;base64,OLD"],
            ),
            InteractItem(sender="u1", text="路人发的图", at=now - 20, image_urls=[
                "data:image/jpeg;base64,USER"
            ]),
            InteractItem(
                sender="唯笑", text="[表情包]", at=now - 5, role="self", sender_name="唯笑",
                image_urls=[
                    "data:image/jpeg;base64,NEW",
                    "https://x.test/remote.png",
                    "data:image/jpeg;base64,NEW",
                ],
            ),
        ]
        self.assertEqual(
            collect_recent_self_images(items),
            ["data:image/jpeg;base64,NEW", "data:image/jpeg;base64,OLD"],
        )
        self.assertEqual(
            collect_recent_self_images(items, max_images=1),
            ["data:image/jpeg;base64,NEW"],
        )
        self.assertEqual(collect_recent_self_images([]), [])


class TestUpdateLastSelf(unittest.TestCase):
    def test_upgrade_label_and_backfill_images(self):
        ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
        ctx.observe_self("g1", "[图片]", bot_name="唯笑")
        self.assertTrue(
            ctx.update_last_self(
                "g1", text="[表情包]", image_urls=["data:image/jpeg;base64,AAA"]
            )
        )
        self_items = [it for it in ctx.recent("g1") if it.is_self()]
        self.assertEqual(len(self_items), 1)  # 不产生重复条目
        self.assertEqual(self_items[0].text, "[表情包]")
        self.assertEqual(self_items[0].image_urls, ["data:image/jpeg;base64,AAA"])

    def test_less_specific_label_does_not_downgrade(self):
        ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
        ctx.observe_self("g1", "[表情包]", bot_name="唯笑")
        self.assertFalse(ctx.update_last_self("g1", text="[图片]"))
        self.assertEqual(ctx.recent("g1")[-1].text, "[表情包]")

    def test_no_self_item_returns_false(self):
        ctx = InteractContext()
        ctx.observe("g1", "u1", "只有群友说话")
        self.assertFalse(ctx.update_last_self("g1", text="[表情包]"))
        self.assertFalse(ctx.update_last_self("empty-group", text="x"))

    def test_semantic_label_upgrades_plain_sticker(self):
        """after_message_sent 后到的语义标签要能覆盖协议端 tap 的 [表情包]。"""
        ctx = InteractContext(window=6, ttl=20.0, self_ttl=90.0)
        ctx.observe_self("g1", "[表情包]", bot_name="唯笑")
        self.assertTrue(
            ctx.update_last_self("g1", text="[发送了表情包: 摸头 (动图/GIF)]")
        )
        self_items = [it for it in ctx.recent("g1") if it.is_self()]
        self.assertEqual(len(self_items), 1)
        self.assertEqual(self_items[0].text, "[发送了表情包: 摸头 (动图/GIF)]")
        # 反向：语义标签不会被更粗糙的占位覆盖
        self.assertFalse(ctx.update_last_self("g1", text="[表情包]"))
        self.assertEqual(ctx.recent("g1")[-1].text, "[发送了表情包: 摸头 (动图/GIF)]")


class TestMediaRankSharedRule(unittest.TestCase):
    """标签具体程度由 bot_echo 统一维护，interact_context 只复用，不另写一套。"""

    def test_rank_matches_bot_echo(self):
        from astrbot_plugin_proactive_life.modules import interact_context
        from astrbot_plugin_proactive_life.modules.bot_echo import media_rank

        for text in (
            "",
            "[图片]",
            "[表情包]",
            "[表情包 (动图/GIF)]",
            "[发送了表情包: 摸头]",
            "[发送了表情包: 摸头 (动图/GIF)]",
            "欢迎进群",
        ):
            self.assertEqual(interact_context._media_rank(text), media_rank(text), text)

    def test_fallback_table_when_shared_rule_unavailable(self):
        from astrbot_plugin_proactive_life.modules import interact_context

        saved = interact_context._shared_media_rank
        try:
            interact_context._shared_media_rank = None
            self.assertEqual(interact_context._media_rank("[图片]"), 1)
            self.assertEqual(interact_context._media_rank("[表情包]"), 2)
            self.assertEqual(interact_context._media_rank("[表情包 (动图/GIF)]"), 3)
            self.assertEqual(interact_context._media_rank("[发送了表情包: 摸头]"), 4)
            self.assertEqual(interact_context._media_rank("欢迎进群"), 0)
        finally:
            interact_context._shared_media_rank = saved


class TestInjectTempImages(unittest.TestCase):
    def test_injects_marked_temp_image_parts(self):
        from astrbot_plugin_proactive_life.life_inject import inject_temp_images

        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="")
        n = inject_temp_images(
            req,
            ["data:image/jpeg;base64,AAA", "data:image/jpeg;base64,AAA",
             "data:image/jpeg;base64,BBB"],
        )
        self.assertEqual(n, 2)
        urls = [p.image_url.url for p in req.extra_user_content_parts]
        self.assertEqual(urls, ["data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB"])
        # 不落盘：dump 后应被过滤掉，不进历史
        for p in req.extra_user_content_parts:
            self.assertTrue(getattr(p, "_no_save", False))

    def test_idempotent_and_never_touches_system_prompt(self):
        from astrbot_plugin_proactive_life.life_inject import inject_temp_images

        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="人设")
        self.assertEqual(inject_temp_images(req, ["data:image/jpeg;base64,AAA"]), 1)
        self.assertEqual(inject_temp_images(req, ["data:image/jpeg;base64,AAA"]), 0)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertEqual(req.system_prompt, "人设")

    def test_text_only_model_gets_no_image_part(self):
        from astrbot_plugin_proactive_life.life_inject import inject_temp_images

        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="")
        self.assertEqual(
            inject_temp_images(
                req, ["data:image/jpeg;base64,AAA"], modalities=["text", "tool_use"]
            ),
            0,
        )
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertEqual(
            inject_temp_images(
                req, ["data:image/jpeg;base64,AAA"], modalities=["text", "image"]
            ),
            1,
        )

    def test_no_parts_list_degrades_silently(self):
        from astrbot_plugin_proactive_life.life_inject import inject_temp_images

        self.assertEqual(inject_temp_images(SimpleNamespace(), ["data:image/jpeg;base64,A"]), 0)
        self.assertEqual(inject_temp_images(None, ["data:image/jpeg;base64,A"]), 0)
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="")
        self.assertEqual(inject_temp_images(req, []), 0)


def _stub_plugin(cfg: dict | None = None):
    """搭一个够用的插件实例（不跑真实平台），用于钩子顺序验证。"""
    from astrbot_plugin_proactive_life.main import ProactiveLifePlugin

    plugin = object.__new__(ProactiveLifePlugin)
    plugin._stop_event = asyncio.Event()
    plugin.cfg = {
        "模块_戳一戳": True,
        "戳一戳_收录自己发言": True,
        "戳一戳_上下文条数": 6,
        "戳一戳_上下文秒数": 20,
        **(cfg or {}),
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
    plugin.bot_echo = BotEchoLog(
        on_record=plugin._collect_self_speech,
        on_update=plugin._update_self_speech,
    )
    plugin.bot_echo_tap = BotSendTap(plugin.bot_echo.record)
    return plugin


class _Api:
    async def call_action(self, action: str, **params):
        return {"ok": True}


def _group_event(group: str = "88888", message: str = "hyw"):
    extras: dict = {}
    return SimpleNamespace(
        is_private_chat=lambda: False,
        get_group_id=lambda: group,
        get_sender_id=lambda: "10002",
        get_sender_name=lambda: "李四",
        message_str=message,
        unified_msg_origin=f"aiocqhttp:GroupMessage:{group}",
        get_extra=lambda k, d=None: extras.get(k, d),
        set_extra=lambda k, v: extras.__setitem__(k, v),
    )


class TestLifeStateInjectionOrder(unittest.TestCase):
    """【本群刚才的动态】必须排在【当前生活状态】之前，并带上自己刚发的图。"""

    def test_echo_block_before_life_state_with_image(self):
        import base64
        import io

        from PIL import Image

        from astrbot_plugin_proactive_life import life_inject

        plugin = _stub_plugin()
        bot = SimpleNamespace(api=_Api())
        plugin._ensure_echo_tap(SimpleNamespace(bot=bot))

        # 一张 3 帧 GIF 表情包（模拟 memelite 走协议端发出去的那张「波纹」）
        frames = [Image.new("RGB", (32, 24), c) for c in ((9, 9, 9), (99, 99, 99), (200, 200, 200))]
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=80)
        gif_b64 = "base64://" + base64.b64encode(buf.getvalue()).decode()

        async def run():
            await bot.api.call_action(
                "send_group_msg",
                group_id="88888",
                message=[{"type": "image", "data": {"file": gif_b64}}],
            )
            # 第二条路径（after_message_sent）把同一条报成 [图片]：只能合并成一条
            plugin.bot_echo.record("88888", "[图片]", media=True)
            self_items = [it for it in plugin.interact_ctx.recent("88888") if it.is_self()]
            self.assertEqual(len(self_items), 1)
            self.assertEqual(self_items[0].text, "[表情包]")
            self.assertEqual(len(self_items[0].image_urls), 1)

            orig = life_inject.read_schedule
            life_inject.read_schedule = lambda *a, **k: {
                "current_activity": "在窗边发呆",
                "outfit": "灰色卫衣",
                "schedule": "下午写代码",
            }
            try:
                req = SimpleNamespace(
                    prompt="hyw", extra_user_content_parts=[], system_prompt=""
                )
                await plugin.on_life_state_llm_request(_group_event(), req)
            finally:
                life_inject.read_schedule = orig

            parts = req.extra_user_content_parts
            texts = [getattr(p, "text", "") for p in parts]
            echo_idx = next(i for i, t in enumerate(texts) if RECENT_ECHO_MARKER in t)
            life_idx = next(i for i, t in enumerate(texts) if life_inject.MARKER in t)
            self.assertLess(echo_idx, life_idx, "刚发生的群动态必须排在生活状态之前")

            echo_text = texts[echo_idx]
            self.assertIn("我（唯笑）：[表情包]", echo_text)
            self.assertIn("附带的图片是上面标", echo_text)
            self.assertNotIn("入群欢迎", echo_text)

            image_urls = [
                p.image_url.url for p in parts if getattr(p, "image_url", None) is not None
            ]
            self.assertEqual(len(image_urls), 1)
            self.assertTrue(image_urls[0].startswith("data:image/jpeg;base64,"))

            plugin.bot_echo_tap.uninstall_all()

        asyncio.run(run())

    def test_no_image_part_when_nothing_attached(self):
        from astrbot_plugin_proactive_life import life_inject

        plugin = _stub_plugin()
        plugin.interact_ctx.observe_self("88888", "成员 张三 离开了群聊", bot_name="唯笑")
        orig = life_inject.read_schedule
        life_inject.read_schedule = lambda *a, **k: {"current_activity": "在窗边发呆"}
        try:
            req = SimpleNamespace(prompt="刚谁退群了", extra_user_content_parts=[], system_prompt="")
            asyncio.run(plugin.on_life_state_llm_request(_group_event(message="刚谁退群了"), req))
        finally:
            life_inject.read_schedule = orig
        self.assertTrue(
            any(RECENT_ECHO_MARKER in getattr(p, "text", "") for p in req.extra_user_content_parts)
        )
        self.assertFalse(
            any(getattr(p, "image_url", None) is not None for p in req.extra_user_content_parts)
        )


if __name__ == "__main__":
    unittest.main()
