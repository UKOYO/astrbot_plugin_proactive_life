"""机器人自己发言采集（BotEchoLog / BotSendTap / 摘要）单元测试。"""

from __future__ import annotations

import asyncio
import functools
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.api.message_components import At, Face, Image, Plain, Reply  # noqa: E402

from astrbot_plugin_proactive_life.modules.bot_echo import (  # noqa: E402
    BotEchoLog,
    BotSendTap,
    is_media_only_summary,
    summarize_components,
    summarize_onebot_segments,
)


class _FakeAsyncApi:
    """模拟 aiocqhttp 底层 AsyncApi（CQHttp._api）。"""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    async def call_action(self, action: str, **params):
        if self.fail_on and action == self.fail_on:
            raise RuntimeError("send failed")
        self.calls.append((action, dict(params)))
        return {"message_id": 1001}


class _FakeCQHttp:
    """模拟 aiocqhttp.CQHttp：bot.send_group_msg / bot.call_action / bot.api.call_action 全落到 _api。"""

    def __init__(self, fail_on: str = "") -> None:
        self._api = _FakeAsyncApi(fail_on=fail_on)

    @property
    def api(self):
        return self._api

    async def call_action(self, action: str, **params):
        return await self._api.call_action(action=action, **params)

    def __getattr__(self, item: str):
        return functools.partial(self.call_action, item)


class _SimpleBot:
    """没有 .api / ._api 的精简协议端 mock（验证降级挂 bot.call_action）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_action(self, action: str, **params):
        self.calls.append((action, dict(params)))
        return {"ok": True}


class TestSummarize(unittest.TestCase):
    def test_summarize_components_mixed_and_base64_gif(self):
        chain = [
            Reply(id="123"),
            At(qq="10001", name="小明"),
            Plain("  欢迎进群  "),
            Image(file="base64://R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"),
            Face(id=1),
        ]
        s = summarize_components(chain)
        self.assertEqual(s, "@小明 欢迎进群 [表情包] [表情]")
        self.assertFalse(is_media_only_summary(s))

    def test_summarize_components_pure_media(self):
        s = summarize_components([Image(file="a.webp")])
        self.assertEqual(s, "[表情包]")
        self.assertTrue(is_media_only_summary(s))

    def test_summarize_onebot_segments_list_and_dict(self):
        segs = [
            {"type": "reply", "data": {"id": "1"}},
            {"type": "text", "data": {"text": "摸摸头"}},
            {"type": "image", "data": {"file": "https://x.test/a.gif"}},
        ]
        self.assertEqual(summarize_onebot_segments(segs), "摸摸头 [表情包]")
        self.assertEqual(
            summarize_onebot_segments({"type": "image", "data": {"file": "x.png"}}),
            "[图片]",
        )

    def test_summarize_onebot_string_and_cq_code(self):
        self.assertEqual(summarize_onebot_segments("欢迎新人进群！"), "欢迎新人进群！")
        raw = "[CQ:at,qq=12345] 欢迎！[CQ:image,file=hello.gif][CQ:face,id=14]"
        self.assertEqual(
            summarize_onebot_segments(raw),
            "@12345 欢迎！ [表情包] [表情]",
        )

    def test_summarize_onebot_forward_nodes(self):
        nodes = [
            {
                "type": "node",
                "data": {"content": [{"type": "text", "data": {"text": "群日报摘要"}}]},
            }
        ]
        self.assertEqual(summarize_onebot_segments(nodes), "[合并转发:群日报摘要]")


class TestBotEchoLog(unittest.TestCase):
    def test_dedupe_and_media_merge(self):
        seen: list[tuple[str, str]] = []
        log = BotEchoLog(
            on_record=lambda g, t: seen.append((g, t)),
            dedupe_window=2.5,
            media_merge_window=8.0,
        )
        # 协议端 tap 和 after_message_sent 各报一次同一条 → 只记一次
        self.assertTrue(log.record("g1", "[表情包]", media=True))
        self.assertFalse(log.record("g1", "[表情包]", media=True))
        # 不同群不互相影响
        self.assertTrue(log.record("g2", "[表情包]", media=True))
        # 超过 dedupe_window 但仍在 media_merge_window 内，纯媒体同占位合并
        log._last["g1"].at = time.time() - 4.0
        self.assertFalse(log.record("g1", "[表情包]", media=True))
        # 新内容立即放行
        self.assertTrue(log.record("g1", "欢迎小明进群"))
        self.assertEqual(seen, [("g1", "[表情包]"), ("g2", "[表情包]"), ("g1", "欢迎小明进群")])

    def test_callback_error_isolated(self):
        def boom(_g: str, _t: str):
            raise RuntimeError("boom")

        log = BotEchoLog(on_record=boom)
        self.assertTrue(log.record("g1", "正常发言"))
        self.assertEqual(log.last("g1"), "正常发言")


class TestBotSendTap(unittest.TestCase):
    def test_cqhttp_all_send_paths_captured_once(self):
        async def run():
            captured: list[tuple[str, str, bool]] = []
            tap = BotSendTap(lambda g, t, media=False: captured.append((g, t, media)))
            bot = _FakeCQHttp()

            self.assertTrue(tap.install(bot))
            self.assertFalse(tap.install(bot))  # 幂等防重挂
            self.assertEqual(tap.installed, 1)

            # 路径 1：bot.send_group_msg（AstrBot 框架发送链路）
            await bot.send_group_msg(
                group_id=1001,
                message=[{"type": "image", "data": {"file": "base64://R0lGOD123"}}],
            )
            # 路径 2：bot.call_action（group_invite_auto_approve 入群欢迎/退群说明）
            await bot.call_action(
                "send_group_msg",
                group_id=1001,
                message=[{"type": "text", "data": {"text": "欢迎新成员入群！"}}],
            )
            # 路径 3：bot.api.call_action（wifepicker / qzone 直接走底层 api）
            await bot.api.call_action(
                "send_group_msg",
                group_id=1001,
                message="今天抽到了谁呢",
            )
            # 路径 4：send_group_forward_msg（使用 messages 复数参数）
            await bot.call_action(
                "send_group_forward_msg",
                group_id=1001,
                messages=[{"type": "node", "data": {"content": "合集内容"}}],
            )
            # 路径 5：私聊不采集
            await bot.send_private_msg(user_id=999, message="私聊内容")

            self.assertEqual(
                captured,
                [
                    ("1001", "[表情包]", True),
                    ("1001", "欢迎新成员入群！", False),
                    ("1001", "今天抽到了谁呢", False),
                    ("1001", "[合并转发:合集内容]", False),
                ],
            )

            # 卸载后不再采集，也不留残壳
            tap.uninstall_all()
            self.assertEqual(tap.installed, 0)
            await bot.send_group_msg(group_id=1001, message="卸载后的消息")
            self.assertEqual(len(captured), 4)

        asyncio.run(run())

    def test_failed_send_not_recorded(self):
        async def run():
            captured: list[tuple[str, str]] = []
            tap = BotSendTap(lambda g, t: captured.append((g, t)))
            bot = _FakeCQHttp(fail_on="send_group_msg")
            self.assertTrue(tap.install(bot))
            with self.assertRaises(RuntimeError):
                await bot.send_group_msg(group_id=1001, message="发送失败的消息")
            self.assertEqual(captured, [])
            tap.uninstall(bot)

        asyncio.run(run())

    def test_fallback_to_bot_call_action_without_api(self):
        async def run():
            captured: list[tuple[str, str]] = []
            tap = BotSendTap(lambda g, t: captured.append((g, t)))
            bot = _SimpleBot()
            self.assertTrue(tap.install(bot))
            await bot.call_action("send_msg", message_type="group", group_id=2002, message="有人退群了")
            self.assertEqual(captured, [("2002", "有人退群了")])
            tap.uninstall(bot)
            self.assertEqual(tap.installed, 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
