"""八股分级治理回归测试（按模型八股清单）：

1. 马屁开头 + 正文 -> 只裁马屁，正文保住
2. 正文 + NPC 结案/反问尾句 -> 裁尾句，正文保住
3. 纯单句马屁/结案 -> 不得洗成空串
4. 短剧甩尾、"不是…而是…" -> 只统计不改写
5. 代码块/表格/引号台词 -> 原样保护
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402
from astrbot_plugin_proactive_life.modules.speech_style import (  # noqa: E402
    MARKER as SPEECH_MARKER, speech_style_block,
)


def _p(**over):
    cfg = {"清AI八股": True, "演练模式": False}
    cfg.update(over)
    return ProsePolisher(cfg)


class TestFlatteryHead(unittest.TestCase):
    """案例 1：马屁开头只裁掉马屁，正文保留。"""

    CASES = [
        "你说得太对了！这方案能省三天。",
        "一针见血。这么改确实快。",
        "你观察得真敏锐，问题就在这里。",
        "确实很有见地，我也这么想。",
        "这个问题问得很好。答案是缓存。",
    ]

    def test_flattery_trimmed_body_kept(self):
        for text in self.CASES:
            out, hits, rules = _p().polish(text)
            self.assertGreater(hits, 0, f"未命中: {text}")
            self.assertIn("trim_flattery_head", rules, text)
            # 正文关键词必须还在
            self.assertTrue(
                any(k in out for k in ("能省三天", "确实快", "问题就在这里",
                                       "我也这么想", "答案是缓存")),
                f"正文被误删: {text} -> {out}",
            )

    def test_pure_flattery_not_emptied(self):
        """案例 3：整句就是马屁时不能洗成空串。"""
        for text in ("你说得太对了！", "一针见血。", "确实很有见地。"):
            out, _, _ = _p().polish(text)
            self.assertTrue(out.strip(), f"被洗空了: {text}")


class TestOfficialHead(unittest.TestCase):
    def test_official_head_trimmed(self):
        out, _, rules = _p().polish("首先，我们要明确目标。")
        self.assertNotIn("首先", out)
        self.assertIn("明确目标", out)
        self.assertIn("trim_official_head", rules)

    def test_pure_official_not_emptied(self):
        out, _, _ = _p().polish("首先，")
        self.assertTrue(out.strip() or out == "首先，")


class TestClosingTail(unittest.TestCase):
    """案例 2：NPC 结案 / 反问留客尾句被裁，正文保留。"""

    def test_npc_closing_trimmed(self):
        out, _, rules = _p().polish("这个方案能省三天。行，那就这样。")
        self.assertIn("能省三天", out)
        self.assertNotIn("那就这样", out)
        self.assertIn("trim_closing_tail", rules)

    def test_ask_back_trimmed(self):
        out, _, _ = _p().polish("缓存能省不少时间。你觉得呢？")
        self.assertIn("缓存能省不少时间", out)
        self.assertNotIn("你觉得呢", out)

    def test_elevate_closing_trimmed(self):
        out, _, _ = _p().polish("改一行就够了。这就够了。")
        self.assertIn("改一行就够了", out)

    def test_single_sentence_kept(self):
        """单句回复即使像结案也不裁（否则消息就没了）。"""
        out, _, _ = _p().polish("行，那就这样。")
        self.assertTrue(out.strip())


class TestHighRiskDetectOnly(unittest.TestCase):
    """案例 4：高风险句式只统计，不改写。"""

    def test_not_but_not_rewritten(self):
        text = "这不是愤怒，而是失望。"
        p = _p()
        out, _, _ = p.polish(text)
        self.assertIn("而是", out, "「不是…而是…」不能硬改")
        self.assertGreaterEqual(p.last_risk.get("not_but", 0), 1)

    def test_drama_tail_not_rewritten(self):
        text = "机器一开，省事。"
        p = _p()
        out, _, _ = p.polish(text)
        self.assertIn("省事", out, "短剧甩尾不能硬砍")
        self.assertGreaterEqual(p.last_risk.get("drama_tail", 0), 1)

    def test_short_parallel_detected(self):
        p = _p()
        out, _, _ = p.polish("很快。很稳。很省。")
        self.assertIn("很稳", out)
        self.assertGreaterEqual(p.last_risk.get("short_parallel", 0), 1)

    def test_clean_text_no_risk(self):
        p = _p()
        p.polish("今天吃了碗面，味道还行。")
        self.assertEqual(p.last_risk, {})


class TestProtection(unittest.TestCase):
    """案例 5：代码块、表格、引号台词原样保护。"""

    def test_code_block_untouched(self):
        text = "看这个\n```python\nprint('首先，你说得太对了')\n```"
        out, _, _ = _p().polish(text)
        self.assertIn("print('首先，你说得太对了')", out)

    def test_table_untouched(self):
        text = "| 项 | 值 |\n|---|---|\n| 首先 | 对 |"
        out, _, _ = _p().polish(text)
        self.assertIn("| 首先 | 对 |", out)

    def test_quoted_line_untouched(self):
        text = "他跟我说「你说得太对了，这就改」然后走了。"
        out, _, _ = _p().polish(text)
        self.assertIn("「你说得太对了，这就改」", out)


class TestSpeechStyleModelAware(unittest.TestCase):
    """任务 2：说话方式要按模型追加针对性避坑。"""

    def _cfg(self):
        return {"拟人_说话方式约束": True, "拟人_说话方式强度": "strict"}

    def test_base_has_equal_tone(self):
        block = speech_style_block(self._cfg(), "some-model")
        self.assertTrue(block.strip())
        self.assertIn("禁止吹捧奉承", block)
        self.assertIn("反问留客", block)
        self.assertIn("那先这样", block)

    def test_deepseek_hint(self):
        block = speech_style_block(self._cfg(), "deepseek-chat")
        self.assertIn("不是…而是…", block)
        self.assertIn("破折号", block)

    def test_gpt_hint(self):
        block = speech_style_block(self._cfg(), "gpt-4o")
        self.assertIn("翻译腔", block)

    def test_claude_hint(self):
        block = speech_style_block(self._cfg(), "claude-3-5-sonnet")
        self.assertIn("文学比喻", block)

    def test_unknown_model_no_hint(self):
        block = speech_style_block(self._cfg(), "some-local-7b")
        self.assertNotIn("翻译腔", block)
        self.assertNotIn("文学比喻", block)

    def test_switch_off(self):
        self.assertEqual(
            speech_style_block({"拟人_说话方式约束": False}, "gpt-4o"), ""
        )

    def test_extra_requirement_appended(self):
        cfg = dict(self._cfg(), 拟人_说话方式补充="别用网络缩写")
        self.assertIn("别用网络缩写", speech_style_block(cfg, "gpt-4o"))


if __name__ == "__main__":
    unittest.main()
