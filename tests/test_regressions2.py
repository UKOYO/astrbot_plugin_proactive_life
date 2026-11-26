"""回归修复（第二批）：群聊双回复、日程缺失不吞内容、破冰记账、防抖生命周期、超时。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestGroupNoDoubleReplyOnAt(unittest.TestCase):
    """@Bot 的消息已由框架原生链路回复，插件不能再接一次。"""

    def test_passive_handler_skips_at_bot(self):
        import inspect

        import astrbot_plugin_proactive_life.group_handler as gh

        src = inspect.getsource(gh.GroupProactiveHandler.handle_group_message_passive)
        self.assertIn('is_at_or_wake_command', src)
        # 过滤必须发生在关键字分支之前（否则命中词仍会触发双回复）
        at_idx = src.index('is_at_or_wake_command')
        kw_filter_idx = src.index('_keyword_filter')
        self.assertLess(at_idx, kw_filter_idx)


class TestLifeStateWithoutSchedule(unittest.TestCase):
    """日程缺失时，情绪/心事仍要注入，不能被提前 return 吞掉。"""

    def _cfg(self):
        return {
            "日程_被动注入": True,
            "拟人_情绪注入被动": True,
            "心事_被动注入": True,
        }

    def test_motive_injected_without_schedule(self):
        from astrbot_plugin_proactive_life.life_inject import MARKER, build_life_state_block

        block = build_life_state_block(
            self._cfg(), info={}, motive="刚看的新番", session_key="u1"
        )
        self.assertIn(MARKER, block)
        self.assertIn("刚看的新番", block)

    def test_mood_injected_without_schedule(self):
        import time

        from astrbot_plugin_proactive_life.life_inject import build_life_state_block
        from astrbot_plugin_proactive_life.mood import MoodStore

        mood = MoodStore()
        mood.apply("u1", "ignored", "被冷落", now=time.time())
        block = build_life_state_block(
            self._cfg(), info={}, mood=mood, session_key="u1"
        )
        self.assertIn("此刻心情", block)

    def test_empty_everything_returns_blank(self):
        from astrbot_plugin_proactive_life.life_inject import build_life_state_block

        self.assertEqual(build_life_state_block(self._cfg(), info={}), "")


class TestIcebreakRecordsRealReply(unittest.TestCase):
    """破冰后记账要用真正发出的文本，不能用心事原文。"""

    def test_post_idle_topic_returns_text(self):
        import inspect

        import astrbot_plugin_proactive_life.group_handler as gh

        sig_src = inspect.getsource(gh.GroupProactiveHandler._post_idle_topic)
        self.assertIn("-> str", sig_src)
        self.assertIn("return reply_text", sig_src)

        caller = inspect.getsource(gh.GroupProactiveHandler.check_idle_groups)
        self.assertIn("reply_text = await self._post_idle_topic", caller)
        self.assertIn("record_bot_group_message(origin, reply_text)", caller)
        # 不能再用心事原文记账
        self.assertNotIn("record_bot_group_message(origin, motive_content)", caller)


class TestDebounceLifecycle(unittest.TestCase):
    """热改参数原地更新；clear() 要真正收尾。"""

    def test_update_params_in_place(self):
        from astrbot_plugin_proactive_life.debounce import DebouncePipeline

        p = DebouncePipeline(1.2, 2.5, 4, 15.0, True)
        p.update_params(0.5, 3.0, 6, 20.0, False)
        self.assertEqual(p.debounce_sec, 0.5)
        self.assertEqual(p.max_wait_sec, 3.0)
        self.assertEqual(p.max_images, 6)
        self.assertEqual(p.lease_ttl, 20.0)
        self.assertFalse(p.enable_emotion_split)

    def test_close_cancels_and_wakes(self):
        from astrbot_plugin_proactive_life.debounce import (
            BatchBucket, DebouncePipeline, MultimodalMessageItem,
        )

        async def scenario():
            p = DebouncePipeline(0.05, 0.05, 4, 15.0, True)
            bucket = BatchBucket()
            bucket.items.append(MultimodalMessageItem(
                sender_name="u", sender_id="1", text="x",
                image_urls=[], reply_text="", reply_image_urls=[],
            ))
            p.current_bucket = bucket
            bucket.max_wait_task = asyncio.create_task(asyncio.sleep(10))
            p.close()
            await asyncio.sleep(0)   # 让取消真正生效
            # 计时器被取消
            self.assertTrue(bucket.max_wait_task.cancelled() or bucket.max_wait_task.done())
            # 等待者被唤醒
            self.assertTrue(bucket.flush_event.is_set())
            # 锁被释放
            self.assertFalse(p.lease_lock._lock.locked())
            # 桶被清空
            self.assertIsNone(p.current_bucket)

        asyncio.run(scenario())

    def test_manager_clear_closes_pipelines(self):
        from astrbot_plugin_proactive_life.debounce import DebounceManager

        mgr = DebounceManager({})
        pipe = mgr.pipeline("aiocqhttp:GroupMessage:1")
        bucket_marker = pipe.current_bucket
        mgr.clear()
        self.assertEqual(mgr.stats()["sessions"], 0)
        self.assertIsNone(bucket_marker)


class TestTimeouts(unittest.TestCase):
    """模型判定与整轮 agent 都要有硬超时。"""

    def test_model_choice_judge_has_wait_for(self):
        import inspect

        import astrbot_plugin_proactive_life.group_handler as gh

        src = inspect.getsource(gh.GroupProactiveHandler._model_choice_judge)
        self.assertIn("asyncio.wait_for", src)
        self.assertIn("_JUDGE_TIMEOUT_SEC", src)

    def test_run_agent_accepts_total_timeout(self):
        import inspect

        from astrbot_plugin_proactive_life.agent_runner import run_agent

        sig = inspect.signature(run_agent)
        self.assertIn("total_timeout", sig.parameters)
        src = inspect.getsource(run_agent)
        self.assertIn("asyncio.wait_for", src)

    def test_callers_pass_total_timeout(self):
        import inspect

        import astrbot_plugin_proactive_life.group_handler as gh
        import astrbot_plugin_proactive_life.private_handler as ph
        import astrbot_plugin_proactive_life.worker_handler as wh

        for mod in (gh, ph, wh):
            src = inspect.getsource(mod)
            self.assertIn("total_timeout=", src, f"{mod.__name__} 未传 total_timeout")


if __name__ == "__main__":
    unittest.main()
