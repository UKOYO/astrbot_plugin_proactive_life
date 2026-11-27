"""被动回复「生活状态注入」与私聊心事分支丢日程的回归测试。

覆盖三处：
1. ``life_inject.build_life_state_block``：开关、空日程、截断钳制、读取失败降级；
2. ``life_inject.inject_life_state``：挂到 extra_user_content_parts、mark_as_temp
   不落盘、幂等、旧宿主降级到 system_prompt、异常吞掉；
3. ``main.ProactiveLifePlugin.on_life_state_llm_request``：优先级 -900、开关、
   停机实例空转、异常不外抛；
4. ``private_handler._wake_and_speak``：心事分支必须带日程，且不重复空标题。
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot.core.agent.message import (  # noqa: E402
    Message,
    TextPart,
    dump_messages_with_checkpoints,
)

from astrbot_plugin_proactive_life import life_inject  # noqa: E402
from astrbot_plugin_proactive_life.life_inject import (  # noqa: E402
    MARKER,
    build_life_state_block,
    inject_life_state,
)

NOW = datetime.datetime(2026, 11, 26, 15, 30)

BASE_CFG = {
    "模块_日程": True,
    "日程_被动注入": True,
    "日程_被动注入字数": 300,
}

INFO = {
    "date": "2026-11-26",
    "outfit": "米色针织衫 + 深灰长裤",
    "outfit_style": "慵懒居家风",
    "schedule": "07:00 起床\n08:00 通勤\n09:00 写代码\n12:00 午饭\n15:00 继续写代码",
    "current_activity": "写代码",
    "time_desc": "下午",
}


# --------------------------------------------------------------- 生活状态文本块


class TestBuildLifeStateBlock:
    def test_module_switch_off_returns_empty(self):
        cfg = {**BASE_CFG, "模块_日程": False}
        assert build_life_state_block(cfg, now=NOW, info=INFO) == ""

    def test_feature_switch_off_returns_empty(self):
        cfg = {**BASE_CFG, "日程_被动注入": False}
        assert build_life_state_block(cfg, now=NOW, info=INFO) == ""

    def test_empty_schedule_returns_empty(self):
        empty = {"outfit": "", "current_activity": "", "schedule": ""}
        assert build_life_state_block(BASE_CFG, now=NOW, info=empty) == ""

    def test_block_contains_outfit_activity_and_time(self):
        block = build_life_state_block(BASE_CFG, now=NOW, info=INFO)
        assert block.startswith(MARKER)
        assert "15:30" in block
        assert "下午" in block
        assert "此刻在做：写代码" in block
        assert "今日穿搭：米色针织衫 + 深灰长裤" in block
        assert "今日日程：" in block
        assert "09:00 写代码" in block

    def test_header_softens_hard_push(self):
        # 弱化「硬塞日程」：只在被问到/聊到那份上时顺口提一句，别强行汇报
        block = build_life_state_block(BASE_CFG, now=NOW, info=INFO)
        head = block.splitlines()[0]
        assert "顺口提一句" in head
        assert "别硬塞" in head
        assert "不要逐条复述" not in head  # 旧文案：只说别复述，模型照样硬塞

    def test_schedule_truncation_is_clamped_to_min(self):
        # 配置写 10 字，实际按 60 字下限截断，避免日程只剩一句话
        cfg = {**BASE_CFG, "日程_被动注入字数": 10}
        schedule = "早" * 200
        block = build_life_state_block(
            cfg, now=NOW, info={"schedule": schedule, "outfit": "", "current_activity": ""}
        )
        assert "早" * 60 + "…" in block
        assert "早" * 61 not in block

    def test_schedule_truncation_respects_configured_limit(self):
        cfg = {**BASE_CFG, "日程_被动注入字数": 120}
        schedule = "忙" * 500
        block = build_life_state_block(
            cfg, now=NOW, info={"schedule": schedule, "outfit": "", "current_activity": ""}
        )
        assert "忙" * 120 + "…" in block
        assert "忙" * 121 not in block

    def test_invalid_limit_falls_back_to_default(self):
        cfg = {**BASE_CFG, "日程_被动注入字数": "不是数字"}
        schedule = "闲" * 400
        block = build_life_state_block(
            cfg, now=NOW, info={"schedule": schedule, "outfit": "", "current_activity": ""}
        )
        assert "闲" * 300 + "…" in block

    def test_reads_schedule_when_info_missing(self, monkeypatch):
        calls = []

        def fake_read(schedule_time, now=None):
            calls.append(schedule_time)
            return INFO

        monkeypatch.setattr(life_inject, "read_schedule", fake_read)
        block = build_life_state_block({**BASE_CFG, "日程生成时间": "07:00"}, now=NOW)
        assert calls == ["07:00"]
        assert "今日穿搭" in block

    def test_read_failure_degrades_to_empty(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("schedule file gone")

        monkeypatch.setattr(life_inject, "read_schedule", boom)
        assert build_life_state_block(BASE_CFG, now=NOW) == ""

    def test_broken_cfg_degrades_open_without_raising(self):
        class BadCfg:
            def get(self, key, default=None):
                raise RuntimeError("config broken")

        # 配置读取异常按「缺省开启」处理（与 handler_base.module_enabled 一致），
        # 但绝不能把异常抛到回复链路上
        block = build_life_state_block(BadCfg(), now=NOW, info=INFO)
        assert MARKER in block


# ------------------------------------------------------------------ 请求挂载


class TestInjectLifeState:
    def test_appends_temp_text_part_and_does_not_persist(self):
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        assert inject_life_state(req, "【当前生活状态】此刻在写代码") is True
        assert len(req.extra_user_content_parts) == 1
        part = req.extra_user_content_parts[0]
        assert isinstance(part, TextPart)
        assert part.text == "【当前生活状态】此刻在写代码"
        assert getattr(part, "_no_save", False) is True
        # 带 _no_save 的部件不写进会话历史，否则每轮累积
        dumped = dump_messages_with_checkpoints(
            [Message(role="user", content=list(req.extra_user_content_parts))]
        )
        assert dumped == [{"role": "user", "content": []}]

    def test_idempotent_within_same_request(self):
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        assert inject_life_state(req, f"{MARKER}状态") is True
        assert inject_life_state(req, f"{MARKER}状态") is False
        assert len(req.extra_user_content_parts) == 1

    def test_existing_marker_in_any_part_blocks_injection(self):
        existing = TextPart(text=f"{MARKER}已经有人挂过了")
        req = SimpleNamespace(extra_user_content_parts=[existing], system_prompt="sys")
        assert inject_life_state(req, f"{MARKER}状态") is False
        assert len(req.extra_user_content_parts) == 1

    def test_non_text_parts_are_ignored(self):
        req = SimpleNamespace(
            extra_user_content_parts=[{"type": "image_url", "image_url": {"url": "x"}}],
            system_prompt="sys",
        )
        assert inject_life_state(req, f"{MARKER}状态") is True
        assert len(req.extra_user_content_parts) == 2

    def test_fallback_to_system_prompt_for_old_host(self):
        req = SimpleNamespace(system_prompt="人设")
        block = f"{MARKER}状态"
        assert inject_life_state(req, block) is True
        assert req.system_prompt == f"人设\n\n{block}"
        # 同一请求再挂一次不重复
        assert inject_life_state(req, block) is False
        assert req.system_prompt.count(MARKER) == 1

    def test_fallback_without_any_target_returns_false(self):
        assert inject_life_state(SimpleNamespace(), f"{MARKER}状态") is False

    def test_empty_block_and_none_req_return_false(self):
        assert inject_life_state(SimpleNamespace(extra_user_content_parts=[]), "") is False
        assert inject_life_state(None, f"{MARKER}状态") is False

    def test_exception_is_swallowed(self):
        class BadReq:
            @property
            def extra_user_content_parts(self):
                raise RuntimeError("host exploded")

        assert inject_life_state(BadReq(), f"{MARKER}状态") is False


# ------------------------------------------------------------------ 插件钩子


def _plugin_class():
    from astrbot_plugin_proactive_life.main import ProactiveLifePlugin

    return ProactiveLifePlugin


class _FakePlugin:
    def __init__(self, cfg):
        self.cfg = cfg
        self._stop_event = threading.Event()


class TestOnLifeStateLlmRequest:
    def test_hook_registered_with_priority_minus_900(self):
        from astrbot.core.star.star_handler import (
            EventType,
            star_handlers_registry,
        )

        _plugin_class()  # 导入即完成装饰器注册
        matched = [
            md
            for md in star_handlers_registry
            if md.handler_name == "on_life_state_llm_request"
        ]
        assert matched, "on_life_state_llm_request 没注册进 handler 注册表"
        md = matched[0]
        assert md.event_type == EventType.OnLLMRequestEvent
        assert md.extras_configs.get("priority") == -900

    def test_hook_injects_real_block(self, monkeypatch):
        monkeypatch.setattr(life_inject, "read_schedule", lambda *a, **k: INFO)
        plugin = _FakePlugin(BASE_CFG)
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        asyncio.run(
            _plugin_class().on_life_state_llm_request(plugin, SimpleNamespace(), req)
        )
        # 可能注入了多个临时块（生活状态 + 说话方式），找出含生活状态 MARKER 的那个
        texts = [p.text for p in req.extra_user_content_parts]
        life = [t for t in texts if MARKER in t]
        assert len(life) == 1, texts
        assert "米色针织衫" in life[0]

    def test_hook_respects_feature_switch(self):
        plugin = _FakePlugin({**BASE_CFG, "日程_被动注入": False})
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        asyncio.run(
            _plugin_class().on_life_state_llm_request(plugin, SimpleNamespace(), req)
        )
                # 说话方式块独立，日程关闭/构建失败时只是不注入生活状态块
        assert all(MARKER not in getattr(p, "text", "") for p in req.extra_user_content_parts)

    def test_hook_noops_on_stale_instance(self, monkeypatch):
        monkeypatch.setattr(
            life_inject, "read_schedule", lambda *a, **k: INFO
        )
        plugin = _FakePlugin(BASE_CFG)
        plugin._stop_event.set()
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        asyncio.run(
            _plugin_class().on_life_state_llm_request(plugin, SimpleNamespace(), req)
        )
                # 说话方式块独立，日程关闭/构建失败时只是不注入生活状态块
        assert all(MARKER not in getattr(p, "text", "") for p in req.extra_user_content_parts)

    def test_hook_swallows_build_errors(self, monkeypatch):
        import astrbot_plugin_proactive_life.main as main_mod

        def boom(cfg):
            raise RuntimeError("build failed")

        monkeypatch.setattr(main_mod, "build_life_state_block", boom)
        plugin = _FakePlugin(BASE_CFG)
        req = SimpleNamespace(extra_user_content_parts=[], system_prompt="sys")
        # 注入失败不能把正常回复搞挂
        asyncio.run(
            _plugin_class().on_life_state_llm_request(plugin, SimpleNamespace(), req)
        )
                # 说话方式块独立，日程关闭/构建失败时只是不注入生活状态块
        assert all(MARKER not in getattr(p, "text", "") for p in req.extra_user_content_parts)


# ------------------------------------------------------ 私聊主动开口：心事 + 日程


class _FakeMotiveMgr:
    def __init__(self):
        self.used = []

    def mark_private_used(self, motive_id):
        self.used.append(motive_id)


class _FakePrivateHandler:
    """只实现 _wake_and_speak 依赖的方法，避免碰磁盘与真模型。"""

    def __init__(self):
        self.cfg = {"模块_日程": True}
        self.state = {"last_active": {}, "daily": {}}
        self.motive_mgr = _FakeMotiveMgr()
        self.prompts = []
        self.saved = 0
        self.logs = []

    def _user_name(self):
        return "阿岚"

    def _persona_field(self):
        return ""

    def _mood_hint(self, uid):
        return ""

    def _activity_hint(self):
        return ""

    def _builtin_motive(self):
        return "", ""

    def _save_state(self):
        self.saved += 1

    def _log_activity(self, *args, **kwargs):
        self.logs.append(args)

    async def _execute_proactive_agent(self, uid, prompt_text, source):
        self.prompts.append({"uid": uid, "prompt": prompt_text, "source": source})
        return True


def _wake(handler, *, motive, schedule, recent="", count=0):
    from astrbot_plugin_proactive_life.private_handler import PrivateProactiveHandler

    return asyncio.run(
        PrivateProactiveHandler._wake_and_speak(
            handler,
            "u1",
            NOW,
            42.0,
            motive,
            schedule,
            recent,
            "下午",
            "2026-11-26",
            count,
        )
    )


class TestWakeAndSpeakSchedule:
    def test_motive_branch_keeps_schedule(self):
        h = _FakePrivateHandler()
        assert _wake(
            h,
            motive={"id": 7, "content": "想买把新键盘"},
            schedule=INFO["schedule"],
        )
        prompt = h.prompts[0]["prompt"]
        assert "想买把新键盘" in prompt
        # 回归点：以前心事分支把日程整段丢了
        assert "【你的日程与作息状态（背景参考，不必复述）】" in prompt
        assert "09:00 写代码" in prompt
        # 成功后的记账
        assert "u1" in h.state["last_active"]
        assert h.state["daily"]["u1"] == {"date": "2026-11-26", "count": 1}
        assert h.motive_mgr.used == [7]
        assert h.saved == 1

    def test_motive_branch_without_schedule_has_no_empty_header(self):
        h = _FakePrivateHandler()
        assert _wake(h, motive={"id": 1, "content": "发呆"}, schedule="")
        prompt = h.prompts[0]["prompt"]
        assert "发呆" in prompt
        assert "【你的日程与作息状态（背景参考，不必复述）】" not in prompt

    def test_motive_branch_truncates_schedule_to_400(self):
        h = _FakePrivateHandler()
        assert _wake(h, motive={"id": 2, "content": "走神"}, schedule="程" * 600)
        prompt = h.prompts[0]["prompt"]
        assert "程" * 400 in prompt
        assert "程" * 401 not in prompt

    def test_no_motive_branch_still_carries_schedule(self):
        h = _FakePrivateHandler()
        assert _wake(h, motive=None, schedule=INFO["schedule"])
        prompt = h.prompts[0]["prompt"]
        assert "【你的日程与作息状态】" in prompt
        assert "09:00 写代码" in prompt
        assert h.motive_mgr.used == []
