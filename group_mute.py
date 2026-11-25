"""群聊：文本工具函式与「闭嘴/静默」子系统。

从 group_handler.py 拆出，不改变任何行为：
- 模块级函式保持原签名；
- Mixin 里的方法保持原样（self 上仍需有对应状态字典与 cfg）。
"""

from __future__ import annotations

import asyncio
import os
import re
import time

from astrbot.api import logger



_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]", re.IGNORECASE)

_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|cn|net|org|top|xyz|me|io|cc|tv)(?:/\S*)?",
    re.IGNORECASE,
)

_MUTE_WORDS = (
    "闭嘴", "閉嘴", "别说话", "别讲了", "别吵", "安静点", "安静一下",
    "消停", "别插话", "少说两句", "别bb", "shut up", "stfu", "别叫了",
)

_ANNoyed_WORDS = (
    "好烦", "好煩", "烦死", "煩死", "烦人", "煩人", "你好烦", "你好煩",
    "别刷了", "別刷了", "别刷屏", "別刷屏", "刷屏了", "吵死了", "吵死",
    "闭嘴吧", "閉嘴吧", "能不能安静", "能不安静", "无语", "無語",
    "别闹了", "別鬧了", "少来", "少來", "够了", "夠了", "行了别", "行了別",
)

def is_annoyed_signal(text: str) -> bool:
    """群友在表达嫌烦/不耐烦（比 is_mute_request 更软的信号）。

    用于让 bot 学会「这时候别凑上来」，命中就进入一段短静默。
    只在短句生效，且排除转述（让别人闭嘴、复述）的语气。
    """
    t = (text or "").strip().lower()
    if not t or len(t) > 20:
        return False
    if not any(w in t for w in _ANNoyed_WORDS):
        return False
    # 转述别人的话不算（"他说你好烦" 之类）
    if any(k in t for k in ("他说", "他說", "她说", "她說", "让我", "讓我", "帮我", "幫我")):
        return False
    # 在说自己的烦心事不算（"这游戏烦死了"、"我烦死了"），不是在嫌 bot
    if any(k in t for k in ("我烦", "我煩", "烦死我了", "煩死我了", "这游戏", "這遊戲", "这关", "這關")):
        return False
    return True

def is_mute_request(text: str, names: list[str] | None = None) -> bool:
    """群友在让 Bot 闭嘴。

    只认冲 Bot 来的话：短句直接算；稍微长一点的得带「你」或 Bot 的称呼
    （称呼从角色配置来，不写死任何人的名字）。转述别人的不算。
    """
    t = (text or "").strip().lower()
    if not t or len(t) > 24:
        return False
    if not any(w in t for w in _MUTE_WORDS):
        return False
    if any(k in t for k in ("让我们", "让他们", "让他", "叫我", "叫我们", "喊我们")):
        return False
    call_words = ["你"] + [str(n).lower() for n in (names or []) if str(n).strip()]
    if len(t) > 12 and not any(k in t for k in call_words):
        return False
    return True

def is_legacy_command(text: str) -> bool:
    """旧前缀 `/` 开头的消息是指令，插话链路一律不碰。

    指令前缀已经换成别的了，这些 `/xxx` 既不会命中指令，也不该被当成
    群友闲聊去接话。
    """
    t = (text or "").lstrip()
    return len(t) > 1 and t.startswith("/") and not t[1].isspace() and t[1] != "/"

def strip_links(text: str) -> str:
    """把链接和占位符抹掉，只留人话"""
    t = _URL_RE.sub(" ", text or "")
    t = _PLACEHOLDER_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()

class GroupMuteMixin:
    """闭嘴判定与静默管理；由 GroupProactiveHandler 继承。"""

    def _mute_store_path(self):
        """静默记录路径，沿用统一的 hub_data_path()。"""
        try:
            from .common import hub_data_path

            base = hub_data_path()
        except Exception:
            base = None
        if not base:
            return None
        return os.path.join(str(base), "mute_until.json")

    def _load_muted(self) -> dict[str, float]:
        path = self._mute_store_path()
        if not path:
            return {}
        from .safe_io import sync_read_json

        data = sync_read_json(
            path, {}, log_prefix="proactive_life | 群静默记录读取失败，本次不静默"
        )
        # 逐条容错：以前是一整个推导式套 float()，只要有半个坏值就
        # 整个 except 掉，静默记录全丢
        now = time.time()
        out: dict[str, float] = {}
        for k, v in (data or {}).items():
            try:
                ts = float(v)
            except (TypeError, ValueError):
                continue
            if ts > now:
                out[str(k)] = ts
        return out

    def _save_muted(self, raise_on_error: bool = False):
        path = self._mute_store_path()
        if not path:
            return
        from .safe_io import sync_write_json

        ok = sync_write_json(
            path, self._muted_until,
            log_prefix="proactive_life | 群静默记录落盘失败（重启后可能复发）",
        )
        if not ok and raise_on_error:
            raise OSError(f"写入 {path} 失败")

    def _should_judge_mute(self, origin: str, text: str) -> bool:
        """关键词没命中的时候，值不值得让模型再看一眼。

        只在 Bot 刚开过口的窗口里判，而且只判短句 —— 长篇大论里出现个
        "烦" 字八成跟她没关系。
        """
        t = (text or "").strip()
        if not t or len(t) > 30:
            return False
        spoke_at = self._recent_bot_spoke.get(origin, {}).get("time", 0.0)
        if time.time() - spoke_at > 600:
            return False
        return True

    async def _judge_mute_by_llm(self, origin: str, msg: str, recent: list[str]) -> bool:
        # 同一个群 90 秒内只判一次，免得一屏消息把模型打爆
        now = time.time()
        if now - self._mute_judge_at.get(origin, 0) < 90:
            return False
        self._mute_judge_at[origin] = now

        try:
            provider = self.context.get_using_provider(origin)
        except Exception as e:
            logger.debug("proactive_life | 闭嘴判定取模型失败，跳过判定: %s", e)
            provider = None
        if provider is None:
            return False

        ctx = "\n".join(f"- {line}" for line in recent[-4:]) or "（无）"
        prompt = (
            f"群里最近几条消息：\n{ctx}\n\n"
            f"现在这条：{msg}\n\n"
            f"问题：这句话是不是在嫌{self._bot_name()}（群里一个话偏多的成员）话多、"
            "让 Bot 闭嘴、别插话、安静一会儿？\n"
            "如果是在转述别人的话、在开玩笑、或者说的根本不是 Bot，一律算 NO。\n"
            "只回 YES 或 NO。"
        )
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt="你只回 YES 或 NO，不要解释，不要加标点。",
                    contexts=[],
                ),
                timeout=8,
            )
        except Exception as e:
            logger.debug("proactive_life | 闭嘴判定模型调用失败，按 NO 处理: %s", e)
            return False
        ans = (getattr(resp, "completion_text", "") or "").strip().upper()
        return ans.startswith("YES") or ans.startswith("是")

    async def _judge_and_mute(self, origin: str, msg: str, recent: list[str], by: str):
        try:
            if await self._judge_mute_by_llm(origin, msg, recent):
                minutes = float(self.cfg.get("群聊插话_闭嘴静默分钟", 15.0) or 0)
                self._mute_group(origin, minutes, by)
        except Exception as e:
            # fire-and-forget task 的例外不会有人接，必须在这里留痕
            logger.debug("proactive_life | 闭嘴判定后台任务失败: %s", e)

    def _is_muted(self, origin: str) -> bool:
        until = self._muted_until.get(origin, 0.0)
        if not until:
            return False
        if time.time() >= until:
            self._muted_until.pop(origin, None)
            return False
        return True

    def _mute_group(self, origin: str, minutes: float, by: str = ""):
        if minutes <= 0:
            return
        until = time.time() + minutes * 60.0
        # 已经在静默里就别把时间往后推，免得连着喊两句就锁死一小时
        if self._muted_until.get(origin, 0.0) >= until:
            return
        self._muted_until[origin] = until
        self._save_muted()
        self._diag(origin, f"被 {by or '群友'} 要求闭嘴，静默 {int(minutes)} 分钟")
        # 被要求闭嘴，情绪走低
        self._mood_apply(origin, "muted", "被要求闭嘴")
