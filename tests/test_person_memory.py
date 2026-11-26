"""跨会话人物档案测试：共享、防串味、token 上限。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.person_memory import PersonMemory  # noqa: E402


class TestSharedIdentity(unittest.TestCase):
    """同一个人在群聊和私聊里是同一个档案。"""

    def test_group_and_private_share_profile(self):
        pm = PersonMemory()
        pm.touch("u1", "最近在打老头环", name="小明", group="g1", scope="group")
        pm.touch("u1", "帮我看看配置", name="小明", scope="private")

        p = pm.get("u1")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "小明")
        self.assertIn("游戏", p.interests)
        # 群里认识的名字，私聊里也在
        self.assertEqual(pm.get("u1").name, "小明")

    def test_name_learned_in_private_visible_in_group(self):
        pm = PersonMemory()
        pm.touch("u1", "在吗", name="小明", scope="private")
        block = pm.block("u1", scope="group")
        self.assertIn("小明", block)


class TestNoFlavorBleed(unittest.TestCase):
    """防串味：私密字段绝不进群聊。"""

    def test_pending_not_in_group(self):
        pm = PersonMemory()
        pm.touch("u1", "你上次说的那个配置怎么弄？", name="小明", scope="private")
        pvt = pm.block("u1", scope="private")
        grp = pm.block("u1", scope="group")
        self.assertIn("欠着他", pvt)
        self.assertNotIn("欠着", grp)
        self.assertNotIn("配置", grp)

    def test_warmth_not_in_group(self):
        pm = PersonMemory()
        for _ in range(10):
            pm.touch("u1", "聊天", name="小明", scope="private")
        pvt = pm.block("u1", scope="private")
        grp = pm.block("u1", scope="group")
        self.assertIn("已经很熟", pvt)
        self.assertNotIn("已经很熟", grp)

    def test_last_topic_private_only(self):
        pm = PersonMemory()
        pm.touch("u1", "那个秘密项目", name="小明", scope="private")
        grp = pm.block("u1", scope="group")
        self.assertNotIn("秘密项目", grp)


class TestTokenBudget(unittest.TestCase):
    """注入内容必须极短，避免每轮多花 token。"""

    def test_group_block_short(self):
        pm = PersonMemory()
        pm.touch("u1", "打游戏看番吃火锅上班加班跑步", name="小明", group="g1", scope="group")
        self.assertLessEqual(len(pm.block("u1", scope="group")), 60)

    def test_private_block_short(self):
        pm = PersonMemory()
        pm.touch("u1", "最近在打老头环，卡在碎星这个boss很久了打不过", name="小明", scope="private")
        pm.touch("u1", "你上次说的那个配置怎么弄？", name="小明", scope="private")
        self.assertLessEqual(len(pm.block("u1", scope="private")), 120)

    def test_no_profile_no_block(self):
        pm = PersonMemory()
        self.assertEqual(pm.block("nobody", scope="group"), "")


class TestLifecycle(unittest.TestCase):
    def test_resolve_pending(self):
        pm = PersonMemory()
        pm.touch("u1", "你说的那个怎么弄？", scope="private")
        self.assertTrue(pm.get("u1").pending)
        pm.resolve_pending("u1")
        self.assertFalse(pm.get("u1").pending)

    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            pm = PersonMemory(path=path)
            pm.touch("u1", "在打老头环", name="小明", scope="private")
            pm.save()
            pm2 = PersonMemory(path=path)
            p = pm2.get("u1")
            self.assertIsNotNone(p)
            self.assertEqual(p.name, "小明")
            self.assertIn("游戏", p.interests)

    def test_bounded(self):
        pm = PersonMemory(max_people=20)
        for i in range(40):
            pm.touch(f"u{i}", "hi", scope="group")
        self.assertLessEqual(len(pm._people), 20)


class TestInjectionIntegration(unittest.TestCase):
    """接进生活状态块后，群聊/私聊拿到的内容不同。"""

    def test_block_injected_into_life_state(self):
        from astrbot_plugin_proactive_life.life_inject import build_life_state_block

        pm = PersonMemory()
        pm.touch("u1", "你上次说的配置怎么弄？", name="小明", scope="private")
        grp = build_life_state_block(
            {"日程_被动注入": True}, info={},
            person=pm.block("u1", scope="group"),
        )
        pvt = build_life_state_block(
            {"日程_被动注入": True}, info={},
            person=pm.block("u1", scope="private"),
        )
        self.assertIn("小明", grp)
        self.assertNotIn("配置", grp)
        self.assertIn("配置", pvt)


if __name__ == "__main__":
    unittest.main()
