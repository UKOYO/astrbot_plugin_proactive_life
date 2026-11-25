"""safe_io 单元测试：原子写、坏文件容错、async 包装。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.safe_io import (  # noqa: E402
    sync_read_json, sync_write_json, async_read_json, async_write_json,
)


class TestSafeIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.assertTrue(sync_write_json(self.path, {"a": 1}))
        self.assertEqual(sync_read_json(self.path), {"a": 1})
        # 原子写不应留下 .tmp
        self.assertEqual(list(Path(self.tmp.name).glob("*.json.*")), [])

    def test_missing_file_silent(self):
        self.assertEqual(sync_read_json(self.path, {"default": 1}), {"default": 1})

    def test_corrupt_file(self):
        self.path.write_text("{bad json", encoding="utf-8")
        self.assertEqual(sync_read_json(self.path, "fallback"), "fallback")

    def test_overwrite(self):
        sync_write_json(self.path, {"v": 1})
        sync_write_json(self.path, {"v": 2})
        self.assertEqual(sync_read_json(self.path), {"v": 2})

    def test_nested_dirs(self):
        p = Path(self.tmp.name) / "a" / "b" / "c.json"
        self.assertTrue(sync_write_json(p, {"x": True}))
        self.assertEqual(sync_read_json(p), {"x": True})


class TestAsyncSafeIO(unittest.IsolatedAsyncioTestCase):
    async def test_async_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "data.json"
            self.assertTrue(await async_write_json(p, {"k": "v"}))
            self.assertEqual(await async_read_json(p), {"k": "v"})


if __name__ == "__main__":
    unittest.main()
