"""防抖管线单元测试。

覆盖：多条合并、情绪拆分、多人格式、图片上限、最长等待兜底、租约锁。
运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.debounce import (  # noqa: E402
    DebounceManager, DebouncePipeline, MultimodalMessageItem,
)


def item(name: str, sid: str, text: str, *, emotional: bool = False,
         imgs: list[str] | None = None) -> MultimodalMessageItem:
    return MultimodalMessageItem(
        sender_name=name, sender_id=sid, text=text,
        image_urls=imgs or [], emotional=emotional,
    )


class TestDebounce(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cfg = {
            "防抖_群聊等待秒": 0.3,
            "防抖_群聊最长等待秒": 0.8,
            "防抖_单次图片上限": 2,
            "防抖_情绪拆分": True,
            "防抖_锁超时秒": 1.0,  # 测试用短 TTL，避免看门狗空等 15 秒
        }
        self.mgr = DebounceManager(self.cfg)
        self.pipe = self.mgr.pipeline("aiocqhttp:GroupMessage:1")

    async def asyncTearDown(self) -> None:
        self._stop_watchdog()

    async def test_merge_multiple_messages(self):
        """同一人连发多条应合并为一批，后续消息被吸收。"""
        tasks = [asyncio.create_task(self.pipe.ingest(item("小明", "1", t)))
                 for t in ("在吗", "问你个事", "这游戏怎么打")]
        await asyncio.sleep(0.05)
        absorbed = tasks[1].done() and tasks[2].done()
        payload = await tasks[0]
        self.assertTrue(absorbed)
        self.assertEqual(payload.count, 3)
        self.assertIn("在吗", payload.prompt)
        self.assertIn("这游戏怎么打", payload.prompt)
        self.assertTrue(payload.lock_token)
        self.pipe.lease_lock.release(payload.lock_token)
        self._stop_watchdog()

    def _stop_watchdog(self) -> None:
        """测试结束立即停掉看门狗，避免每个用例空等 15 秒。"""
        wd = getattr(self.pipe.lease_lock, "_watchdog_task", None)
        if wd and not wd.done():
            wd.cancel()

    async def test_emotion_split(self):
        """情绪倾诉与普通提问应分属两批。"""
        tasks = [
            asyncio.create_task(self.pipe.ingest(item("小红", "2", "累死了好烦", emotional=True))),
            asyncio.create_task(self.pipe.ingest(item("小红", "2", "帮我看下这个", emotional=False))),
        ]
        a = await tasks[0]
        b = await tasks[1]
        self.assertEqual(a.count, 1)
        self.assertEqual(b.count, 1)
        self.assertIn("先接住情绪", a.prompt)
        self.assertNotIn("先接住情绪", b.prompt)
        self.pipe.lease_lock.release(a.lock_token)
        self.pipe.lease_lock.release(b.lock_token)

    async def test_multi_sender_format(self):
        """多人同时提问应合成一批，用带 sender 的列表格式。"""
        tasks = [
            asyncio.create_task(self.pipe.ingest(item("小刚", "3", "这是啥"))),
            asyncio.create_task(self.pipe.ingest(item("小美", "4", "我也想知道"))),
        ]
        await asyncio.sleep(0.05)
        p = await tasks[0]
        self.assertEqual(p.count, 2)
        self.assertIn("小刚(QQ:3)", p.prompt)
        self.assertIn("小美(QQ:4)", p.prompt)
        self.assertTrue(tasks[1].done())
        self.pipe.lease_lock.release(p.lock_token)

    async def test_image_cap(self):
        """图片超上限应截断并注明。"""
        p = await self.pipe.ingest(item("小刚", "3", "看图", imgs=["u1", "u2", "u3"]))
        self.assertEqual(len(p.image_urls), 2)
        self.assertIn("已省略", p.prompt)
        self.pipe.lease_lock.release(p.lock_token)

    async def test_max_wait_fallback(self):
        """持续刷屏时最长等待必须兜底发车，不能无限等。"""
        async def spam():
            for i in range(10):
                await asyncio.sleep(0.15)
                try:
                    await self.pipe.ingest(item("刷屏", "9", f"第{i}条"))
                except Exception:
                    pass

        sp = asyncio.create_task(spam())
        await asyncio.sleep(0.02)
        t0 = asyncio.create_task(self.pipe.ingest(item("刷屏", "9", "开头")))
        p = await asyncio.wait_for(t0, timeout=2.0)
        self.assertIsNotNone(p)
        self.assertGreater(p.count, 1)
        self.pipe.lease_lock.release(p.lock_token)
        sp.cancel()
        await asyncio.sleep(0)

    async def test_lease_lock_release(self):
        """租约锁：占用后释放，看门狗不误伤。"""
        p = await self.pipe.ingest(item("甲", "1", "你好"))
        self.assertTrue(self.pipe.lease_lock._lock.locked())
        self.assertTrue(self.pipe.lease_lock.release(p.lock_token))
        self.assertFalse(self.pipe.lease_lock._lock.locked())
        # 再次释放应返回 False（不匹配当前 token）
        self.assertFalse(self.pipe.lease_lock.release(p.lock_token))

    async def test_pipeline_rebuilt_on_config_change(self):
        """配置变化后应自动重建管线，而不是沿用旧参数。"""
        old = self.pipe
        self.cfg["防抖_群聊等待秒"] = 0.9
        new = self.mgr.pipeline("aiocqhttp:GroupMessage:1")
        self.assertIsNot(new, old)
        self.assertEqual(new.debounce_sec, 0.9)


if __name__ == "__main__":
    unittest.main()
