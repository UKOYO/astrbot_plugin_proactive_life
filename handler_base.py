"""Handler 共用的混入基類。

之前 `_log_activity` 和 `_mood_hint/_mood_apply` 在 private/group/worker
三个 handler 里各抄了一份（含注释），改一处要同步三处。统一收到这里。

依赖：实例上要有 `self.cfg`（配置视图），可选 `activity_logger` / `mood_store`
（由插件主类注入）。
"""

from __future__ import annotations


def module_enabled(cfg, name: str) -> bool:
    """模块级总开关判定：缺省视为开启，配置异常也按开启处理。"""
    try:
        return bool(cfg.get(f"模块_{name}", True))
    except Exception:
        return True


class HandlerContextMixin:
    """提供活动日志与跨轮情绪的通用实现。"""

    # 由插件主类注入，默认 None
    activity_logger = None
    mood_store = None
    identity = None

    # ---------------------------------------------------------------- 角色身分
    def _id(self):
        """取角色身分；未注入时回落到空身份（不含任何人的角色特征）。"""
        from .identity import Identity, from_config

        obj = getattr(self, "identity", None)
        if obj is not None:
            return obj
        try:
            return from_config(self.cfg)
        except Exception:
            return Identity()

    def _bot_name(self) -> str:
        """Bot 自称；留空时从 AstrBot 人设推断一个中性称呼。"""
        return self._id().bot_name or "我"

    def _user_name(self) -> str:
        """私聊对象称呼；留空则用中性词。"""
        return self._id().user_name or "对方"

    def _persona_block(self) -> str:
        """AstrBot 人设 + 手动补充 + 生活背景。"""
        return self._id().persona_block()

    def _persona_field(self) -> str:
        """给 prompt 用的整段人设文字（带小节标题）；没有人设则返回空串。"""
        text = self._persona_block()
        if not text.strip():
            return ""
        return f"【你的人设与生活方式】\n{text.strip()}\n\n"

    # ---------------------------------------------------------------- 模块总开关
    def _module_on(self, name: str) -> bool:
        """模块级总开关：缺省视为开启，便于与别的插件共存。"""
        return module_enabled(self.cfg, name)

    # ---------------------------------------------------------------- 活动日志
    def _log_activity(self, kind: str, text: str, origin: str = "") -> None:
        fn = getattr(self, "activity_logger", None)
        if callable(fn):
            try:
                fn(kind, text, origin)
            except Exception:
                pass

    # ---------------------------------------------------------------- 跨轮情绪
    def _mood_on(self) -> bool:
        try:
            return bool(self.cfg.get("拟人_情绪启用", True))
        except Exception:
            return True

    def _mood_hint(self, key: str) -> str:
        """返回带换行的情绪提示，供拼进 prompt；无情绪时返回空串。"""
        if not self._mood_on():
            return ""
        store = getattr(self, "mood_store", None)
        if store is None:
            return ""
        try:
            hint = store.hint(str(key))
            return (hint + "\n") if hint else ""
        except Exception:
            return ""

    def _mood_apply(self, key: str, event: str, reason: str = "") -> None:
        if not self._mood_on():
            return
        store = getattr(self, "mood_store", None)
        if store is None:
            return
        try:
            store.apply(str(key), event, reason)
        except Exception:
            pass
