import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any


class MotiveManager:
    # 被动闲聊的频控：同一条心事最多递几次、两次之间至少隔多久。
    # 以前每轮都把同一条塞给模型，只要私聊主动没消费它，模型就会连着几轮
    # 反复提同一件事，像卡带。
    PASSIVE_MAX_SHOWN = 2
    PASSIVE_COOLDOWN_SEC = 1800.0

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        # 被动回复热路径要读「最近一条未消费心事」，每次都读盘会抖，
        # 这里用 (mtime, size) 做失效判断的内存缓存。
        self._peek_cache: Optional[Dict[str, Any]] = None
        self._peek_stamp: tuple = ()
        # 被动提示计数（只在内存里，重启清零；不影响 private_used 的持久语义）
        self._passive_stats: Dict[str, Dict[str, float]] = {}

    def _load_data(self) -> Dict[str, Any]:
        from .safe_io import sync_read_json

        data = sync_read_json(
            self.storage_path, {"motives": []},
            log_prefix="proactive_life | 动机池读取失败，本次按空池处理",
        )
        data = data if isinstance(data, dict) else {"motives": []}
        try:
            st = self.storage_path.stat()
            self._peek_stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._peek_stamp = ()
        self._peek_cache = data
        return data

    def peek_latest_unused(self) -> str:
        """只读：最近一条未被私聊消费的心事文本（带文件戳缓存，热路径可用）。"""
        try:
            st = self.storage_path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = ()
        if self._peek_cache is None or stamp != self._peek_stamp:
            self._load_data()
        data = self._peek_cache or {}
        for m in reversed(data.get("motives") or []):
            if not isinstance(m, dict) or m.get("private_used", False):
                continue
            text = str(m.get("content") or "").strip()
            if text:
                return text
        return ""

    def list_motives(self, limit: int | None = None) -> list[Dict[str, Any]]:
        """只读列出心事（带文件戳缓存）。

        面板每 4 秒轮询一次状态，直接 ``_load_data()`` 就是每 4 秒一次磁盘读；
        这里复用 peek 的 (mtime_ns, size) 缓存，文件没变就不读盘。
        """
        try:
            st = self.storage_path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = ()
        if self._peek_cache is None or stamp != self._peek_stamp:
            self._load_data()
        data = self._peek_cache or {}
        pool = [m for m in (data.get("motives") or []) if isinstance(m, dict)]
        if limit is not None and limit > 0:
            return pool[-limit:]
        return pool

    def peek_passive_hint(
        self,
        max_shown: int | None = None,
        cooldown_sec: float | None = None,
        now: float | None = None,
    ) -> str:
        """被动闲聊用的心事提示：带频控，避免连续几轮复读同一条。

        规则（都可覆盖）：同一条最多递 ``PASSIVE_MAX_SHOWN`` 次，两次之间
        至少隔 ``PASSIVE_COOLDOWN_SEC``。计数只在内存里，重启清零；
        真正的「已消费」仍由 ``private_used`` 负责。
        """
        cap = int(self.PASSIVE_MAX_SHOWN if max_shown is None else max_shown)
        gap = float(self.PASSIVE_COOLDOWN_SEC if cooldown_sec is None else cooldown_sec)
        ts = time.time() if now is None else float(now)
        try:
            st = self.storage_path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = ()
        if self._peek_cache is None or stamp != self._peek_stamp:
            self._load_data()
        data = self._peek_cache or {}
        for m in reversed(data.get("motives") or []):
            if not isinstance(m, dict) or m.get("private_used", False):
                continue
            text = str(m.get("content") or "").strip()
            if not text:
                continue
            key = str(m.get("id") or text[:40])
            stat = self._passive_stats.get(key) or {}
            shown = int(stat.get("count", 0) or 0)
            if shown >= cap:
                continue
            last = float(stat.get("last", 0) or 0)
            if last and (ts - last) < gap:
                continue
            self._passive_stats[key] = {"count": shown + 1, "last": ts}
            return text
        return ""

    def _save_data(self, data: Dict[str, Any]) -> None:
        # 原子写由 safe_io 统一处理，避免半截 JSON 导致整个心事库被清空
        from .safe_io import sync_write_json

        sync_write_json(
            self.storage_path, data,
            log_prefix="proactive_life | 动机池落盘失败",
        )

    def get_recent_summary(self, limit: int = 10) -> str:
        """获取最近探索记录摘要，用于传给摸鱼探索 prompt 做去重引导"""
        data = self._load_data()
        motives = data.get("motives", [])
        if not motives:
            return ""
        lines = []
        for m in motives[-limit:]:
            c = m.get("content", "").strip()
            if c:
                lines.append(f"- {c[:60]}")
        return "\n".join(lines)

    def add_motive(self, content: str, source: str = "chat", scope: str = "private") -> Dict[str, Any]:
        """
        添加一条动机或心事。
        scope: 'private' (仅私聊用), 'public' (私聊和群聊都可用)
        """
        content = (content or "").strip()
        if not content:
            return {}
        data = self._load_data()
        motives = data.setdefault("motives", [])
        
        # 相似度/查重
        content_lower = content.lower()
        for m in motives[-20:]:
            exist = m.get("content", "").lower()
            if exist == content_lower or (len(exist) > 10 and exist[:15] in content_lower):
                return m
                
        item = {
            "id": f"m_{uuid.uuid4().hex[:8]}",
            "content": content,
            "source": source,
            "scope": scope,
            "created_at": time.time(),
            "private_used": False,
            "used_groups": []
        }
        motives.append(item)
        if len(motives) > 60:
            data["motives"] = motives[-60:]
        self._save_data(data)
        return item

    def get_candidate_for_private(self, recent_chat: str = "", max_age_hours: float = 48.0) -> Optional[Dict[str, Any]]:
        """获取一条适合私聊开口的心事，并排除已经在最近聊天里提过的话题"""
        data = self._load_data()
        now = time.time()
        recent_chat_lower = (recent_chat or "").lower()

        for m in reversed(data.get("motives", [])):
            if not m.get("private_used", False):
                age = (now - float(m.get("created_at", 0))) / 3600
                if age <= max_age_hours:
                    c = m.get("content", "")
                    # 如果这件心事里的核心片段（比如前8个字或关键词）已经在最近私聊里出现过，视为已经聊过，直接跳过并标记已用
                    sample_keywords = [w for w in [c[:8], c[8:16], c[-8:]] if len(w) >= 4]
                    if recent_chat_lower and any(kw.lower() in recent_chat_lower for kw in sample_keywords):
                        m["private_used"] = True
                        self._save_data(data)
                        continue
                    return m
        return None

    def delete_motive(self, motive_id: str) -> bool:
        """按 id 删除一条心事；返回是否真的删掉。"""
        mid = str(motive_id or "").strip()
        if not mid:
            return False
        data = self._load_data()
        motives = list(data.get("motives") or [])
        kept = [m for m in motives if str(m.get("id") or "") != mid]
        if len(kept) == len(motives):
            return False
        data["motives"] = kept
        self._save_data(data)
        return True

    def mark_private_used(self, motive_id: str) -> None:
        data = self._load_data()
        for m in data.get("motives", []):
            if m.get("id") == motive_id:
                m["private_used"] = True
                break
        self._save_data(data)

    def get_candidate_for_group(self, group_id: str, max_age_hours: float = 36.0) -> Optional[Dict[str, Any]]:
        """获取一条适合某个群聊自发破冰的公共动机"""
        data = self._load_data()
        now = time.time()
        for m in reversed(data.get("motives", [])):
            if m.get("scope") == "public":
                used_groups = m.get("used_groups", [])
                if group_id not in used_groups:
                    age = (now - float(m.get("created_at", 0))) / 3600
                    if age <= max_age_hours:
                        return m
        return None

    def mark_group_used(self, motive_id: str, group_id: str) -> None:
        data = self._load_data()
        for m in data.get("motives", []):
            if m.get("id") == motive_id:
                used = m.setdefault("used_groups", [])
                if group_id not in used:
                    used.append(group_id)
                break
        self._save_data(data)
