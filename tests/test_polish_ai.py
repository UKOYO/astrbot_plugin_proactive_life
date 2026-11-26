"""八股清洗（AI 规则）回归测试：规则覆盖、透明目录、幂等与安全兜底。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestAIRuleCatalog(unittest.TestCase):
    """规则清单要完整、分类齐全、可安全序列化（面板透明展示用）。"""

    def test_catalog_complete(self):
        from astrbot_plugin_proactive_life.modules.polish_ai_rules import (
            ALL_AI_RULES, category_catalog, rule_catalog,
        )

        catalog = rule_catalog()
        self.assertEqual(len(catalog), len(ALL_AI_RULES))
        self.assertGreaterEqual(len(catalog), 40)
        # 每条都有面板要显示的三要素
        for r in catalog:
            self.assertTrue(r["id"] and r["name"] and r["desc"])
            self.assertTrue(r["category_label"])
        # 分类计数与规则数一致
        cats = category_catalog()
        self.assertGreaterEqual(len(cats), 6)
        self.assertEqual(sum(c["count"] for c in cats), len(catalog))

    def test_ids_stable_and_unique(self):
        """禁用规则存的是 id，id 必须跨进程稳定且唯一。"""
        from astrbot_plugin_proactive_life.modules.polish_ai_rules import ALL_AI_RULES

        ids = [r["id"] for r in ALL_AI_RULES]
        self.assertEqual(len(ids), len(set(ids)), "存在重复规则 id")
        # 职场黑话用序号 id，不能是 hash（hash 每次进程都变）
        corp = [i for i in ids if i.startswith("corp_")]
        self.assertTrue(corp)
        for i in corp:
            self.assertRegex(i, r"^corp_\d{2}$")


class TestAIBoilerplateCleaning(unittest.TestCase):
    """各类八股都要能被清掉。"""

    def _p(self):
        from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher

        return ProsePolisher({"清AI八股": True, "演练模式": False})

    def test_opening_boilerplate(self):
        out, hits, rules = self._p().polish(
            "首先，我们要明确目标。其次，要制定计划。最后，坚持不懈。"
        )
        self.assertNotIn("首先", out)
        self.assertNotIn("其次", out)
        self.assertIn("明确目标", out)   # 正文要保住
        self.assertEqual(hits, 3)

    def test_worth_noting(self):
        out, hits, _ = self._p().polish("值得注意的是，这个方案不错。")
        self.assertNotIn("值得注意的是", out)
        self.assertIn("这个方案不错", out)

    def test_structure_markdown(self):
        out, _, _ = self._p().polish("**重点**如下：\n1. 第一点\n2. 第二点")
        self.assertNotIn("**", out)
        self.assertNotIn("1.", out)
        self.assertIn("重点", out)

    def test_ending_elevation(self):
        out, hits, _ = self._p().polish("让我们一起努力，创造更美好的未来！")
        self.assertEqual(out.strip(), "")
        self.assertGreater(hits, 0)

    def test_self_reference(self):
        out, _, _ = self._p().polish("作为AI，这个问题我不好答。")
        self.assertNotIn("作为AI", out)
        self.assertIn("这个问题我不好答", out)

    def test_service_tone(self):
        out, _, _ = self._p().polish("以上是目前的情况。如果还有其他问题，欢迎随时告诉我。")
        self.assertNotIn("欢迎随时告诉我", out)

    def test_corporate_jargon(self):
        out, _, _ = self._p().polish("我们要赋能团队，形成闭环。")
        self.assertNotIn("赋能", out)
        self.assertNotIn("闭环", out)

    def test_redundant_adverb(self):
        out, _, _ = self._p().polish("这个非常非常重要。")
        self.assertNotIn("非常非常", out)

    def test_switch_off_keeps_text(self):
        from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher

        p = ProsePolisher({"清AI八股": False, "改句式": False, "换词": False,
                           "删八股短语": False, "合并碎句": False,
                           "删结尾升华": False, "水词配额": -1})
        text = "首先，我们要明确目标。"
        out, _, _ = p.polish(text)
        self.assertIn("首先", out)

    def test_code_block_protected(self):
        """代码块里的内容一个字都不能动。"""
        p = self._p()
        text = "看这个\n```python\nprint('首先')\n```"
        out, _, _ = p.polish(text)
        self.assertIn("```python\nprint('首先')\n```", out)

    def test_disabled_rule_skipped(self):
        from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher

        p = ProsePolisher({
            "清AI八股": True, "演练模式": False,
            # 句首裁剪是独立规则，要一并禁用
            "禁用规则": ["ai_worth_noting", "trim_official_head"],
            "改句式": False, "换词": False, "删八股短语": False,
            "合并碎句": False, "删结尾升华": False, "水词配额": -1,
        })
        out, _, _ = p.polish("值得注意的是，这事能成。")
        self.assertIn("值得注意的是", out)


class TestPolishSafety(unittest.TestCase):
    """清洗不能让消息变空或变乱。"""

    def test_plain_text_kept(self):
        from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher

        p = ProsePolisher({})
        text = "今天吃了碗面，味道还行。"
        out, hits, _ = p.polish(text)
        self.assertEqual(hits, 0)
        self.assertEqual(out, text)

    def test_panel_switch_reaches_polisher(self):
        """面板的「清理 AI 八股」开关必须真的传到清洗器。"""
        from astrbot_plugin_proactive_life.modules import outbound

        text = "首先，我们要明确目标。"
        outbound._POLISHER = None
        on = outbound.polish_text(text, {"模块_八股清洗": True, "出站_八股_清AI八股": True})
        self.assertNotIn("首先", on)
        outbound._POLISHER = None
        off = outbound.polish_text(text, {"模块_八股清洗": True, "出站_八股_清AI八股": False})
        self.assertIn("首先", off)

    def test_history_records_rule_names(self):
        from astrbot_plugin_proactive_life.panel_status import _outbound_block

        class FakePlugin:
            cfg = {"模块_思考过滤": True, "模块_八股清洗": True,
                   "出站_八股_清AI八股": True}

        block = _outbound_block(FakePlugin())
        self.assertIn("rules", block)
        self.assertIn("categories", block)
        self.assertEqual(block["rule_total"], len(block["rules"]))
        for item in block["history"]:
            self.assertIn("rule_names", item)


if __name__ == "__main__":
    unittest.main()
