"""群黑话人名过滤与单条/批量删除回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from astrbot_plugin_proactive_life import panel_actions
from astrbot_plugin_proactive_life.modules.slang import SlangMiner


def test_filters_member_names_mentions_and_surname_calls():
    miner = SlangMiner(min_hits=2)
    # "电焊" 需独立成词：_WORD_RE 按连续中文切词，"又在电焊" 会切成一个 token，
    # 且整体因含 "在" 命中 _NORMAL_SPEECH 被过滤，导致 "电焊" 无法单独被采集。
    for _ in range(4):
        miner.observe(
            "g1",
            "@唯笑 优可 老王 张总 又在 电焊",
            sender_name="[管理]优可",
            extra_names=["唯笑"],
        )
    words = {r["word"] for r in miner.records()}
    assert "唯笑" not in words
    assert "优可" not in words
    assert "老王" not in words
    assert "张总" not in words
    assert "电焊" in words


def test_register_names_retroactively_cleans_existing_records():
    miner = SlangMiner(min_hits=2)
    for _ in range(3):
        miner.observe("g1", "蒂欧 整活")
    assert any(r["word"] == "蒂欧" for r in miner.records())

    miner.register_names(["★蒂欧★"], gid="g1")
    assert not any(r["word"] == "蒂欧" for r in miner.records())
    assert any(r["word"] == "整活" for r in miner.records())


def test_single_and_batch_delete_preserve_other_words_in_same_group():
    miner = SlangMiner(min_hits=2)
    for _ in range(3):
        miner.observe("g1", "电焊 摸鱼 整活")
        miner.observe("g2", "电焊 摸鱼")
    miner.set_analysis([
        {"word": "电焊", "meaning": "修代码", "is_slang": True},
        {"word": "整活", "meaning": "搞节目效果", "is_slang": True},
    ])

    assert miner.remove("电焊", "g1") == 1
    recs = {(r["group"], r["word"]) for r in miner.records()}
    assert ("g1", "电焊") not in recs
    assert ("g1", "摸鱼") in recs
    assert ("g1", "整活") in recs
    assert ("g2", "电焊") in recs
    assert any(a["word"] == "电焊" for a in miner.analysis())

    removed = miner.remove_many([
        {"group": "g1", "word": "整活"},
        {"group": "g2", "word": "电焊"},
    ])
    assert removed == 2
    assert {(r["group"], r["word"]) for r in miner.records()} == {("g1", "摸鱼"), ("g2", "摸鱼")}
    assert miner.analysis() == []


def test_panel_actions_slang_delete_and_batch():
    miner = SlangMiner(min_hits=2)
    for _ in range(3):
        miner.observe("g1", "电焊 摸鱼 整活")
    plugin = SimpleNamespace(_slang=miner)

    res1 = asyncio.run(
        panel_actions.do_action(plugin, {"action": "slang_delete", "group": "g1", "word": "电焊"})
    )
    assert res1["ok"] is True
    assert {r["word"] for r in miner.records()} == {"摸鱼", "整活"}

    res2 = asyncio.run(
        panel_actions.do_action(
            plugin,
            {
                "action": "slang_delete_batch",
                "items": [{"group": "g1", "word": "摸鱼"}, {"group": "g1", "word": "整活"}],
            },
        )
    )
    assert res2["ok"] is True
    assert miner.records() == []
