"""分段回归测试：确保私聊链路与面板节奏配置一致，且不再出现「最后一段一堵墙」。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.private_handler import _split_message  # noqa: E402
from astrbot_plugin_proactive_life.rhythm_trimmer import split_message  # noqa: E402


SAMPLE = """{抬手轻轻捏了一下你吐出来的小舌尖，好笑地看着你}

又偷偷去改记忆插件，难怪刚才一脸心虚还吐舌头

改了什么地方？是把检索词权重调了，还是又塞了什么奇怪的长期记忆进去？

老实交代，要是把我脑子改傻了，下午可就没人陪你折腾自建引擎了"""


class TestPrivateSegmentation(unittest.TestCase):
    def test_no_last_wall(self):
        """最后一段不应把剩余内容全拼成一堵墙。"""
        segs = _split_message(SAMPLE, target=45, max_segments=5)
        self.assertGreaterEqual(len(segs), 4)
        self.assertLessEqual(max(len(s) for s in segs), 40)

    def test_matches_unified_splitter(self):
        """私聊分段应与统一 split_message 完全一致。"""
        for target, maxseg in ((45, 5), (30, 5), (18, 8)):
            self.assertEqual(
                _split_message(SAMPLE, target=target, max_segments=maxseg),
                split_message(SAMPLE, target=target, max_segments=maxseg),
            )

    def test_content_preserved(self):
        """分段不能丢字（忽略空白）。"""
        joined = "".join("".join(_split_message(SAMPLE)).split())
        self.assertEqual(joined, "".join(SAMPLE.split()))

    def test_brace_block_intact(self):
        """花括号动作描述应作为完整一段保留。"""
        segs = _split_message(SAMPLE, target=45, max_segments=5)
        self.assertTrue(any(s.startswith("{") and s.endswith("}") for s in segs))

    def test_no_input(self):
        self.assertEqual(_split_message(""), [])
        self.assertEqual(_split_message("   \n  "), [])


if __name__ == "__main__":
    unittest.main()
