"""黑名单准入策略（融合自 astrbot_plugin_global_blacklist）。

设计：
- 纯判定逻辑（parse_ids / is_blacklisted_event / is_blacklisted_session）无副作用，
  供各钩子共享；
- 宿主强耦合的 monkey-patch（send_message / CoreEvent.send）改为可选 adapter：
  只在方法存在时启用，失败只记 debug，不影响其他功能；
- 不写任何配置文件，ID 来源为插件配置（与旧插件兼容）；
- 群号绝不拿来做 sender 比对，避免误伤整个群。
"""

from __future__ import annotations

import re
import threading
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 黑名单"
_SPLIT_RE = re.compile(r"[\s,，;；、|]+")
_DIGIT_RUN_RE = re.compile(r"\d{4,}")


def parse_ids(value: Any) -> frozenset[str]:
    """从 list / 多行文本解析 QQ 号，支持 # 注释与 UMO 尾段。"""
    values: list[Any]
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    elif value is None:
        values = []
    else:
        values = [value]
    out: set[str] = set()
    for item in values:
        for line in str(item or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            for tok in _SPLIT_RE.split(line):
                tok = tok.strip().strip("'\"")
                if not tok:
                    continue
                tail = tok.rsplit(":", 1)[-1]
                if tail.isdigit() and len(tail) >= 4:
                    out.add(tail)
                    continue
                m = _DIGIT_RUN_RE.search(tok)
                if m:
                    out.add(m.group(0))
    return frozenset(out)


def _tail(value: Any) -> str:
    return str(value or "").rsplit(":", 1)[-1].strip()


class BlacklistPolicy:
    """按配置缓存的 ID 集合做准入判定。配置签名变化时自动重建。"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._ids: frozenset[str] = frozenset()
        self._sig: tuple = ()
        self._lock = threading.Lock()
        self._refresh()

    def _source(self) -> tuple[list[Any], bool, bool]:
        def g(key: str, default):
            try:
                return self.cfg.get(key, default)
            except Exception:
                return default
        return (
            [g("模块_黑名单_ID", []), g("模块_黑名单_批量文本", "")],
            bool(g("模块_黑名单_拦主动", True)),
            bool(g("模块_黑名单_记日志", False)),
        )

    def _refresh(self) -> None:
        try:
            sources, block_proactive, log_blocked = self._source()
            sig = (tuple(str(s) for s in sources), block_proactive, log_blocked)
            with self._lock:
                if sig == self._sig:
                    return
                self._sig = sig
                self._ids = parse_ids(sources)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 配置读取失败，按空黑名单处理: %s", _LOG, e)

    def ids(self) -> frozenset[str]:
        self._refresh()
        return self._ids

    def is_blacklisted_event(self, event: Any) -> bool:
        """被动消息准入：只拿 sender 比对，绝不拿群号比对。"""
        ids = self.ids()
        if not ids:
            return False
        try:
            sender = str(event.get_sender_id() or "")
            if sender in ids or _tail(sender) in ids:
                return True
            # 私聊（没有 group_id）时用 session / UMO 尾号再判一次
            try:
                gid = event.get_group_id()
            except Exception:
                gid = None
            if not gid:
                for getter in (getattr(event, "get_session_id", None),
                               lambda: getattr(event, "unified_msg_origin", "")):
                    try:
                        val = str(getter() or "")
                        if val and (_tail(val) in ids or val in ids):
                            return True
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 事件判定异常，按未命中处理: %s", _LOG, e)
        return False

    def is_blacklisted_session(self, umo: str) -> bool:
        """主动私聊/追问准入：只对 FriendMessage 类 UMO 比对尾号。"""
        ids = self.ids()
        if not ids or not umo:
            return False
        try:
            parts = str(umo).split(":")
            if len(parts) < 2:
                return False
            kind = parts[-2].lower()
            if kind not in ("friendmessage", "friend", "私聊", "private"):
                return False
            return _tail(umo) in ids
        except Exception:  # noqa: BLE001
            return False

    @property
    def block_proactive(self) -> bool:
        try:
            return bool(self.cfg.get("模块_黑名单_拦主动", True))
        except Exception:
            return True

    @property
    def log_blocked(self) -> bool:
        try:
            return bool(self.cfg.get("模块_黑名单_记日志", False))
        except Exception:
            return False


def silence_event(event: Any, policy: BlacklistPolicy) -> bool:
    """尽力中止事件：逐项 adapter，有则调用，缺则跳过。"""
    hit = False
    for name, args in (
        ("should_call_llm", (False,)),
        ("stop_event", ()),
    ):
        try:
            fn = getattr(event, name, None)
            if callable(fn):
                fn(*args)
                hit = True
        except Exception:  # noqa: BLE001
            continue
    for attr, value in (("is_wake", False), ("is_at_or_wake_command", False)):
        try:
            setattr(event, attr, value)
            hit = True
        except Exception:  # noqa: BLE001
            continue
    try:
        result = event.get_result()
        chain = getattr(result, "chain", None) if result else None
        if chain is not None and hasattr(chain, "clear"):
            chain.clear()
            hit = True
    except Exception:  # noqa: BLE001
        pass
    if policy.log_blocked:
        try:
            logger.info("%s | 已拦截事件 sender=%s", _LOG, event.get_sender_id())
        except Exception:  # noqa: BLE001
            pass
    return hit
