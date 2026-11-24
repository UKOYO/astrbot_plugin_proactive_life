import asyncio
import json
import os
import random
import time
import traceback
import re
from typing import Any

from astrbot.api import logger

from .handler_base import HandlerContextMixin
from .agent_runner import (
    build_agent_config, make_request, attach_history, run_agent,
)
from .context_scene import (
    SceneAnalyzer, SceneGenerator, SceneTracker, SummaryStore,
    note_from_data, SummaryNote, MemberStore,
)

# 群里那些带链接的消息大半是别的 bot 发的卡片，插话链路一律不碰
_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|cn|net|org|top|xyz|me|io|cc|tv)(?:/\S*)?",
    re.IGNORECASE,
)
# [图片] [表情] [CQ:json,...] 这类占位符
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]", re.IGNORECASE)


# 让人闭嘴的说法，短句里命中任意一个就算
_MUTE_WORDS = (
    "闭嘴", "閉嘴", "别说话", "别讲了", "别吵", "安静点", "安静一下",
    "消停", "别插话", "少说两句", "别bb", "shut up", "stfu", "别叫了",
)

# 「嫌烦」的软信号：没直接喊闭嘴，但明显表达不耐烦/不想她继续说。
# 只在短句且指向她时生效，避免误伤群友之间吵架或转述。
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


def is_mute_request(text: str) -> bool:
    """群友在让唯笑闭嘴。

    只认冲她来的话：短句直接算；稍微长一点的得带称呼（你/唯笑/大魔王）。
    转述别人的（"领导让我们闭嘴"）不算。
    """
    t = (text or "").strip().lower()
    if not t or len(t) > 24:
        return False
    if not any(w in t for w in _MUTE_WORDS):
        return False
    if any(k in t for k in ("让我们", "让他们", "让他", "叫我", "叫我们", "喊我们")):
        return False
    if len(t) > 12 and not any(k in t for k in ("你", "唯笑", "大魔王")):
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

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain, Reply
from astrbot.api.platform import MessageType

try:
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform.message_session import MessageSession

    _BREAK_IMPORT_OK = True
except Exception as _e:
    _BREAK_IMPORT_OK = False
    logger.warning("proactive_life | 群聊破冰依赖导入失败，破冰功能将停用: %s", _e)

# MainAgent 能力由 agent_runner 统一判定，避免两处各判一套
from .agent_runner import AGENT_AVAILABLE  # noqa: E402

_MAIN_AGENT_OK = _BREAK_IMPORT_OK and AGENT_AVAILABLE


class GroupProactiveHandler(HandlerContextMixin):
    def __init__(self, context, cfg: dict, motive_mgr):
        self.context = context
        self.cfg = cfg
        self.motive_mgr = motive_mgr
        # 由插件主类注入：写面板活动日志
        self.activity_logger = None
        # 由插件主类注入：跨轮情绪（群按 origin 记）
        self.mood_store = None
        self.active_reply_stacks: dict[str, list[str]] = {}
        self.model_choice_histories: dict[str, list[str]] = {}
        self.recent_context_cache: dict[str, list[dict[str, str]]] = {}
        self._active_stamp: dict[str, float] = {}
        # 记录每个群上一次成功插话/发言的时间，用于动态防刷屏冷却
        self._last_spoke_stamp: dict[str, float] = {}
        # 记录每个群上一次掷骰时间
        self._last_roll_stamp: dict[str, float] = {}
        # 记录唯笑在群里最近一次发言状态：{"time": ts, "text": str, "turns": int}
        # 用于支持【被回应后一段时间内自然接话】
        self._recent_bot_spoke: dict[str, dict[str, Any]] = {}
        # 连续未插话次数（饥饿补偿用）：消息一直聊但一直没插上嘴，概率要往上抬
        self._miss_streak: dict[str, int] = {}
        # 群友在唯笑上次发言之后说过话没有：用来判断接话轮数，
        # 不能拿“两条发言间隔”当轮数——分段发送会一口气把轮数刷满
        self._user_spoke_after: dict[str, bool] = {}
        # 诊断日志节流（每个群最多 10 分钟打一条，避免刷屏）
        self._diag_stamp: dict[str, float] = {}
        # 被人喊闭嘴后的静默截止时间
        self._muted_until: dict[str, float] = self._load_muted()
        # 上次让模型判闭嘴的时间（节流用）
        self._mute_judge_at: dict[str, float] = {}
        # 上次清理 per-origin 内存表的时间
        self._last_prune = 0.0

        # 场景感知：谁在跟谁说话、触发类型、短期对话流（从 context_aware 提炼）
        self._scene = SceneTracker(max_messages=40)
        self._scene_analyzer = SceneAnalyzer()
        self._scene_gen = SceneGenerator()
        self.update_config(cfg)  # 套用场景回复特征词等初始配置
        # 短期总结：跨轮的约束/纠正/决定/待办
        self._summary = SummaryStore(max_sessions=100)
        self._summary_pending: dict[str, list] = {}     # 攒够消息才整理
        self._summary_last_at: dict[str, float] = {}    # 上次整理时间（节流）
        self._summary_tasks: set[asyncio.Task] = set()
        # 成员印象：谁常找你、爱提问、爱发图、说话长短（纯统计）
        self._members = MemberStore(max_members=60)
        # 最近一次注入的場景快照，供面板展示「每次說話注入了什麼」
        self._last_scene: dict[str, dict[str, Any]] = {}

    def _origin_caches(self) -> tuple[dict, ...]:
        """所有按 origin 一张表往里塞的容器，清理时统一过一遍。

        新增 per-origin 的 dict 记得登记到这里，否则就是又一处只涨不落。
        """
        return (
            self.recent_context_cache,
            self.active_reply_stacks,
            self.model_choice_histories,
            self._active_stamp,
            self._last_spoke_stamp,
            self._last_roll_stamp,
            self._recent_bot_spoke,
            self._miss_streak,
            self._user_spoke_after,
            self._diag_stamp,
            self._muted_until,
            self._mute_judge_at,
        )

    def _prune_origin_caches(self, now: float | None = None) -> None:
        """淘汰长期没动静的群。

        这些表只增不减，进过的群越多涨得越凶。先按最后活跃时间清掉
        24 小时没消息的，再拿 300 个群的硬上限兜个底。
        """
        now = now or time.time()
        # 先快照：下面遍历会连 _active_stamp 自己一起清，边清边查会
        # 把剩下的表都当成“没有活跃时间戳”而放过
        stamps = dict(self._active_stamp)
        for table in self._origin_caches():
            for origin in [o for o in table if now - stamps.get(o, now) > 86400]:
                table.pop(origin, None)
        if len(stamps) <= 300:
            return
        alive = set(sorted(stamps, key=stamps.get, reverse=True)[:300])
        for table in self._origin_caches():
            for origin in [o for o in table if o not in alive]:
                table.pop(origin, None)
        # 场景窗口同步清理
        for origin in list(self._scene._sessions.keys()):
            if origin not in alive:
                self._scene.clear(origin)
        for origin in list(self._last_scene.keys()):
            if origin not in alive:
                self._last_scene.pop(origin, None)
        for origin in list(self._summary_pending.keys()):
            if origin not in alive:
                self._summary_pending.pop(origin, None)
                self._summary_last_at.pop(origin, None)
                self._summary.clear(origin)
                self._members.clear(origin)

    def update_config(self, cfg: dict):
        self.cfg = cfg
        # 场景相关配置变了就同步到分析器/追踪器
        try:
            starters = list(cfg.get("场景感知_回复特征词") or [])
            if starters:
                from .context_scene import REPLY_STARTERS
                self._scene_analyzer.reply_starters = tuple(
                    str(x).strip() for x in starters if str(x).strip()
                ) or REPLY_STARTERS
        except Exception as e:
            logger.debug("proactive_life | 场景回复特征词更新失败: %s", e)

    def _scene_cfg(self, key: str, default):
        try:
            return self.cfg.get(key, default)
        except Exception:
            return default

    def _life_state_hint(self) -> str:
        """读取日程的当前生活状态，拼成 prompt 片段（只读取，失败返回空串）。"""
        if not self._module_on("日程"):
            return ""
        try:
            from .common import read_schedule
            st = str(self.cfg.get("日程生成时间", "00:00") or "00:00")
            info = read_schedule(st)
            bits = []
            if info.get("outfit"):
                bits.append(f"今日穿搭：{info['outfit']}")
            if info.get("current_activity"):
                bits.append(f"此刻在做：{info['current_activity']}")
            if not bits:
                return ""
            return "【你现在的生活状态】" + "；".join(bits) + "\n"
        except Exception:
            return ""

    def _queue_summary(self, origin: str, sm, now: float) -> None:
        """攒消息；条数或时间到阈值就触发一次不阻塞的整理。"""
        if not self._module_on("上下文"):
            return
        if not bool(self._scene_cfg("场景感知_总结启用", True)):
            return
        try:
            self._summary_pending.setdefault(origin, []).append({
                "sender": sm.sender_name,
                "text": sm.content[:160],
                "to": sm.talking_to_name,
                "is_bot": sm.is_bot,
            })
            if len(self._summary_pending[origin]) > 40:
                self._summary_pending[origin] = self._summary_pending[origin][-40:]
            need_n = max(4, int(self._scene_cfg("场景感知_总结每N条", 12) or 12))
            need_sec = max(30, int(self._scene_cfg("场景感知_总结间隔秒", 180) or 180))
            last = self._summary_last_at.get(origin, 0.0)
            if len(self._summary_pending[origin]) >= need_n and (now - last) >= need_sec:
                self._maybe_summarize(origin)
        except Exception as e:
            logger.debug("proactive_life | 总结排队失败: %s", e)

    def _maybe_summarize(self, origin: str) -> None:
        """后台生成总结（fire-and-forget），失败不影响接话。"""
        pending = list(self._summary_pending.get(origin) or [])
        if len(pending) < 2:
            return
        # 先清空待整理并记时间，避免并发重复触发
        self._summary_pending[origin] = []
        self._summary_last_at[origin] = time.time()
        try:
            task = asyncio.create_task(self._summarize_session(origin, pending))
            self._summary_tasks.add(task)
            task.add_done_callback(self._summary_tasks.discard)
        except Exception as e:
            logger.debug("proactive_life | 总结任务创建失败: %s", e)

    async def _summarize_session(self, origin: str, pending: list[dict]) -> None:
        """用 LLM 把最近对话提炼成约束/纠正/决定/待办，合并进旧笔记。"""
        prompt = (
            "从下面的群聊片段里提炼需要继续记住的信息，只输出 JSON，不要解释。\n"
            "格式：{\"constraints\":[],\"corrections\":[],\"decisions\":[],\"todos\":[]}\n"
            "- constraints: 用户的偏好/长期约束（如不吃香菜、周五没空）\n"
            "- corrections: 对之前说法的纠正（如改到周六、不是A是B）\n"
            "- decisions: 已经定下来的事\n"
            "- todos: 还待办/待确认的事\n"
            "没有的类别给空数组；每条不超过30字；不要把闲聊、玩笑、你自己的建议当成用户事实。\n\n"
            "群聊片段：\n" +
            "\n".join(f"{p['sender']}->{p['to']}: {p['text']}" for p in pending[-20:])
        )
        try:
            from .common import get_chat_provider, get_provider_by_id
            pid = str(self._scene_cfg("场景感知_总结模型", "") or "").strip()
            prov = get_provider_by_id(self.context, pid)
            if prov is None:
                prov = get_chat_provider(self.context, origin)
            if prov is None:
                return
            resp = await asyncio.wait_for(
                prov.text_chat(prompt=prompt, system_prompt="你是信息提炼器，只输出 JSON"),
                timeout=20,
            )
            text = (getattr(resp, "completion_text", "") or "").strip()
            # 提取 JSON 片段（模型可能带 ```json 包裹）
            import json as _json, re as _re
            m = _re.search(r"\{[\s\S]*\}", text)
            data = _json.loads(m.group(0)) if m else {}
            note = note_from_data(data, time.time())
            if note.is_empty():
                return
            old = self._summary.get(origin) or SummaryNote()
            # 合并：新条目优先，保留旧的，去重限量
            for k in ("constraints", "corrections", "decisions", "todos"):
                merged = list(getattr(note, k)) + [
                    x for x in getattr(old, k) if x not in getattr(note, k)
                ]
                setattr(note, k, merged[:8])
            self._summary.set(origin, note)
            logger.debug(
                "proactive_life | 短期总结更新 [%s] 约束%d 纠正%d 决定%d 待办%d",
                origin, len(note.constraints), len(note.corrections),
                len(note.decisions), len(note.todos),
            )
        except Exception as e:
            logger.debug("proactive_life | 短期总结失败: %s", e)

    def record_bot_group_message(self, origin: str, text: str):
        """记录唯笑在群里的发言，开启回应接话窗口"""
        now = time.time()
        self._last_spoke_stamp[origin] = now
        # 场景窗口也记一条 Bot 发言，供「用户是否在回我」的推断使用
        if bool(self.cfg.get("场景感知_启用", True)):
            try:
                from .context_scene import SceneMessage
                win = self._scene.recent(origin, 1)
                last_user = win[-1] if win else None
                bot_msg = SceneMessage(
                    sender_id=self._scene_analyzer.bot_id,
                    sender_name="唯笑",
                    content=(text or "")[:300],
                    timestamp=now,
                    is_bot=True,
                    talking_to=(last_user.sender_id if last_user and not last_user.is_bot else "group"),
                    talking_to_name=(last_user.sender_name if last_user and not last_user.is_bot else "群聊"),
                )
                self._scene.append(origin, bot_msg)
                # 标记「Bot 最近接了这个人的话」，供成员印象使用
                if last_user and not last_user.is_bot and last_user.sender_id:
                    self._members.mark_bot_replied(origin, last_user.sender_id)
            except Exception as e:
                logger.debug("proactive_life | 场景记录 Bot 发言失败: %s", e)
        prev = self._recent_bot_spoke.get(origin)
        turns = 0
        # 只有“群友在她上次发言之后说过话”，这一条才算新的一轮接话。
        # 以前按两条发言间隔 <120 秒累加，分段发送（一次拆三四条）会
        # 瞬间把 turns 刷到 2，接话窗口直接失效，群友接话她再也不接
        if prev and self._user_spoke_after.pop(origin, False):
            turns = prev.get("turns", 0) + 1
        elif prev:
            turns = prev.get("turns", 0)

        self._recent_bot_spoke[origin] = {
            "time": now,
            "text": text.strip(),
            "turns": turns
        }
        # 记录到上下文缓存
        cache = self.recent_context_cache.setdefault(origin, [])
        cache.append({"sender": "唯笑", "text": text.strip()})
        if len(cache) > 15:
            self.recent_context_cache[origin] = cache[-15:]

    def _parse_whitelist(self, raw: list) -> list[str]:
        tokens = []
        for item in raw or []:
            if isinstance(item, str):
                tokens.append(item.strip())
            elif isinstance(item, dict):
                val = item.get("group_id") or item.get("id") or item.get("value")
                if val:
                    tokens.append(str(val).strip())
            else:
                tokens.append(str(item).strip())
        return [t for t in tokens if t]

    def _get_target_group_ids(self) -> list[str]:
        """获取所有有效的目标群ID（自动打通独立分区配置与白名单，避免配置为空时死锁）"""
        ids = set()
        # 1. 来自显式白名单
        for tok in self._parse_whitelist(self.cfg.get("群聊白名单") or []):
            gid = tok.split(":")[-1] if ":" in tok else tok
            if gid:
                ids.add(gid)

        # 2. 来自群聊独立分区配置
        partitions = self.cfg.get("群聊独立分区配置") or []
        if isinstance(partitions, dict):
            for k in partitions.keys():
                ids.add(str(k).strip())
        elif isinstance(partitions, list):
            for item in partitions:
                if isinstance(item, dict):
                    gid = str(item.get("group_id", "")).strip()
                    if gid:
                        ids.add(gid)

        return [x for x in ids if x]

    def _get_whitelist_boost(self, gid: str) -> float:
        """白名单群的概率加成倍数，不在名单里的返回 1.0"""
        for tok in self._parse_whitelist(self.cfg.get("群聊白名单") or []):
            g = tok.split(":")[-1] if ":" in tok else tok
            if g and g == str(gid):
                return float(self.cfg.get("群聊白名单_概率倍数", 2.5))
        return 1.0

    def _get_group_custom_param(self, gid: str, param_key: str, default_val: Any) -> Any:
        partitions = self.cfg.get("群聊独立分区配置") or []
        target_cfg: dict | None = None
        gid_str = str(gid)

        if isinstance(partitions, dict):
            target_cfg = partitions.get(gid_str) or partitions.get(gid)
        elif isinstance(partitions, list):
            for item in partitions:
                if isinstance(item, dict):
                    item_gid = str(item.get("group_id", "")).strip()
                    if item_gid and item_gid == gid_str:
                        target_cfg = item
                        break

        if not isinstance(target_cfg, dict):
            return default_val

        aliases = {
            "破冰概率": ["破冰概率", "icebreak_prob", "break_prob"],
            "插话概率": ["插话概率", "chitchat_prob", "chat_prob", "prob"],
            "冷场时间": ["冷场时间", "冷场判定时间", "idle_minutes", "silent_min", "idle_time"]
        }
        lookup_keys = aliases.get(param_key, [param_key])
        for k in lookup_keys:
            if k in target_cfg and target_cfg[k] is not None:
                return target_cfg[k]

        return default_val

    def on_group_message_received(self, event: AstrMessageEvent):
        # 刷新群活跃时间戳
        origin = event.unified_msg_origin
        now = time.time()
        self._active_stamp[origin] = now
        # 群友说话了，下一条唯笑的发言才算接了一轮
        self._user_spoke_after[origin] = True
        # 半小时顺手清一次冷群，别让这些表跟着群数一路涨
        if now - self._last_prune > 1800:
            self._last_prune = now
            self._prune_origin_caches(now)
            self._members.prune(now)  # 成员印象按 TTL 过期

        # 场景感知：提取消息 -> 推断对话对象 -> 写入短期窗口
        if self._module_on("上下文") and bool(self.cfg.get("场景感知_启用", True)):
            try:
                if not self._scene_analyzer.bot_id:
                    sid = ""
                    try:
                        sid = str(event.get_self_id() or "")
                    except Exception:
                        pass
                    self._scene_analyzer.set_bot_identity(sid, ["唯笑", "大魔王"])
                sm = self._scene_analyzer.extract(event)
                self._scene_analyzer.infer_addressee(sm, self._scene.recent(origin))
                self._scene.append(origin, sm)
                # 成员印象：累积谁常找你/爱提问/爱发图等统计
                if bool(self._scene_cfg("拟人_成员印象", True)):
                    self._members.observe(origin, sm)
                if bool(self._scene_cfg("场景感知_调试推断", False)):
                    logger.debug(
                        "proactive_life | 场景推断 [%s] %s → %s (%s)",
                        origin, sm.sender_name, sm.talking_to_name, sm.talking_to,
                    )
                # 短期总结：攒消息，满足条数或间隔就触发一次背景整理
                self._queue_summary(origin, sm, now)
            except Exception as e:
                logger.debug("proactive_life | 场景分析记录失败: %s", e)

        # 饥饿计数按【消息条数】累加，而不是按掷骰次数
        # 否则高密度群里 30 秒才掷一次骰，999+ 条消息也只累积了十几次，补偿永远抬不起来
        self._miss_streak[origin] = self._miss_streak.get(origin, 0) + 1

        # 录入最近群聊上下文
        raw = (event.message_str or "").strip()

        # 带链接的卡片推送连上下文都不进，免得后面顺着标题接话
        if _URL_RE.search(raw) or "[cq:json" in raw.lower():
            return
        # 指令先排掉，免得 /闭嘴 这种命令被当成群友喊话
        if is_legacy_command(raw):
            return

        # 有人喊闭嘴就静默一段时间，连上下文都不用往里塞
        sender = event.get_sender_name() or str(event.get_sender_id())

        # 字面命中，直接静默
        if is_mute_request(raw):
            minutes = float(self.cfg.get("群聊插话_闭嘴静默分钟", 15.0) or 0)
            self._mute_group(origin, minutes, sender)
            return

        # 软性嫌烦信号（你好烦/别刷屏/吵死了等）：短静默，学会看脸色
        if is_annoyed_signal(raw):
            minutes = float(self.cfg.get("群聊插话_嫌烦静默分钟", 8.0) or 0)
            self._mute_group(origin, minutes, sender)
            return

        # 字面没中，但她刚开过口、这句又短，交给模型判一次意思
        if self._should_judge_mute(origin, raw):
            recent = [
                f"{m['sender']}：{m['text']}"
                for m in self.recent_context_cache.get(origin, [])[-4:]
            ]
            try:
                asyncio.create_task(
                    self._judge_and_mute(origin, raw, recent, sender)
                )
            except RuntimeError as e:
                logger.debug("proactive_life | 闭嘴判定任务创建失败（loop 关闭？）: %s", e)
        msg = strip_links(raw)
        if msg:
            cache = self.recent_context_cache.setdefault(origin, [])
            cache.append({"sender": sender, "text": msg})
            if len(cache) > 15:
                self.recent_context_cache[origin] = cache[-15:]

    def _calculate_chitchat_prob(self, base_prob: float, msg_str: str) -> float:
        """
        根据群友聊天内容计算动态插话概率：
        对死宅、二次元、游戏、修仙/熬夜、摸鱼、生草话题赋予兴趣加权
        """
        prob = base_prob
        lower_msg = msg_str.lower()

        high_interest_keywords = [
            "gal", "galgame", "visual novel", "vn", "steam", "switch", "主机",
            "像素", "点阵", "立绘", "脚本", "分镜", "演出", "引擎",
            "二次元", "新番", "补番", "动漫", "漫画", "手办", "画师", "摸鱼",
            "熬夜", "修仙", "卡文", "外卖", "破防", "笑死", "大魔王",
            "神作", "神曲", "ost", "推完", "通关", "全成就", "掉san", "拔作"
        ]

        if any(kw in lower_msg for kw in high_interest_keywords):
            # 兴趣加权只许抬不许压：×1.8，最高封到 0.60
            # 旧写法 min(prob*1.8, 0.20) 会把 0.20 分区直接顶回原点，等于没加成
            prob = min(prob * 1.8, 0.60)
        
        if len(msg_str) <= 2 and msg_str in ("1", "2", "打卡", "签到", "滴"):
            prob = prob * 0.3

        return prob

    def _is_direct_reply_to_bot(self, event: AstrMessageEvent, origin: str) -> bool:
        """检查群友消息是否直接【引用回复】了唯笑刚才的发言"""
        bot_spoke = self._recent_bot_spoke.get(origin)
        # 引用的是几分钟甚至几小时前的老消息，那不叫接话，别顺着翻旧账
        if not bot_spoke or time.time() - bot_spoke.get("time", 0.0) > 120.0:
            return False
        self_id = str(event.get_self_id() or "")
        
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                reply_sender = str(getattr(comp, "sender_id", "") or getattr(comp, "qq", "") or "")
                if self_id and reply_sender == self_id:
                    return True
                # 或者引用文本里包含唯笑刚才发言的片段
                if bot_spoke:
                    bot_txt = bot_spoke.get("text", "")
                    reply_txt = str(getattr(comp, "text", "") or getattr(comp, "message_str", "") or "")
                    if bot_txt and reply_txt and (bot_txt[:15] in reply_txt or reply_txt[:15] in bot_txt):
                        return True
        return False

    def _is_followup_window_active(self, origin: str, msg_str: str) -> bool:
        """
        判断是否处于唯笑刚说完话后的接话窗口（Conversation Window）：
        - 距离唯笑上次发言在 90 秒内
        - 连续接话轮数 <= 2 轮（真人惰性，聊两句自然收尾，防止无休止刷屏）
        - 消息不是无意义纯符号或复读
        """
        bot_spoke = self._recent_bot_spoke.get(origin)
        if not bot_spoke:
            return False

        now = time.time()
        elapsed = now - bot_spoke.get("time", 0.0)
        # 超过 90 秒则接话窗口关闭，群友没接住就翻篇
        if elapsed > 90.0:
            return False

        # 连续接话超过 2 轮后自动潜水（真人打字累了，不纠缠）
        turns = bot_spoke.get("turns", 0)
        if turns >= 2:
            return False

        # 纯单字或纯标点不接
        if len(msg_str) <= 1:
            return False

        return True

    def _mute_store_path(self):
        base = None
        try:
            from astrbot.core.star.star_tools import StarTools

            base = StarTools.get_data_dir("astrbot_plugin_proactive_life")
        except Exception:
            try:
                base = self.context.get_data_dir()
            except Exception:
                base = None
        if not base:
            return None
        return os.path.join(str(base), "mute_until.json")

    def _load_muted(self) -> dict[str, float]:
        path = self._mute_store_path()
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError) as e:
            logger.warning("proactive_life | 群静默记录读取失败，本次不静默: %s", e)
            return {}
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
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._muted_until, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            logger.warning("proactive_life | 群静默记录落盘失败（重启后可能复发）: %s", e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            if raise_on_error:
                raise

    def _should_judge_mute(self, origin: str, text: str) -> bool:
        """关键词没命中的时候，值不值得让模型再看一眼。

        只在唯笑刚开过口的窗口里判，而且只判短句 —— 长篇大论里出现个
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
            "问题：这句话是不是在嫌唯笑（群里一个话偏多的成员）话多、"
            "让她闭嘴、别插话、安静一会儿？\n"
            "如果是在转述别人的话、在开玩笑、或者说的根本不是她，一律算 NO。\n"
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

    def _diag(self, origin: str, msg: str):
        """群插话诊断日志：每群 10 分钟最多一条，用来定位到底卡在哪个判定条件"""
        if not bool(self.cfg.get("群聊插话_诊断日志", True)):
            return
        now = time.time()
        if now - self._diag_stamp.get(origin, 0.0) < 600:
            return
        self._diag_stamp[origin] = now
        logger.info("proactive_life | 群插话诊断 [%s] %s", origin, msg)

    # 说话的分寸感：少喊人设口号，像真人一样知道什么时候该说、该怎么说
    _GUARDRAIL = (
        "【说话的分寸】\n"
        "- 真的有话想说才开口。只是在场、听大家聊、没想法的时候，安静待着就很正常，"
        "不用硬挤一句出来，也别每次都当那个接话的人。\n"
        "- 看清气氛：大家在认真说话、在安慰人、或刚有人嫌你烦/让你安静，就别凑上去抖机灵；"
        "聊得热络、在开玩笑、话题你真有兴趣时，随口搭一句才自然。\n"
        "- 别重复别人刚说过的话，也别当复读机或总结腔。接的是话头里的梗或情绪，不是把上一句换个说法再说一遍。\n"
        "- 不知道就说不知道，别编。没听过的事随口吐槽一句就行，别装懂。\n"
        "- 形似真人单手打字：短、碎、口语。一个语气词（「啊」「草」「啊这」「哈哈」）、"
        "一句吐槽、一个附和，都算正常的回应，不必凑成一整段。句末不用句号。\n"
        "- 不写客服腔、不写老妈子说教、不在尾巴硬加反问句留客。说完就说完。\n"
        "- 带链接的消息基本是别的 bot 在发推送，当没看见，不点开不复述。"
    )

    def _passive_filter(self, event: AstrMessageEvent, origin: str) -> str | None:
        """被动接話的前置過濾；通過則回傳清洗後的訊息，否則 None。"""
        if getattr(event, "is_at_or_wake_command", False):
            return None

        raw_str = (event.message_str or "").strip()
        if _URL_RE.search(raw_str) or "[cq:json" in raw_str.lower():
            self._diag(origin, "带链接/卡片消息，跳过")
            return None
        if is_legacy_command(raw_str):
            self._diag(origin, "旧前缀指令，跳过")
            return None
        msg_str = strip_links(raw_str)
        if not msg_str:
            self._diag(origin, "纯占位符消息，跳过")
            return None
        if len(self.recent_context_cache.get(origin, [])) < 2:
            self._diag(origin, "上下文太薄，先不插话")
            return None
        if self._is_muted(origin):
            left = int(self._muted_until.get(origin, 0) - time.time())
            self._diag(origin, f"静默中 剩{max(left, 0)}s")
            return None
        return msg_str

    async def _passive_decide(
        self, event: AstrMessageEvent, origin: str, msg_str: str, now: float
    ) -> tuple[bool, str, bool]:
        """判斷是否接話，回傳 (should_reply, reply_reason, is_responding_to_bot)。"""
        gid = str(event.get_group_id() or "")

        is_direct_reply = self._is_direct_reply_to_bot(event, origin)
        is_followup_topic = False
        if not is_direct_reply:
            is_followup_topic = self._is_followup_window_active(origin, msg_str)

        is_responding_to_bot = is_direct_reply or is_followup_topic
        reply_reason = "random_chitchat"

        if is_responding_to_bot:
            min_gap = float(self.cfg.get("群聊接话_最短间隔", 8.0))
            if now - self._last_spoke_stamp.get(origin, 0.0) < min_gap:
                return False, reply_reason, is_responding_to_bot
            chance = 0.85 if is_direct_reply else 0.45
            if random.random() < chance:
                reply_reason = "direct_reply" if is_direct_reply else "topic_followup"
                # 群友在接我的话，情绪转好
                self._mood_apply(origin, "replied", "群友接话")
                return True, reply_reason, is_responding_to_bot
            return False, reply_reason, is_responding_to_bot

        # 普通群消息插话判定：冷却 -> 掷骰窗口 -> 概率（含饥饿补偿）-> 硬保底
        last_spoke = self._last_spoke_stamp.get(origin, 0.0)
        cooldown = float(self.cfg.get("群聊插话_冷却时间", 45.0))
        if now - last_spoke < cooldown:
            self._diag(origin, f"冷却中 剩{int(cooldown - (now - last_spoke))}s")
            return False, reply_reason, is_responding_to_bot

        roll_window = float(self.cfg.get("群聊插话_判定窗口", 0.0) or 0.0)
        last_roll = self._last_roll_stamp.get(origin, 0.0)
        if now - last_roll < (roll_window if roll_window > 0 else 3.0):
            return False, reply_reason, is_responding_to_bot
        self._last_roll_stamp[origin] = now

        streak = self._miss_streak.get(origin, 0)
        mode = str(self.cfg.get("群聊插话_模式", "probability") or "probability").strip().lower()
        if mode not in ("probability", "model_choice"):
            self._diag(origin, f"插话模式 {mode!r} 无法识别，按 probability 走")
            mode = "probability"

        if mode == "model_choice":
            should_reply = await self._model_choice_judge(event)
            if not should_reply:
                self._diag(origin, "模型判定潜水")
            return should_reply, reply_reason, is_responding_to_bot

        default_prob = float(self.cfg.get("群聊插话_概率", 0.05))
        base_prob = float(self._get_group_custom_param(gid, "插话概率", default_prob))
        if abs(base_prob - default_prob) < 1e-9:
            base_prob = min(base_prob * self._get_whitelist_boost(gid), 0.60)
        if base_prob <= 0:
            self._diag(origin, "该群概率为 0，静音")
            return False, reply_reason, is_responding_to_bot

        final_prob = self._calculate_chitchat_prob(base_prob, msg_str)
        boosted = min(final_prob * (1.0 + min(streak * 0.02, 2.0)), 0.75)
        # 沉默倾向也影响开口概率：越沉默越不爱接（配额/C 仍按配置）
        try:
            quiet = max(0, min(100, int(self._scene_cfg("拟人_沉默倾向", 45))))
            boosted = max(0.0, min(0.9, boosted * (1.25 - quiet / 200.0)))
        except (TypeError, ValueError):
            pass
        should_reply = random.random() < boosted

        if not should_reply:
            floor_lines = int(self.cfg.get("群聊插话_饥饿保底条数", 30))
            floor_gap = float(self.cfg.get("群聊插话_保底最短间隔", 12.0)) * 60
            if floor_lines > 0 and streak >= floor_lines and (now - last_spoke) >= floor_gap:
                should_reply = True
                reply_reason = "starvation_floor"
                logger.info(
                    "proactive_life | 群插话饥饿保底触发 [%s] 积压%d条",
                    origin, streak
                )

        if should_reply:
            self._miss_streak[origin] = 0
        else:
            self._diag(origin, f"掷骰未中 p={boosted:.3f} 积压{streak}条")
        return should_reply, reply_reason, is_responding_to_bot

    def _context_aware_active(self) -> bool:
        """检查原 context_aware 外挂是否启用；启用时避免重复注入 conversation_scene。"""
        if not bool(self.cfg.get("场景感知_让位contextaware", True)):
            return False
        try:
            meta = self.context.get_registered_star("astrbot_plugin_context_aware")
            return bool(meta and getattr(meta, "activated", False))
        except Exception:
            return False

    def _build_scene_block(self, origin: str) -> str:
        """用场景感知生成 <conversation_scene>；失败时回空串（降级不影响接话）。"""
        if not self._module_on("上下文"):
            return ""
        if not bool(self.cfg.get("场景感知_启用", True)):
            return ""
        if self._context_aware_active():
            # 原 context_aware 正在注入同类场景，避免 LLM 收到两份重叠提示
            return ""
        try:
            win_size = max(2, int(self._scene_cfg("场景感知_对话窗口", 8) or 8))
            show_flow = bool(self._scene_cfg("场景感知_显示对话流", True))
            strict = bool(self._scene_cfg("场景感知_严格模式", False))
            window = self._scene.recent(origin, win_size + 1)
            if not window:
                return ""
            from copy import copy as _copy
            current = _copy(window[-1])  # 避免严格模式改写污染共享历史
            # 被动接话都是主动插入，直接按主动场景告知模型（不依赖 event extra）
            trigger, desc = "active", "你是主动加入这个对话的，没有人在叫你"
            # 严格模式：不把当前消息推断成「在跟 Bot 说」
            if strict and current.talking_to == "bot":
                current.talking_to, current.talking_to_name = "group", "群聊"
            # 短期总结：注入跨轮笔记，让接话像「记得聊过什么」
            summary_text = ""
            if bool(self._scene_cfg("场景感知_总结启用", True)):
                note = self._summary.get(origin)
                if note and not note.is_empty():
                    summary_text = note.render(
                        max_items=max(2, int(self._scene_cfg("场景感知_总结条数", 6) or 6)),
                    )
            # 成员印象：当前发言人的说话习惯，让回应因人而异
            member_note = ""
            if bool(self._scene_cfg("拟人_成员印象", True)):
                member_note = self._members.render(origin, current.sender_id)
            scene = self._scene_gen.generate(
                trigger, desc, current, window[:-1],
                show_flow=show_flow, summary=summary_text, member_note=member_note,
            )
            # 保存快照：面板用它展示「最近一次注入的上下文」
            self._last_scene[origin] = {
                "at": time.time(),
                "text": scene,
                "trigger": trigger,
                "chars": len(scene),
                "flow_count": (max(0, len(window) - 1) if show_flow else 0),
                "talking_to": current.talking_to_name or current.talking_to,
                "has_summary": bool(summary_text),
                "member_note": member_note,
            }
            return scene
        except Exception as e:
            logger.debug("proactive_life | 场景提示生成失败: %s", e)
            return ""

    def _persona_hint(self) -> str:
        """把拟人参数转成自然语言指令，注入接话 prompt。

        三个维度：话多程度 / 沉默倾向 / 吐槽强度，各自分档给不同指令。
        """
        def clamp(v, default=50):
            try:
                return max(0, min(100, int(v)))
            except (TypeError, ValueError):
                return default

        talk = clamp(self._scene_cfg("拟人_话多程度", 50))
        quiet = clamp(self._scene_cfg("拟人_沉默倾向", 45))
        snark = clamp(self._scene_cfg("拟人_吐槽强度", 50))

        # 话多程度：影响句数与展开意愿
        if talk <= 25:
            length = "只说最短的一句，能一个词就别一句话，绝不展开。"
        elif talk <= 50:
            length = "一两短句说完就停，不主动展开话题。"
        elif talk <= 75:
            length = "可以聊两句，接梗后顺带补一句自己的看法，但仍别写长段落。"
        else:
            length = "话可以多一点，接梗后多聊两句、顺着话题延伸，但也别写成小作文。"

        # 沉默倾向：影响「没话说时」的态度
        if quiet >= 75:
            attitude = (
                "没有真正想说的就别开口；群聊是大家的，你不挨个回答。"
                "宁可跳过也不硬凑，只挑真有意思或真想说的那句接。"
            )
        elif quiet >= 50:
            attitude = "只在真有话想说、或话题你真有兴趣时接；没想法就安静。"
        elif quiet >= 25:
            attitude = "多数时候自然搭一句，只有气氛明显不适合时才沉默。"
        else:
            attitude = "乐于搭话，看到消息就自然接一句，别让人觉得你冷淡。"

        # 吐槽强度：影响语气
        if snark >= 75:
            tone = "嘴毒一点，爱吐槽抖机灵，但也别真伤人——玩笑式吐槽，不是攻击。"
        elif snark >= 50:
            tone = "可以随口吐槽、带点调侃，语气轻松不认真说教。"
        elif snark >= 25:
            tone = "温和吐槽就好，以附和、认同为主，偶尔调侃一句。"
        else:
            tone = "温和友善，以附和、鼓励为主，避免任何可能让人不舒服的调侃。"

        return (
            "【说话风格（按这组参数来，优先级高于一般客套）】\n"
            f"- 篇幅：{length}\n"
            f"- 开口意愿：{attitude}\n"
            f"- 语气：{tone}"
        )

    def _build_passive_prompt(
        self, origin: str, msg_str: str, is_responding_to_bot: bool, current_sender: str
    ) -> str:
        """依据上下文组出接话 prompt，不碰事件与状态。

        融入场景感知：<conversation_scene> 告诉模型谁在跟谁说话、它是不是被叫，
        避免把别人之间的对话当成问自己（抢答）；再叠加反应多样性与护栏。
        """
        scene = self._build_scene_block(origin)
        mood_part = self._mood_hint(origin)

        # 反应方式多样性 + 拟人参数（话多/沉默/吐槽）共同决定怎么说
        reaction_hint = (
            "怎么回应自己挑，别每次都一个样：\n"
            "- 好笑的：接梗、抖一句、跟着哈哈；\n"
            "- 有同感的：附和一句，站队或吐槽，不用长篇；\n"
            "- 有看法的：说句真心话或主观吐槽，说完就停；\n"
            "- 没啥好说的：一句语气词（啊 / 草 / 啊这 / 嗯），或按下面的沉默倾向处理。\n\n"
            + self._persona_hint()
        )

        scene_part = ((scene + "\n\n") if scene else "") + mood_part
        if is_responding_to_bot:
            bot_last_words = self._recent_bot_spoke.get(origin, {}).get("text", "")
            return (
                scene_part +
                f"你刚才在群里说的是：「{bot_last_words}」\n"
                f"{current_sender} 接着说：{msg_str}\n\n"
                f"{reaction_hint}\n\n"
                f"{self._GUARDRAIL}\n\n"
                f"他在接你的话。像真人一样自然回一句就行，不用解释、不用报幕。"
            )
        return (
            scene_part +
            f"{current_sender} 刚说：{msg_str}\n\n"
            f"{reaction_hint}\n\n"
            f"{self._GUARDRAIL}\n\n"
            f"你在旁边看聊天，挑一个此刻最自然的反应说出来，哪怕只是一个语气词。"
            f"先看清场景提示：如果这条明显是在跟别人说话，别抢答。"
            f"只输出要说的话本身。"
        )

    async def handle_group_message_passive(self, event: AstrMessageEvent):
        """
        升级版群聊被动感知与接话系统：
        1. 被回应优先接话（引用回复 / 90秒内顺话头接话）；
        2. 兴趣加权被动插话（含饥饿补偿与硬保底）；
        3. 严格的防胡说八道 & 人设护栏 Prompt。
        """
        if not bool(self.cfg.get("群聊插话_启用", False)):
            return

        origin = event.unified_msg_origin
        msg_str = self._passive_filter(event, origin)
        if msg_str is None:
            return

        now = time.time()
        should_reply, reply_reason, is_responding_to_bot = await self._passive_decide(
            event, origin, msg_str, now
        )
        if not should_reply:
            return

        logger.info(
            "proactive_life | 群聊接话命中 [%s] -> %s (msg: %s)",
            reply_reason, origin, msg_str[:30]
        )
        self._log_activity("group", f"群聊接话（{reply_reason}）：{msg_str[:24]}", origin)
        self._last_spoke_stamp[origin] = now

        try:
            if hasattr(event, "set_extra"):
                event.set_extra("_group_active_message_triggered", True)

            session_curr_cid = await self.context.conversation_manager.get_curr_conversation_id(origin)
            if not session_curr_cid:
                try:
                    session_curr_cid = await self.context.conversation_manager.new_conversation(
                        origin,
                        event.get_sender_id(),
                    )
                except Exception as e:
                    logger.error("proactive_life | 自动补建会话失败: %s", e)
                    return

            conv = await self.context.conversation_manager.get_conversation(origin, session_curr_cid)
            if not conv:
                return

            # prompt 需要当前发话人；放在会话就绪后取，避免过滤阶段多取值
            current_sender = event.get_sender_name() or str(event.get_sender_id())
            prompt = self._build_passive_prompt(
                origin, msg_str, is_responding_to_bot, current_sender
            )

            yield event.request_llm(
                prompt=prompt,
                session_id=event.session_id,
                conversation=conv,
            )
        except Exception as e:
            logger.error("proactive_life | 群聊接话投递异常: %s\n%s", e, traceback.format_exc())

    async def _model_choice_judge(self, event: AstrMessageEvent) -> bool:
        try:
            from .common import get_chat_provider, get_provider_by_id
            pid = self.cfg.get("群聊判定模型") or ""
            prov = get_provider_by_id(self.context, pid)
            if not prov:
                prov = get_chat_provider(self.context, event.unified_msg_origin)
            if not prov:
                return False

            prompt = (
                f"当前群消息：「{event.message_str}」。\n"
                f"作为真实群友唯笑，你现在想不想在群里顺口搭一句话？"
                f"输出 YES 或 NO。"
            )
            resp = await prov.text_chat(prompt=prompt, system_prompt="你是判定器，只输出 YES 或 NO")
            txt = (resp.completion_text or "").strip().upper()
            return ("YES" in txt and "NO" not in txt)
        except Exception as e:
            logger.debug("proactive_life | 模型插话判定失败，按不插话处理: %s", e)
            return False

    async def check_idle_groups(self) -> bool:
        """检查是否有冷场群需要自发破冰冒泡。返回 True 表示投递了一条"""
        if not bool(self.cfg.get("群聊自发破冰_启用", True)):
            return False
        if not _MAIN_AGENT_OK:
            return False

        # 破冰时段限制已按需求移除：全天候判定，只受群聊白名单/冷场时长/破冰概率约束

        target_gids = self._get_target_group_ids()
        if not target_gids:
            return False

        now_ts = time.time()
        for gid in target_gids:
            origin = None
            for o in self._active_stamp.keys():
                if o.endswith(f":{gid}"):
                    origin = o
                    break

            if not origin:
                for inst in self.context.platform_manager.get_insts():
                    pid = getattr(getattr(inst, "metadata", None), "id", None)
                    if pid:
                        origin = f"{pid}:GroupMessage:{gid}"
                        break
            if not origin:
                continue

            last_msg_time = self._active_stamp.get(origin, 0.0)
            if last_msg_time == 0:
                self._active_stamp[origin] = now_ts
                continue

            silent_min = (now_ts - last_msg_time) / 60
            default_silent = int(self.cfg.get("群聊冷场判定时间", 120))
            need_silent = int(self._get_group_custom_param(gid, "冷场时间", default_silent))

            # 静默期内连破冰都不许冒头
            if self._is_muted(origin):
                continue

            if silent_min < need_silent:
                continue

            default_prob = float(self.cfg.get("群聊自发破冰概率", 0.35))
            icebreak_prob = float(self._get_group_custom_param(gid, "破冰概率", default_prob))

            if random.random() > icebreak_prob:
                continue

            if not self.motive_mgr:
                continue

            motive = self.motive_mgr.get_candidate_for_group(gid, max_age_hours=36.0)
            if not motive:
                continue

            motive_content = motive.get("content", "")
            logger.info("proactive_life | 命中群破冰 (群:%s, 概率:%.2f, 冷场:%d分) -> %s", gid, icebreak_prob, int(silent_min), motive_content[:30])

            success = await self._post_idle_topic(origin, gid, motive_content)
            if success:
                self._active_stamp[origin] = now_ts
                self._last_spoke_stamp[origin] = now_ts
                self.record_bot_group_message(origin, motive_content)
                self.motive_mgr.mark_group_used(motive["id"], gid)
                logger.info("proactive_life | 群聊冷场破冰成功 -> %s", origin)
                self._log_activity("icebreak", f"冷场破冰：{motive_content[:24]}", origin)
                return True
        return False

    async def _post_idle_topic(self, origin: str, gid: str, topic_content: str) -> bool:
        """调用 MainAgent 带着心事话题在群里冒泡破冰（适配 AstrBot v4.29 新版 build_main_agent）"""
        try:
            try:
                session = MessageSession.from_str(origin)
            except Exception:
                pid = origin.split(":")[0]
                session = MessageSession(
                    platform_name=pid,
                    message_type=MessageType.GROUP_MESSAGE,
                    session_id=gid,
                )

            prompt = (
                f"你注意到群里已经安静很久了（群冷场）。\n"
                f"你最近刚看到或心里惦记着这件事：「{topic_content}」。\n"
                f"{self._life_state_hint()}"
                f"【铁律】绝对不要胡编乱造，严禁无中生有。短句自然，直接输出说话内容，不要报幕，不要带前缀，句末不加句号。"
            )

            ev = CronMessageEvent(
                context=self.context,
                session=session,
                message=prompt,
                sender_id="system",
                sender_name="唯笑",
                message_type=MessageType.GROUP_MESSAGE,
            )

            build_cfg = build_agent_config(self.context, origin, tool_call_timeout=120)
            req = make_request(prompt)
            await attach_history(self.context, req, origin, create_if_missing=True)
            outcome = await run_agent(self.context, ev, req, build_cfg=build_cfg, max_steps=15)
            if not outcome.ok:
                logger.debug("proactive_life | 群破冰未得到有效回复：%s", outcome.reason)
                return False
            reply_text = outcome.text

            reply_text = reply_text.strip().rstrip("。！～~!.")
            await self.context.send_message(session, MessageChain([Plain(reply_text)]))

            try:
                from astrbot.core.proactive_history import append_proactive_message
                await append_proactive_message(
                    self.context, origin, reply_text, source="proactive_life:group"
                )
            except Exception as e_hist:
                logger.warning("proactive_life | 写入群会话历史失败: %s", e_hist)

            return True
        except Exception as e:
            logger.error("proactive_life | 群聊破冰发送异常: %s\n%s", e, traceback.format_exc())
            return False
