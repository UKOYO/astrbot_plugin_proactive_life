"""动作旁白（解说员腔）治理回归测试。

低参数量模型的高发毛病：正文里夹带「我这就去看看」「让我先查查」这类
自己正在干什么的旁白，读起来像解说员。分两层治理：

1. prompt 硬约束：strict / gentle 两种强度都要带，且对低参数量模型追加一句专项提示
2. 出站句首裁剪：兜底，但不得把整条回复洗空，也不许误伤「我先看看你的图，」
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.polish_bagu import (  # noqa: E402
    trim_narration_head,
)
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402
from astrbot_plugin_proactive_life.modules.speech_style import (  # noqa: E402
    speech_style_block,
)


def _cfg(mode: str = "strict", **over):
    cfg = {"拟人_说话方式约束": True, "拟人_说话方式强度": mode}
    cfg.update(over)
    return cfg


class TestNarrationPrompt(unittest.TestCase):
    """prompt 层：两种强度都要有硬约束。"""

    def test_both_modes_have_rule(self):
        for mode in ("strict", "gentle"):
            block = speech_style_block(_cfg(mode), "gemini-2.5-pro")
            self.assertIn("不报流程", block, f"{mode} 模式缺少旁白约束")

    def test_rule_names_the_phrases(self):
        block = speech_style_block(_cfg(), "")
        for phrase in ("我先看看", "我去查一下", "我这就去改"):
            self.assertIn(phrase, block)

    def test_low_param_models_get_hint(self):
        for name in ("gemini-2.5-flash", "gpt-4o-mini", "some-local-7b", "qwen2.5-14b"):
            block = speech_style_block(_cfg(), name)
            self.assertIn("不宣布打算", block, f"{name} 没拿到低参数模型提示")

    def test_gentle_mode_also_gets_hint(self):
        block = speech_style_block(_cfg("gentle"), "gemini-2.5-flash")
        self.assertIn("不宣布打算", block)

    def test_strong_model_skips_low_param_hint(self):
        block = speech_style_block(_cfg(), "claude-3-5-sonnet")
        self.assertNotIn("不宣布打算", block)

    def test_switch_off_still_empty(self):
        self.assertEqual(
            speech_style_block({"拟人_说话方式约束": False}, "gemini-2.5-flash"), ""
        )


class TestNarrationTrim(unittest.TestCase):
    """清洗层：句首旁白句裁掉，正文保住。"""

    def test_trim_prelude_sentence(self):
        text, n = trim_narration_head("我先看看这个配置。里面那个开关是开着的。")
        self.assertEqual(n, 1)
        self.assertTrue(text.startswith("里面"))

    def test_trim_let_me_variant(self):
        text, n = trim_narration_head("让我先查查日志里有没有报错。看起来没动静。")
        self.assertEqual(n, 1)
        self.assertTrue(text.startswith("看起来"))

    def test_whole_narration_not_emptied(self):
        for s in ("我这就去改。", "稍等一下，我看看。"):
            text, n = trim_narration_head(s)
            self.assertEqual(n, 0, s)
            self.assertEqual(text, s)

    def test_no_false_positive_on_comma(self):
        # 逗号连着后文的不算独立旁白句，删了会缺成分
        for s in ("我先看看你的图，再决定要不要。", "让我想想，可能是缓存的问题。"):
            text, n = trim_narration_head(s)
            self.assertEqual(n, 0, s)
            self.assertEqual(text, s)

    def test_plain_text_untouched(self):
        s = "这个开关是开着的，你看一眼就知道。"
        self.assertEqual(trim_narration_head(s), (s, 0))

    def test_pipeline_applies_rule(self):
        polisher = ProsePolisher({"清AI八股": True, "演练模式": False})
        out, _hits, ids = polisher.polish("我先看看这个配置。那个开关是开着的。")
        self.assertNotIn("我先看看", out)
        self.assertIn("那个开关是开着的", out)
        self.assertIn("trim_narration_head", ids)

    def test_rule_can_be_disabled(self):
        polisher = ProsePolisher(
            {"清AI八股": True, "演练模式": False, "禁用规则": ["trim_narration_head"]}
        )
        out, _hits, _ids = polisher.polish("我先看看这个配置。那个开关是开着的。")
        self.assertIn("我先看看", out)


if __name__ == "__main__":
    unittest.main()
