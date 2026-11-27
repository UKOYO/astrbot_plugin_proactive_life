"""v1.5.22 验收：八股清洗的「砍断正文 / 反转语义 / 乱替名词」缺陷。

全部用例都用默认配置跑 `ProsePolisher`，逐条对应审计报告里实测过的输入。
原则：宁可少洗，也不能把正常句子改坏。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules import polish_ai_rules, polish_bagu, polish_rules  # noqa: E402
from astrbot_plugin_proactive_life.modules.prose_polish import ProsePolisher  # noqa: E402


def _polish(text: str, **cfg) -> tuple[str, int]:
    polisher = ProsePolisher(cfg or None)
    out, hits, _ids = polisher.polish(text)
    return out, hits


def _polish_full(text: str) -> tuple[str, int]:
    """full 档：显式开启换词/删词/水词才能验证这些规则本身的行为。"""
    polisher = ProsePolisher({
        "清洗档位": "full", "改句式": True, "换词": True,
        "删八股短语": True, "水词配额": 1, "合并碎句": True,
    })
    out, hits, _ids = polisher.polish(text)
    return out, hits


# ---------------------------------------------------------------- 1. 不能砍断正文


def test_above_is_keeps_mid_sentence_usage():
    text = "3000元以上是我能接受的上限。"
    assert _polish(text)[0] == text


def test_how_can_i_keeps_normal_question():
    text = "你还有什么需要带的吗？"
    assert _polish(text)[0] == text


def test_numbered_list_does_not_eat_decimals():
    text = "大概是\n2.5 版本才修好。"
    assert _polish(text)[0] == text


def test_numbered_list_still_strips_real_list():
    out, hits = _polish("1. 先备份\n2. 再升级")
    assert "备份" in out and "升级" in out
    assert not out.lstrip().startswith("1."), out
    assert hits > 0


def test_ask_back_closing_keeps_choice_question():
    text = "我刚下班。晚上吃火锅还是烧烤，你觉得呢？"
    assert _polish(text)[0] == text


def test_ask_back_closing_still_strips_empty_echo():
    # 多句回复的纯反问尾句照旧砍掉（前一句没有选项）
    out, hits = _polish("今天加班到九点。你觉得呢？")
    assert "你觉得呢" not in out
    assert hits > 0


# ---------------------------------------------------------------- 2. 不改意思


def test_almost_is_not_deleted():
    text = "昨晚一直忙着改代码，我几乎没睡。"
    assert _polish(text)[0] == text


def test_predicate_adjectives_are_not_deleted():
    text = "水有点浑浊，屏幕也突然泛白了。"
    assert _polish(text)[0] == text


def test_delete_phrases_does_not_hole_out_mid_clause():
    """即便短语还在池子里，也不能在「程度副词 + 标点」中间挖洞。"""
    from astrbot_plugin_proactive_life.modules.prose_polish import _delete_phrases

    out, hits = _delete_phrases("水有点浑浊，别的没事。", ["浑浊"])
    assert out == "水有点浑浊，别的没事。"
    assert hits == 0


def test_i_cannot_is_detect_only():
    text = "我没办法完成这个需求。"
    out, hits = _polish(text)
    assert out == text
    assert hits > 0, "仍应计入统计"


def test_corporate_daily_words_are_not_replaced():
    for text in ("两行文字要对齐，别硬发力。", "这关打法不对，打破这个壁垒再落地吧。"):
        out, hits = _polish(text)
        assert out == text, f"{text} -> {out}"
        assert hits > 0, "高歧义职场词仍应计入统计"


def test_corporate_ppt_words_still_cleaned():
    # 纯 PPT 词属于 full 档的换词；止损档不碰（只统计 corp_ambiguous）
    out, _hits = _polish_full("我们要打通颗粒度，做好赋能。")
    assert "颗粒度" not in out and "赋能" not in out


def test_ratio_split_is_detect_only():
    text = "带着三分醉意，七分清醒走过来。"
    out, _hits = _polish(text)
    assert out == text


# ---------------------------------------------------------------- 3. 不改残句式


def test_both_and_also_keeps_modal():
    out, hits = _polish_full("这个工具既能画画，又能写代码，还能剪视频。")
    assert "能画画" in out and "能写代码" in out and "能剪视频" in out
    assert "还能" not in out or "能剪视频" in out
    assert hits > 0


def test_softener_removal_takes_trailing_de():
    out, hits = _polish_full("风轻轻吹过，我静静地看着。")
    assert "地看着" not in out
    assert "我看着" in out
    assert hits > 0


# ---------------------------------------------------------------- 4. 换词池卫生


def test_word_pools_have_no_empty_or_predicate_phrases():
    for rule in polish_rules.WORD_RULES:
        pool = rule.get("pool") or []
        assert all(str(x).strip() for x in pool), f"{rule['id']} 有空替换项"
        assert "手指很长" not in pool, f"{rule['id']} 的替换项是主谓短语，塞定语会变病句"


def test_voice_rasp_is_detect_only():
    """词池是谓语短语、匹配点在宾语名词位：替换后必出残句，只统计不改写。"""
    # 乐器名本身完全不该被碰（连命中都不该有）
    plain = "我学过大提琴，他声音像大提琴一样。"
    out, hits = _polish(plain)
    assert out == plain and hits == 0
    # 真的是八股比喻：只统计，不改写
    cliche = "他有一副大提琴般的嗓音。"
    out2, hits2 = _polish(cliche)
    assert out2 == cliche, out2
    assert hits2 > 0


def test_cold_pool_is_detect_only():
    # 景物描写不该命中
    plain = "山里有个深不见底的古井。"
    out, hits = _polish(plain)
    assert out == plain and hits == 0
    # 写眼睛的八股：只统计，不改写
    cliche = "他有一双深不见底的眼睛。"
    out2, hits2 = _polish(cliche)
    assert out2 == cliche, out2
    assert hits2 > 0


def test_no_ambiguous_words_in_corporate_repl():
    for word in polish_ai_rules.CORPORATE_AMBIGUOUS:
        assert word not in polish_ai_rules.CORPORATE_REPL, word


def test_safe_delete_has_no_content_words():
    risky = {"几乎", "几乎是", "仿佛", "一种", "泛白", "发白", "浑浊", "虔诚", "虔诚的"}
    assert not (risky & set(polish_bagu.SAFE_DELETE))


# ---------------------------------------------------------------- 5. 残余边界（v1.5.23）


def test_above_is_does_not_cross_comma():
    """「以上是…」只删到第一个逗号，不能把后面那半句一起吃掉。"""
    out, hits = _polish("以上是我整理的清单，你看下有没有漏掉的。")
    assert out == "你看下有没有漏掉的。"
    assert hits > 0

    out2, hits2 = _polish("以上内容仅供参考，具体以实际情况为准。")
    assert out2 == "具体以实际情况为准。"
    assert hits2 > 0


def test_lets_together_requires_slogan_tail():
    """带具体内容的「大家一起来…」不能被整条删掉。"""
    text = "大家一起努力把这个bug修掉吧，顺便看看日志。"
    out, hits = _polish(text)
    assert out == text, out
    assert hits == 0
    # 真正的结尾升华仍然要清掉
    out2, hits2 = _polish("改一行就行。让我们一起努力，创造更美好的未来！")
    assert "让我们一起" not in out2
    assert hits2 > 0


def test_softener_does_not_break_fixed_words():
    # 止损档本来就不删水词：固定词更不可能被动
    text = "风轻轻吹过，我在说悄悄话。"
    out, hits = _polish(text)
    assert out == text
    assert hits == 0
    # full 档也只删独立水词，不拆「悄悄话」
    out2, hits2 = _polish_full(text)
    assert out2 == text and hits2 == 0
    # 单独的水词该删还是要删
    out3, hits3 = _polish_full("风轻轻吹过，我悄悄走了。")
    assert "悄悄走了" not in out3 and "我走了" in out3
    assert hits3 > 0


def test_lash_butterfly_composes_with_following_verb():
    out, hits = _polish_full("她的睫毛如蝶翼般轻颤着落下一片阴影。")
    assert "睫毛轻颤着落下" in out
    assert "动了动着" not in out
    assert hits > 0
