"""机器人自己发言采集（BotEchoLog / BotSendTap / 摘要）单元测试。"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.api.message_components import At, Face, Image, Plain, Reply  # noqa: E402

from astrbot_plugin_proactive_life.modules.bot_echo import (  # noqa: E402
    BotEchoLog,
    BotSendTap,
    extract_component_image_sources,
    extract_image_previews,
    extract_onebot_image_sources,
    extract_preview_data_url,
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


def _animated_gif_b64() -> str:
    """造一张 6 帧的动图 GIF（base64），模拟表情包插件发出去的东西。"""
    from PIL import Image

    frames = [
        Image.new("RGB", (40, 30), color)
        for color in (
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255),
        )
    ]
    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=100, loop=0,
    )
    return base64.b64encode(buf.getvalue()).decode()


def _png_b64(width: int = 1024, height: int = 768) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _open_data_url(data_url: str):
    from PIL import Image

    head, _, body = data_url.partition(",")
    assert head == "data:image/jpeg;base64", head
    return Image.open(io.BytesIO(base64.b64decode(body)))


class TestPreviewExtraction(unittest.TestCase):
    """出站图片 → 512px JPEG 预览（模型能「看见」自己刚发的图）。"""

    def test_animated_gif_base64_becomes_jpeg_preview(self):
        preview = extract_preview_data_url("base64://" + _animated_gif_b64())
        self.assertTrue(preview.startswith("data:image/jpeg;base64,"))
        with _open_data_url(preview) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.mode, "RGB")
            self.assertLessEqual(max(img.size), 512)

    def test_large_png_is_scaled_down(self):
        preview = extract_preview_data_url("data:image/png;base64," + _png_b64(1024, 768))
        self.assertTrue(preview.startswith("data:image/jpeg;base64,"))
        with _open_data_url(preview) as img:
            self.assertLessEqual(max(img.size), 512)
            # 长宽比保持不变
            self.assertAlmostEqual(img.size[0] / img.size[1], 1024 / 768, places=1)

    def test_oversized_source_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.png"
            path.write_bytes(b"\x00" * (9 * 1024 * 1024))
            self.assertEqual(extract_preview_data_url(str(path)), "")

    def test_local_path_and_file_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.png"
            path.write_bytes(base64.b64decode(_png_b64(80, 60)))
            self.assertTrue(extract_preview_data_url(str(path)).startswith("data:image/"))
            self.assertTrue(
                extract_preview_data_url(path.as_uri()).startswith("data:image/")
            )

    def test_non_image_and_remote_sources_are_ignored(self):
        self.assertEqual(extract_preview_data_url(""), "")
        self.assertEqual(extract_preview_data_url("这不是图片"), "")
        self.assertEqual(extract_preview_data_url("base64://不是base64"), "")
        # 远程图不拉：出站路径上不能等网络
        self.assertEqual(extract_preview_data_url("https://x.test/a.gif"), "")
        self.assertEqual(extract_preview_data_url("http://x.test/a.png"), "")

    def test_extract_image_previews_caps_and_dedupes(self):
        one = "base64://" + _animated_gif_b64()
        two = "data:image/png;base64," + _png_b64(64, 64)
        previews = extract_image_previews([one, one, two, "bad", one], max_images=2)
        self.assertEqual(len(previews), 2)
        self.assertNotEqual(previews[0], previews[1])

    def test_extract_onebot_image_sources(self):
        segs = [
            {"type": "text", "data": {"text": "看图"}},
            {"type": "image", "data": {"file": "base64://R0lGOD"}},
            {"type": "image", "data": {"url": "https://x.test/b.png"}},
            {
                "type": "node",
                "data": {"content": [{"type": "image", "data": {"file": "/tmp/c.gif"}}]},
            },
        ]
        self.assertEqual(
            extract_onebot_image_sources(segs),
            ["base64://R0lGOD", "https://x.test/b.png", "/tmp/c.gif"],
        )
        self.assertEqual(
            extract_onebot_image_sources("[CQ:image,file=hello.gif] 看"),
            ["hello.gif"],
        )
        self.assertEqual(extract_onebot_image_sources("纯文本"), [])
        self.assertEqual(extract_onebot_image_sources(None), [])

    def test_extract_component_image_sources(self):
        chain = [
            Plain("看图"),
            Image(file="base64://R0lGOD"),
            Face(id=1),
            Image(file="a.webp"),
        ]
        self.assertEqual(
            extract_component_image_sources(chain), ["base64://R0lGOD", "a.webp"]
        )
        self.assertEqual(extract_component_image_sources([]), [])


class TestCrossPathMediaDedupe(unittest.TestCase):
    """一条 GIF 表情包被两条路径分别报成 [图片] / [表情包] 时只能留一条。"""

    def test_picture_then_sticker_merges_into_one(self):
        seen: list[tuple[str, str]] = []
        updated: list[tuple[str, str, list[str]]] = []
        log = BotEchoLog(
            on_record=lambda g, t: seen.append((g, t)),
            on_update=lambda g, t, images=None: updated.append((g, t, list(images or []))),
        )
        # 路径 1：after_message_sent 报成 [图片]
        self.assertTrue(log.record("g1", "[图片]", media=True))
        # 路径 2：协议端 tap 毫秒内报成 [表情包]，还带了预览
        self.assertFalse(
            log.record("g1", "[表情包]", media=True, images=["data:image/jpeg;base64,AAA"])
        )
        self.assertEqual(seen, [("g1", "[图片]")])  # 只入队一条
        # 最后一条被升级成更具体的标签并补上了图片
        self.assertEqual(log.last("g1"), "[表情包]")
        self.assertEqual(log.last_images("g1"), ["data:image/jpeg;base64,AAA"])
        self.assertEqual(
            updated, [("g1", "[表情包]", ["data:image/jpeg;base64,AAA"])]
        )

    def test_images_backfilled_on_same_text(self):
        updated: list[tuple[str, str, list[str]]] = []
        log = BotEchoLog(
            on_record=lambda g, t: None,
            on_update=lambda g, t, images=None: updated.append((g, t, list(images or []))),
        )
        self.assertTrue(log.record("g1", "[表情包]", media=True))
        self.assertFalse(
            log.record("g1", "[表情包]", media=True, images=["data:image/jpeg;base64,BBB"])
        )
        self.assertEqual(log.last("g1"), "[表情包]")
        self.assertEqual(log.last_images("g1"), ["data:image/jpeg;base64,BBB"])
        self.assertEqual(updated, [("g1", "[表情包]", ["data:image/jpeg;base64,BBB"])])

    def test_distinct_media_messages_not_merged_after_window(self):
        log = BotEchoLog(dedupe_window=2.5)
        self.assertTrue(log.record("g1", "[图片]", media=True))
        log._last["g1"].at = time.time() - 5.0
        # 超出 dedupe_window 的 [表情包] 是另一条真实发送，要单独记
        self.assertTrue(log.record("g1", "[表情包]", media=True))

    def test_record_passes_images_only_when_callback_accepts(self):
        got: list[tuple] = []

        def with_images(group, text, images=None):
            got.append((group, text, images))

        log = BotEchoLog(on_record=with_images)
        log.record("g1", "[表情包]", media=True, images=["data:image/jpeg;base64,X"])
        self.assertEqual(got, [("g1", "[表情包]", ["data:image/jpeg;base64,X"])])

        old: list[tuple] = []
        legacy = BotEchoLog(on_record=lambda g, t: old.append((g, t)))
        legacy.record("g1", "[表情包]", media=True, images=["data:image/jpeg;base64,X"])
        self.assertEqual(old, [("g1", "[表情包]")])

    def test_tap_captures_gif_preview_for_echo_record(self):
        async def run():
            records: list[dict] = []
            log = BotEchoLog()

            def on_group(group, text, media=False, images=None):
                log.record(group, text, media=media, images=images)
                records.append({"text": text, "media": media, "images": images})

            tap = BotSendTap(on_group)
            bot = _FakeCQHttp()
            self.assertTrue(tap.install(bot))
            await bot.send_group_msg(
                group_id=1001,
                message=[{"type": "image", "data": {"file": "base64://" + _animated_gif_b64()}}],
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["text"], "[表情包]")
            self.assertTrue(records[0]["media"])
            self.assertEqual(len(records[0]["images"]), 1)
            self.assertTrue(records[0]["images"][0].startswith("data:image/jpeg;base64,"))
            tap.uninstall_all()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
