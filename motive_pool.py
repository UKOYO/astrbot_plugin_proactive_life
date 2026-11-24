import json
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any

from astrbot.api import logger

class MotiveManager:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path

    def _load_data(self) -> Dict[str, Any]:
        try:
            if self.storage_path.exists():
                return json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            # 解析失败或读取异常时返回空池，但必须留痕，免得整个心事库被悄悄清空
            logger.warning("proactive_life | 动机池读取失败，本次按空池处理: %s", e)
        return {"motives": []}

    def _save_data(self, data: Dict[str, Any]) -> None:
        # 原子写：write_text 直接覆盖时被杀会留半截 JSON，_load_data 解析失败
        # 会返回空池，等于整个心事库被清空
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_name(self.storage_path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except (OSError, TypeError, ValueError) as e:
            logger.warning("proactive_life | 动机池落盘失败: %s", e)

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
