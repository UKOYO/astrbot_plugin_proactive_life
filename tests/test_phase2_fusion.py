"""Phase2 融合模块回归测试：黑名单 / 撤回 / 出站清洗 / 戳一戳 / 跟读。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.modules.blacklist import (  # noqa: E402
    BlacklistPolicy, parse_ids, silence_event,
)
from astrbot_plugin_proactive_life.modules.recall_cancel import (  # noqa: E402
    RecallStateManager, is_recall_event,
)
from astrbot_plugin_proactive_life.modules.outbound import (  # noqa: E402
    clean_llm_text, polish_text,
)
from astrbot_plugin_proactive_life.modules.poke import (  # noqa: E402
    PokeCooldown, is_poke_event,
)
from astrbot_plugin_proactive_life.modules.reread import (  # noqa: E402
    RereadState, make_fingerprint, uniform_tail,
)


class TestBlacklist(unittest.TestCase):
    def test_parse_ids(self):
        ids = parse_ids(["123456789", "a:987654321 # note", "abc 55555"])
        self.assertIn("123456789", ids)
        self.assertIn("987654321", ids)
        self.assertIn("55555", ids)

    def test_event_check_group_id_not_mistaken(self):
        cfg = {"模块_黑名单_ID": ["111111"]}
        p = BlacklistPolicy(cfg)
        ev = SimpleNamespace(
            get_sender_id=lambda: "222222",
            get_group_id=lambda: "111111",  # 群号命中，但不应拦截
        )
        self.assertFalse(p.is_blacklisted_event(ev))
        ev2 = SimpleNamespace(get_sender_id=lambda: "111111", get_group_id=lambda: "999")
        self.assertTrue(p.is_blacklisted_event(ev2))

    def test_session_check(self):
        p = BlacklistPolicy({"模块_黑名单_ID": ["111111"]})
        self.assertTrue(p.is_blacklisted_session("aiocqhttp:FriendMessage:111111"))
        self.assertFalse(p.is_blacklisted_session("aiocqhttp:GroupMessage:111111"))

    def test_silence_adapter(self):
        calls = []
        ev = SimpleNamespace(
            stop_event=lambda: calls.append("stop"),
            should_call_llm=lambda v: calls.append(f"llm:{v}"),
            get_result=lambda: None,
        )
        silence_event(ev, BlacklistPolicy({"模块_黑名单_记日志": False}))
        self.assertIn("stop", calls)
        self.assertIn("llm:False", calls)


class TestRecall(unittest.TestCase):
    def test_recall_event(self):
        raw = {"post_type": "notice", "notice_type": "group_recall",
               "message_id": "12345", "operator_id": "999"}
        ev = SimpleNamespace(message_obj=SimpleNamespace(raw_message=raw))
        hit, mid, op = is_recall_event(ev)
        self.assertTrue(hit)
        self.assertEqual(mid, "12345")
        self.assertEqual(op, "999")

    def test_non_recall(self):
        ev = SimpleNamespace(message_obj=SimpleNamespace(raw_message={"post_type": "message"}))
        self.assertFalse(is_recall_event(ev)[0])

    def test_state(self):
        s = RecallStateManager()
        asyncio.run(s.add_pending("umo", "m1"))
        asyncio.run(s.mark_recalled("umo", "m1", "op"))
        self.assertTrue(asyncio.run(s.is_recalled("umo", "m1")))
        self.assertEqual(s.counts()["pending"], 1)


class TestOutbound(unittest.TestCase):
    def test_think_filter_tags(self):
        cfg = {"模块_思考过滤": True, "出站_裸思考过滤": False}
        out, changed = clean_llm_text("系统<think>不该看到的推理</think>你好", cfg)
        self.assertNotIn("不该看到", out)
        self.assertIn("你好", out)
        self.assertTrue(changed)

    def test_think_switch_off(self):
        out, changed = clean_llm_text("<think>x</think>你好", {"模块_思考过滤": False})
        self.assertIn("<think>", out)
        self.assertFalse(changed)

    def test_polish_idempotent(self):
        class Ev:
            def __init__(self): self.m = {}
            def get_extra(self, k): return self.m.get(k)
            def set_extra(self, k, v): self.m[k] = v

        ev = Ev()
        cfg = {"模块_八股清洗": True}
        text = "这不是愤怒，而是彻底的失望。"
        once = polish_text(text, cfg, event=ev)
        twice = polish_text(once, cfg, event=ev)
        self.assertEqual(once, twice)  # 第二次命中 marker，不再改
        # 「不是…而是…」属于高风险句式：只统计不改写（硬改会出语病）
        self.assertIn("而是", once)


class TestPoke(unittest.TestCase):
    def test_poke_event(self):
        raw = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
               "target_id": "BOT", "self_id": "BOT", "user_id": "U1", "group_id": "G1"}
        ev = SimpleNamespace(
            message_obj=SimpleNamespace(raw_message=raw),
            get_self_id=lambda: "BOT", get_sender_id=lambda: "U1", get_group_id=lambda: "G1",
        )
        hit, bot, sender, group = is_poke_event(ev)
        self.assertTrue(hit)
        self.assertEqual((bot, sender, group), ("BOT", "U1", "G1"))

    def test_cooldown_admin_bypass(self):
        c = PokeCooldown(30, 60)
        c.mark("U1", "G1", now=1000)
        self.assertTrue(c.is_cooling("U1", "G1", now=1010))
        self.assertFalse(c.is_cooling("U1", "G1", is_admin=True, now=1010))
        self.assertFalse(c.is_cooling("U1", "G1", now=1065))


class TestReread(unittest.TestCase):
    def test_fingerprint(self):
        from astrbot.api.message_components import Plain
        self.assertEqual(make_fingerprint([Plain("你好")]), "text:你好")
        self.assertEqual(make_fingerprint([]), "")

    def test_uniform_tail(self):
        self.assertEqual(uniform_tail(deque(["a", "a"]), 2), "a")
        self.assertEqual(uniform_tail(deque(["a", "b"]), 2), "")

    def test_state_and_dedup(self):
        st = RereadState(2, 8)
        self.assertEqual(st.observe("g", "a"), "")
        self.assertEqual(st.observe("g", "a"), "a")
        st.mark_handled("g", "a")
        st.observe("g", "a")
        st.observe("g", "a")
        self.assertEqual(st.observe("g", "a"), "")  # 同一指纹不再触发


if __name__ == "__main__":
    unittest.main()
