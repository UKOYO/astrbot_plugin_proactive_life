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
    STICKER_SEMANTIC_PREFIX,
    detect_meme_keyword,
    detect_meme_source,
    extract_component_image_sources,
    extract_image_previews,
    extract_onebot_image_sources,
    extract_preview_data_url,
    is_media_only_summary,
    is_sticker_summary,
    media_rank,
    refine_self_media_summary,
    sticker_label,
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


class _FakeEvent:
    """模拟 AstrBot 事件：``get_extra()`` 无参返回整份 dict，另有 ``message_str``。"""

    def __init__(self, message_str: str = "", extras: dict | None = None) -> None:
        self.message_str = message_str
        self._extras = dict(extras or {})

    def get_extra(self, key=None, default=None):
        if key is None:
            return dict(self._extras)
        return self._extras.get(key, default)


class _LegacyEvent:
    """老版本事件：``get_extra`` 必须带 key，走 ``_extras`` 兜底路径。"""

    def __init__(self, message_str: str = "", extras: dict | None = None) -> None:
        self.message_str = message_str
        self._extras = dict(extras or {})

    def get_extra(self, key):
        return self._extras.get(key)


class _MemeHandler:
    """模拟 memelite 插件命中的处理器（AstrBot 只挂路径/名字属性）。"""

    def __init__(self, path: str, full_name: str = "") -> None:
        self.handler_module_path = path
        self.handler_full_name = full_name


class TestStickerLabel(unittest.TestCase):
    """语义化表情包标签：``[发送了表情包: 摸头 (动图/GIF)]``。"""

    def test_label_variants(self):
        self.assertEqual(sticker_label("摸头", animated=True), "[发送了表情包: 摸头 (动图/GIF)]")
        self.assertEqual(sticker_label("摸头"), "[发送了表情包: 摸头]")
        self.assertEqual(sticker_label(animated=True), "[表情包 (动图/GIF)]")
        self.assertEqual(sticker_label(), "[表情包]")
        # 关键词收窄：压空白、去首尾标点、最长 16 字
        self.assertEqual(sticker_label("  摸 头 :："), "[发送了表情包: 摸 头]")
        self.assertEqual(sticker_label("一" * 40), f"[发送了表情包: {'一' * 16}]")
        self.assertTrue(sticker_label("摸头").startswith(STICKER_SEMANTIC_PREFIX))

    def test_media_rank_order(self):
        self.assertEqual(media_rank(""), 0)
        self.assertEqual(media_rank("今天吃了面"), 0)
        self.assertEqual(media_rank("[图片]"), 1)
        self.assertEqual(media_rank("[表情包]"), 2)
        self.assertEqual(media_rank("[表情包 (动图/GIF)]"), 3)
        self.assertEqual(media_rank("[发送了表情包: 摸头 (动图/GIF)]"), 4)
        self.assertGreater(
            media_rank("[发送了表情包: 摸头]"), media_rank("[表情包 (动图/GIF)]")
        )

    def test_is_sticker_summary(self):
        self.assertTrue(is_sticker_summary("[表情包]"))
        self.assertTrue(is_sticker_summary("[表情包 (动图/GIF)]"))
        self.assertTrue(is_sticker_summary("[发送了表情包: 摸头]"))
        self.assertFalse(is_sticker_summary("[图片]"))
        self.assertFalse(is_sticker_summary("哈哈哈"))
        self.assertFalse(is_sticker_summary(""))

    def test_media_only_summary_accepts_semantic_labels(self):
        self.assertTrue(is_media_only_summary("[发送了表情包: 摸头 (动图/GIF)]"))
        self.assertTrue(is_media_only_summary("[表情包 (动图/GIF)]"))
        self.assertTrue(is_media_only_summary("[表情包 (动图/GIF)] [图片]"))
        # 带正文就不是纯媒体了，别把整句话吞掉
        self.assertFalse(is_media_only_summary("[发送了表情包: 摸头] 哈哈哈"))
        self.assertFalse(is_media_only_summary("[图片] 看这个"))


class TestMemeDetection(unittest.TestCase):
    """来源/关键词探测：靠事件命中的插件处理器、附加信息、图片本地路径。"""

    def test_source_from_activated_handlers(self):
        event = _FakeEvent(
            extras={"activated_handlers": [_MemeHandler("data.plugins.astrbot_plugin_memelite.main")]}
        )
        self.assertEqual(detect_meme_source(event), "memelite")
        # 老事件（get_extra 必须带 key）也能从 _extras 读到
        legacy = _LegacyEvent(
            extras={"activated_handlers": [_MemeHandler("plugins/astrbot_plugin_memelite/main.py")]}
        )
        self.assertEqual(detect_meme_source(legacy), "memelite")

    def test_source_from_extras_and_image_path(self):
        self.assertTrue(detect_meme_source(_FakeEvent(extras={"meme_keyword": "摸头"})))
        chain = [Image(file="/data/memes/摸头.gif")]
        self.assertTrue(detect_meme_source(None, chain))
        # 普通相册图不认
        self.assertEqual(detect_meme_source(_FakeEvent(), [Image(file="a.png")]), "")

    def test_source_ignores_inline_base64(self):
        # 内联 base64 是噪音，不能因为它里面恰好有字节就误判来源
        chain = [Image(file="base64://iVBORw0KGgoAAAANSUhEUg")]
        self.assertEqual(detect_meme_source(_FakeEvent(), chain), "")
        self.assertEqual(detect_meme_source(None), "")

    def test_keyword_from_extras_first(self):
        event = _FakeEvent(
            message_str="meme 别的词",
            extras={"meme_keyword": "摸头"},
        )
        self.assertEqual(detect_meme_keyword(event), "摸头")
        self.assertEqual(detect_meme_keyword(_FakeEvent(extras={"sticker": " rua "})), "rua")

    def test_keyword_from_explicit_text(self):
        self.assertEqual(detect_meme_keyword(_FakeEvent(message_str="meme 摸头")), "摸头")
        self.assertEqual(detect_meme_keyword(_FakeEvent(message_str="表情包：rua 一下")), "rua")
        self.assertEqual(detect_meme_keyword(_FakeEvent(message_str="#meme:摸头")), "摸头")

    def test_keyword_fallback_only_when_source_confirmed(self):
        handler = _MemeHandler("plugins/astrbot_plugin_memelite/main.py")
        event = _FakeEvent(
            message_str="摸摸头", extras={"activated_handlers": [handler]}
        )
        self.assertEqual(detect_meme_keyword(event), "摸摸头")
        # 来源没确认时，不能把群友那句话当关键词
        self.assertEqual(detect_meme_keyword(_FakeEvent(message_str="摸摸头")), "")

    def test_keyword_rejects_long_or_sentence_text(self):
        # 附加信息里塞了整句话（超 16 字）→ 不当关键词
        long_text = "今天天气真好啊我们出去玩吧顺便再聊两句"
        self.assertGreater(len(long_text), 16)
        self.assertEqual(detect_meme_keyword(_FakeEvent(extras={"meme": long_text})), "")
        # 句中带标点 → 是整句不是关键词
        self.assertEqual(detect_meme_keyword(_FakeEvent(extras={"meme": "我今天，心情不错"})), "")
        # 首尾标点剥掉后仍是短词 → 保留
        self.assertEqual(detect_meme_keyword(_FakeEvent(extras={"meme": " 摸头！"})), "摸头")
        # 文本路径最多截到 16 字，不会把整句塞进标签
        kw = detect_meme_keyword(_FakeEvent(message_str="meme " + long_text))
        self.assertLessEqual(len(kw), 16)
        self.assertEqual(detect_meme_keyword(_FakeEvent(message_str="随便聊聊")), "")


class TestRefineSelfMediaSummary(unittest.TestCase):
    """after_message_sent 的摘要细化：只在「纯媒体 + 有线索」时才改写。"""

    _MEME_EXTRAS = {
        "activated_handlers": [_MemeHandler("plugins/astrbot_plugin_memelite/main.py")]
    }

    def test_non_media_summary_untouched(self):
        event = _FakeEvent(extras=self._MEME_EXTRAS)
        self.assertEqual(refine_self_media_summary("欢迎小明进群", event=event), "欢迎小明进群")
        self.assertEqual(refine_self_media_summary("", event=event), "")

    def test_keyword_and_animated_gif(self):
        event = _FakeEvent(message_str="meme 摸头", extras=self._MEME_EXTRAS)
        chain = [Image(file="base64://" + _animated_gif_b64())]
        self.assertEqual(
            refine_self_media_summary("[表情包]", event=event, chain=chain),
            "[发送了表情包: 摸头 (动图/GIF)]",
        )

    def test_keyword_without_animation(self):
        event = _FakeEvent(message_str="meme 摸头", extras=self._MEME_EXTRAS)
        chain = [Image(file="a.png")]
        self.assertEqual(
            refine_self_media_summary("[表情包]", event=event, chain=chain),
            "[发送了表情包: 摸头]",
        )

    def test_animated_without_keyword(self):
        chain = [Image(file="base64://" + _animated_gif_b64())]
        self.assertEqual(
            refine_self_media_summary("[表情包]", chain=chain), "[表情包 (动图/GIF)]"
        )

    def test_no_clue_keeps_plain_label(self):
        # 既没有来源线索也没有动图 → 保持原样，别编关键词
        self.assertEqual(
            refine_self_media_summary("[表情包]", event=_FakeEvent(), chain=[Image(file="a.png")]),
            "[表情包]",
        )

    def test_plain_image_label_not_promoted(self):
        # 普通照片不是表情包，不该被贴成「发送了表情包」
        event = _FakeEvent(extras=self._MEME_EXTRAS)
        self.assertEqual(
            refine_self_media_summary("[图片]", event=event, chain=[Image(file="a.png")]),
            "[图片]",
        )

    def test_mixed_multi_media_left_alone(self):
        chain = [Image(file="base64://" + _animated_gif_b64()), Image(file="b.png")]
        self.assertEqual(
            refine_self_media_summary("[表情包] [图片]", chain=chain), "[表情包] [图片]"
        )


class TestSemanticUpgradeInEchoLog(unittest.TestCase):
    """语义标签比协议端 tap 晚到：窗口内原地升级，不许多记一条。"""

    def test_upgrade_same_entry_within_merge_window(self):
        seen: list[tuple[str, str]] = []
        updated: list[tuple[str, str, list[str]]] = []
        log = BotEchoLog(
            on_record=lambda g, t: seen.append((g, t)),
            on_update=lambda g, t, images=None: updated.append((g, t, list(images or []))),
            dedupe_window=2.5,
            media_merge_window=8.0,
        )
        self.assertTrue(log.record("g1", "[表情包]", media=True))
        # 4 秒后 after_message_sent 才报出语义标签（已超 dedupe_window）
        log._last["g1"].at = time.time() - 4.0
        self.assertFalse(
            log.record("g1", "[发送了表情包: 摸头 (动图/GIF)]", media=True)
        )
        self.assertEqual(seen, [("g1", "[表情包]")])
        self.assertEqual(log.last("g1"), "[发送了表情包: 摸头 (动图/GIF)]")
        self.assertEqual(
            updated, [("g1", "[发送了表情包: 摸头 (动图/GIF)]", [])]
        )

    def test_upgrade_beyond_merge_window_records_new_entry(self):
        log = BotEchoLog(dedupe_window=2.5, media_merge_window=8.0)
        self.assertTrue(log.record("g1", "[表情包]", media=True))
        log._last["g1"].at = time.time() - 20.0
        # 超出合并窗口 → 当成另一条真实发送
        self.assertTrue(log.record("g1", "[发送了表情包: 摸头 (动图/GIF)]", media=True))


class TestAfterMessageSentIntegration(unittest.TestCase):
    """main.on_after_message_sent 真的把语义标签喂进了 BotEchoLog。"""

    def test_hook_records_semantic_sticker_label(self):
        async def run():
            from astrbot_plugin_proactive_life.main import ProactiveLifePlugin

            plugin = object.__new__(ProactiveLifePlugin)
            plugin._stop_event = asyncio.Event()
            plugin._module_on = lambda _name: True
            recorded: list[tuple[str, str]] = []
            plugin.bot_echo = BotEchoLog(on_record=lambda g, t: recorded.append((g, t)))

            extras = {
                "activated_handlers": [_MemeHandler("plugins/astrbot_plugin_memelite/main.py")]
            }

            class _HookEvent:
                message_str = "meme 摸头"
                unified_msg_origin = "aiocqhttp:GroupMessage:88888"

                def get_extra(self, key=None, default=None):
                    if key is None:
                        return dict(extras)
                    return extras.get(key, default)

                def get_result(self):
                    return SimpleNamespace(
                        chain=[Image(file="base64://" + _animated_gif_b64())],
                        get_plain_text=lambda: "",
                    )

                def is_private_chat(self):
                    return False

                def get_group_id(self):
                    return "88888"

            await plugin.on_after_message_sent(_HookEvent())
            return recorded

        self.assertEqual(
            asyncio.run(run()), [("88888", "[发送了表情包: 摸头 (动图/GIF)]")]
        )


if __name__ == "__main__":
    unittest.main()
