import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

# 私聊状态落盘节流：高频私聊时不必每条消息都写盘
_STATE_SAVE_MIN_INTERVAL = 5.0


from astrbot.api import logger
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSession

from .common import parse_quiet_hours
from .handler_base import HandlerContextMixin
from .agent_runner import (
    build_agent_config, make_request, attach_history, run_agent, max_steps_from,
)

_WAKE_PROMPT = """【时间与情境】
- 当前时间：{now_str}（{period_desc}）
- {user_name}约 {silent_min} 分钟没说话了，你手头正好有点空。
{mood_hint}
【这次的开口动机与背景】
{motive_instruction}

【开口规范（只管说什么，怎么写由你自己的人设决定）】
1. 别写成近况汇报（我在哪 + 在做什么 + 在忙什么 + 反问对方在干嘛），也别把私聊当成新闻早报、资讯搬运。
2. 聊具体的事，有真实体感：优先当下的生理与生活状态（刚醒、纠结吃什么、外卖慢了、手头的事卡住）；
   有【心事/话题】就自然带一句，觉得生硬就不提，改聊日常。
3. 以第一人称说话，不解释自己为什么发消息。"""

_NUDGE_PROMPT = """【时间与情境】
- 当前时间：{now_str}（{period_desc}）
- 刚才你在私聊里对{user_name}说了/抛出了下面的话题或提问，但已经约 {elapsed_min} 分钟没有回复了，对话卡在这儿。
{mood_hint}
【刚才你的发言/抛出的问题】
「{question}」

【追问要求】
1. 你心里还挂着刚才那个话题，被放置了一会儿，多少有点在意；语气完全按你自己的人设来，不要自己加设定；
2. 顺着刚才没答完的话题自然追问一句、问一下{user_name}就行，别写成催债；
3. 以第一人称说话，不解释自己为什么发消息。"""

# 当前活动关键词 -> 可聊的方向（日程驱动，只作为建议）
_ACTIVITY_TOPICS = (
    (("游戏", "开黑", "排位", "副本", "上分", "steam"), "游戏卡关、上分破防或队友"),
    (("吃", "饭", "外卖", "午餐", "晚餐", "早餐", "零食", "泡面"), "吃了什么、想吃什么"),
    (("番", "动画", "剧", "电影"), "刚看的那集/剧情"),
    (("画", "稿", "绘画", "画图"), "画画卡住或赶稿"),
    (("睡", "醒", "起床", "午睡", "躺"), "困、刚醒或没睡够"),
    (("工作", "上班", "开会", "加班"), "上班摸鱼或累"),
    (("看", "直播", "视频", "B站", "冲浪"), "刚刷到的内容"),
    (("书", "读", "学习"), "在看的东西"),
)

# 没心事可用时的内置开口动机（避免每次都是同一句「摸鱼发呆」）
# 全部中性、去性别化：具体关系与语气交给已加载的人设（persona_block），
# 不在这里写死「他 / 情侣 / 傲娇」之类的设定。
_BUILTIN_MOTIVES = (
    ("想念", "隔了一阵没说话，忽然有点想找对方聊两句，想随口戳一下。"),
    ("无聊", "手头正好没事，闲得有点发慌，想找个人说句话。"),
    ("报备", "刚做完一件小事（刚吃完/刚收拾完/刚洗完澡），想随口说一声。"),
    ("分享", "刚碰到一件有意思的小事，想随口吐槽一句。"),
    ("小情绪", "想起上次聊天里的某句话，还有点在意，想提一嘴。"),
    ("关心", "不知道对方这会儿在干嘛、吃没吃饭，有点想问一句。"),
)


def _quiet_hours(raw) -> list[int]:
    """把免打扰时段配置解析成小时数列表（统一走 common，避免多份实现漂移）。"""
    return parse_quiet_hours(raw)


def _period_desc(now: datetime) -> str:
    """时段描述。

    只描述「这个时段通常会发生什么」，不写死任何具体爱好/关系设定
    （以前这里是「打游戏推Gal/看番」，换成别的人设就串戏）。
    """
    h = now.hour
    w = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    if 5 <= h < 9:
        return f"{w}清晨（刚醒开机/准备出门/早班路上）"
    if 9 <= h < 11:
        return f"{w}上午（工位摸鱼/早会大概刚完）"
    if 11 <= h < 13:
        return f"{w}中午（准备觅食点外卖/午休摸鱼）"
    if 13 <= h < 17:
        return f"{w}下午（容易犯困/下午茶摸鱼/盯屏幕发呆）"
    if 17 <= h < 19:
        return f"{w}傍晚（快下班/晚高峰/觅食途中）"
    if 19 <= h < 23:
        return f"{w}晚间（自由活动/洗漱/刷手机/看剧）"
    return f"{w}深夜（夜猫子冲浪/熬夜修仙/该睡没睡）"


def _split_message(text: str, target: int = 45, max_segments: int = 5) -> list[str]:
    """兼容旧签名：委托给统一的 rhythm_trimmer.split_message。

    以前这里有自己的一套拆句逻辑，且 max_segments 超限时会把剩余全部拼成
    最后一条（"最后一段一堵墙"）。现在与群聊/回复前处理共用同一套实现。
    """
    from .rhythm_trimmer import split_message

    return split_message(text, target=target, max_segments=max_segments)

def _clean_spoken_text(text: str) -> str:
    t = re.sub(r"```[\s\S]*?```", "", text)
    t = re.sub(r"\{[^{}]*\}", "", t)
    t = re.sub(r"（[^（）]*）|\([^()]*\)", "", t)
    t = re.sub(r"\[dir:\d+\]", "", t)
    t = re.sub(r"\[Image Attachment:.*?\]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_inquiry(text: str) -> bool:
    """启发式检测 Bot 发言是否属于提问、促使对方表态或挂念的话题（升级版，兼容无标点短句口白）。"""
    t = text.strip()
    if not t:
        return False
    # 1. 显式问号
    if "?" in t or "？" in t:
        return True

    # 2. 核心疑问与促问关键词（扩充了口白中常见的无问号疑问）
    prompt_keywords = [
        "选哪个", "选什么", "哪一个", "怎么看", "怎么说", "行不行", "对不对", "好不好",
        "快说", "从实招来", "老实交代", "你说呢", "你觉得", "人呢", "去哪了", "在干嘛",
        "是不是", "到底选", "到底怎么", "给个话", "理我一下", "别装死", "装死是吧",
        "回答我", "说实话", "招不招", "怎么样", "如何", "要不要", "打算", "哪种",
        "认不认", "接不接", "去不去", "看不看", "想不想", "能不能", "可不可",
        "成不成", "哪样", "哪天", "几点", "什么情况", "谁家", "啥样", "啥时候"
    ]
    if any(kw in t for kw in prompt_keywords):
        return True

    # 3. 句末语气词表态识别（兼容正文句末不打终止符号的人设风格）
    # 截取最后一句或最后几个字
    last_clause = re.split(r"[,，\s\n]", t)[-1].strip()
    if last_clause and len(last_clause) >= 2:
        # 句末以疑问语气助词收尾
        if any(last_clause.endswith(suffix) for suffix in ("呢", "吗", "么", "吧", "哈")):
            # 排除非疑问固定词（比如“好吧”、“行吧”）
            if last_clause not in ("好吧", "行吧", "算了吧", "走吧", "去吧", "这样吧"):
                return True
        # “看看...”、“试试...”式征求意见
        if last_clause.startswith(("看看", "瞧瞧", "如何", "怎么样")):
            return True

    return False


class PrivateProactiveHandler(HandlerContextMixin):
    def __init__(self, context, cfg: dict, motive_mgr, state_file: Path):
        self.context = context
        self.cfg = cfg
        self.motive_mgr = motive_mgr
        self.state_file = state_file
        self.state = {
            "last_user_msg": {},
            "last_active": {},
            "daily": {},
            "pending_inquiries": {},
        }
        # 由插件主类注入：写面板活动日志、跨轮情绪
        self.activity_logger = None
        self.mood_store = None
        self._load_state()

    def update_config(self, cfg: dict):
        self.cfg = cfg

    # ------------------------------------------------------------ 内置开口动机
    def _builtin_motive(self) -> tuple[str, str]:
        """没心事可用时挑一个内置动机类型，避免每次同一句开场。"""
        try:
            if not bool(self.cfg.get("拟人_动机多样化", True)):
                return "", ""
            kind, text = random.choice(_BUILTIN_MOTIVES)
            return kind, text
        except Exception:
            return "", ""

    # ------------------------------------------------------------ 日程驱动开口方向
    def _activity_hint(self) -> str:
        """用日程的「此刻在做」给出可聊方向；只读取，失败返回空串。"""
        if not self._module_on("日程"):
            return ""
        try:
            from .common import read_schedule
            st = str(self.cfg.get("日程生成时间", "00:00") or "00:00")
            act = str(read_schedule(st).get("current_activity") or "").strip()
            if not act:
                return ""
            # 只在开头的描述里匹配关键词：日程条目常是长句，后段可能提到别的事
            head = act[:60]
            topic = ""
            for kws, t in _ACTIVITY_TOPICS:
                if any(k in head for k in kws):
                    topic = t
                    break
            short = act[:36] + ("…" if len(act) > 36 else "")
            if topic:
                return f"【此刻的生活场景】你正在「{short}」，适合顺口聊{topic}；不想聊就忽略。\n"
            return f"【此刻的生活场景】你正在「{short}」，可以顺口提一句；不想提就忽略。\n"
        except Exception:
            return ""

    def _load_state(self):
        from .safe_io import sync_read_json

        st = sync_read_json(
            self.state_file, {},
            log_prefix="proactive_life | 私聊状态读取失败，按空状态启动",
        )
        self.state = st if isinstance(st, dict) else {}
        for k in ("last_user_msg", "last_active", "daily", "pending_inquiries"):
            self.state.setdefault(k, {})

    def _save_state(self, raise_on_error: bool = False, force: bool = False):
        """落盘私聊状态。

        内存先更新，写盘按最小间隔节流（高频私聊时避免每条消息都读盘写盘）；
        force=True 用于退出/关键路径，立即落盘。
        """
        now = time.time()
        if not force:
            last = float(getattr(self, "_last_save_at", 0) or 0)
            if now - last < _STATE_SAVE_MIN_INTERVAL:
                self._state_dirty = True
                return
        self._last_save_at = now
        self._state_dirty = False
        # 原子写由 safe_io 统一处理，避免半截 JSON 导致状态清零
        from .safe_io import sync_write_json

        ok = sync_write_json(
            self.state_file, self.state,
            log_prefix="proactive_life | 私聊状态落盘失败",
        )
        if not ok and raise_on_error:
            raise OSError(f"写入 {self.state_file} 失败")

    def flush_state(self) -> None:
        """把节流期间积压的状态写出去（退出/重载时调用）。"""
        if getattr(self, "_state_dirty", False):
            self._save_state(force=True)

    def record_user_message(self, uid: str):
        uid_str = str(uid)
        self.state["last_user_msg"][uid_str] = time.time()
        # 对方回消息了，自动清除该用户的待追问挂念
        if "pending_inquiries" in self.state and uid_str in self.state["pending_inquiries"]:
            self.state["pending_inquiries"].pop(uid_str, None)
            logger.info("proactive_life | 对方已回复消息，解除待追问挂念 -> %s", uid_str)
        self._save_state()
        # 被回应了，情绪转好
        self._mood_apply(uid_str, "replied", "对方回消息")

    def record_bot_message(self, uid: str, text: str):
        """记录 Bot 发送给用户的私聊消息，若有疑问或挂念则注册待追问任务"""
        uid_str = str(uid)
        targets = [str(x).strip() for x in (self.cfg.get("对谁主动") or []) if str(x).strip()]
        if uid_str not in targets:
            return

        clean_txt = _clean_spoken_text(text)
        if _is_inquiry(clean_txt):
            self.state.setdefault("pending_inquiries", {})
            self.state["pending_inquiries"][uid_str] = {
                "question": clean_txt[:300],
                "asked_at": time.time(),
                "nudged_count": 0,
            }
            self._save_state()
            logger.info("proactive_life | 记录待追问话题 -> %s: %s", uid_str, clean_txt[:50])

    def _resolve_umo(self, uid: str) -> str:
        """私聊会话 UMO：平台 id 从运行时取，取不到才回落默认适配器。"""
        from .common import resolve_umo

        return resolve_umo(self.context, "FriendMessage", uid)

    def _read_schedule(self) -> str:
        """读取日程并提炼当前生活状态，供开口参考（只读取，不生成）。"""
        if not self._module_on("日程"):
            return ""
        try:
            from .common import read_schedule
            st = str(self.cfg.get("日程生成时间", "00:00") or "00:00")
            info = read_schedule(st)
            if not info.get("schedule"):
                return ""
            bits = []
            appearance = str(self.cfg.get("日程_外观锚点", "") or "").strip()
            if appearance:
                bits.append(f"固定外观（不随穿搭变化）：{appearance}")
            if info.get("outfit"):
                bits.append(f"今日穿搭：{info['outfit']}")
            if info.get("current_activity"):
                bits.append(f"此刻在做：{info['current_activity']}")
            bits.append(f"今日日程：\n{info['schedule'][:500]}")
            return "【生活状态】" + "\n".join(bits)
        except Exception as e:
            logger.debug("proactive_life | 日程读取失败，本次不带日程上下文: %s", e)
            return ""

    async def _read_recent(self, uid: str) -> str:
        try:
            umo = self._resolve_umo(uid)
            conv_mgr = self.context.conversation_manager
            cid = await conv_mgr.get_curr_conversation_id(umo)
            if not cid:
                return ""
            conv = await conv_mgr.get_conversation(umo, cid)
            if not conv or not conv.history:
                return ""
            hist = json.loads(conv.history)
            n = max(2, int(self.cfg.get("参考几条", 12)))
            out = []
            for m in hist[-n:]:
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                if not content:
                    continue
                who = self._user_name() if role == "user" else self._bot_name()
                out.append(f"{who}: {str(content)[:160]}")
            return "\n".join(out)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            logger.debug("proactive_life | 最近聊天读取失败，本次不带聊天上下文: %s", e)
            return ""

    async def _execute_proactive_agent(self, uid: str, prompt_text: str, source: str) -> bool:
        """核心执行代理：唤醒模型、打字分段发送、TTS与写入上下文历史"""
        umo = self._resolve_umo(uid)
        try:
            session = MessageSession.from_str(umo)
        except Exception as e:
            logger.warning("proactive_life | 私聊会话 UMO 解析失败 uid=%s umo=%s: %s", uid, umo, e)
            return False

        try:
            ev = CronMessageEvent(
                context=self.context,
                session=session,
                message=prompt_text,
                sender_id=uid,
                sender_name=self._bot_name(),
            )

            build_cfg = build_agent_config(self.context, umo, tool_call_timeout=120)
            req = make_request(prompt_text)
            await attach_history(self.context, req, umo)
            outcome = await run_agent(
                self.context, ev, req,
                build_cfg=build_cfg,
                # 主动开口是「说一句话」，不是干活：步数压到 5 足够，
                # 每多一步就多一次模型调用（含工具往返），日常没必要。
                max_steps=max_steps_from(self.cfg, "主动_最大步数", 5),
                total_timeout=240,
                clean_cfg=self.cfg,
            )
            if not outcome.ok:
                logger.debug("proactive_life | 私聊主动未得到有效回复：%s", outcome.reason)
                return False
            text = outcome.text

            # 分段打字：私聊主动用较慢的拟真节奏（旧键 1.2s）
            # 正常回复走「节奏_间隔秒数」(0.2s)，两条链路节奏不同是有意为之：
            # 主动开口更像"想了一下才发"，正常回复可以快。
            simulate_typing = bool(self.cfg.get("模拟分段打字", True))
            try:
                delay = float(self.cfg.get("分段发送延迟", 1.2))
            except (TypeError, ValueError):
                delay = 1.2

            # 统一走 rhythm_trimmer 的清理：连逗号/顿号/分号一起清，
            # 不只清最后一段——中间段留个「，」最像机器。
            from .rhythm_trimmer import human_pause, strip_trailing_punct

            if simulate_typing:
                # 与面板「节奏」配置保持一致，不再用写死的 26/4
                try:
                    target = max(6, int(self.cfg.get("节奏_每段字数", 45) or 45))
                    max_seg = max(1, int(self.cfg.get("节奏_最多几条", 5) or 5))
                except (TypeError, ValueError):
                    target, max_seg = 45, 5
                segs = _split_message(text, target=target, max_segments=max_seg)
                if not segs:
                    segs = [text]

                for idx, seg in enumerate(segs):
                    seg, _n = strip_trailing_punct(seg)
                    if not seg:
                        continue
                    await self.context.send_message(session, MessageChain([Plain(seg)]))
                    if idx < len(segs) - 1 and delay > 0:
                        # 按下一段的字数决定打多久：短句快、长句慢，更像真人在敲
                        await asyncio.sleep(human_pause(delay, segs[idx + 1]))
            else:
                clean, _n = strip_trailing_punct(text)
                await self.context.send_message(
                    session, MessageChain([Plain(clean or text)])
                )

            # 同步写进 conversation 上下文历史
            try:
                from astrbot.core.proactive_history import append_proactive_message

                await append_proactive_message(
                    self.context, umo, text, source=source
                )
            except Exception as e_hist:
                logger.warning("proactive_life | 写入会话历史失败: %s", e_hist)

            return True
        except Exception as e:
            logger.warning("proactive_life | 执行主动/追问流程失败: %s", e)
            return False

    async def check_and_nudge(self, now: datetime) -> bool:
        """检查是否有未答复的提问需要追问。返回 True 表示成功发出了追问。"""
        if not bool(self.cfg.get("私聊追问_启用", True)):
            return False

        quiet = _quiet_hours(self.cfg.get("免打扰时段"))
        if now.hour in quiet:
            return False

        wait_sec = int(self.cfg.get("追问等待秒数", 300))
        max_nudges = int(self.cfg.get("追问最多次数", 1))
        expire_sec = int(self.cfg.get("追问过期秒数", 1800))

        now_ts = time.time()
        targets = [str(x).strip() for x in (self.cfg.get("对谁主动") or []) if str(x).strip()]

        blocked = getattr(self, "is_target_blocked", None)

        for uid in targets:
            # 黑名单校验：和主动开口保持同一套准入
            if callable(blocked) and blocked(uid):
                continue

            inquiry = self.state.get("pending_inquiries", {}).get(uid)
            if not inquiry:
                continue

            asked_at = float(inquiry.get("asked_at", 0) or 0)
            elapsed = now_ts - asked_at

            # 超过过期时间，判定对方确实走开了，丢弃追问任务
            if elapsed > expire_sec:
                self.state["pending_inquiries"].pop(uid, None)
                self._save_state()
                continue

            # 检查提问后用户是否发过消息
            last_user = float(self.state.get("last_user_msg", {}).get(uid, 0) or 0)
            if last_user > asked_at:
                self.state["pending_inquiries"].pop(uid, None)
                self._save_state()
                continue

            # 追问次数已达上限
            nudged_count = int(inquiry.get("nudged_count", 0))
            if nudged_count >= max_nudges:
                continue

            # 已追问过：以「上次追问时间」为基准算冷却，否则第二次会立刻连发
            if nudged_count > 0:
                last_nudged_at = float(inquiry.get("last_nudged_at", 0) or 0)
                if now_ts - last_nudged_at < wait_sec:
                    continue
            elif elapsed < wait_sec:
                # 首次追问：从提问时刻开始等
                continue

            # 执行追问
            spoken = await self._wake_and_nudge(uid, now, inquiry, elapsed)
            if spoken:
                inquiry["nudged_count"] = nudged_count + 1
                inquiry["last_nudged_at"] = now_ts
                if inquiry["nudged_count"] >= max_nudges:
                    self.state["pending_inquiries"].pop(uid, None)
                self._save_state()
                return True
        return False

    async def _wake_and_nudge(
        self, uid: str, now: datetime, inquiry: dict, elapsed: float
    ) -> bool:
        period_desc = _period_desc(now)
        question = inquiry.get("question", "")
        elapsed_min = max(1, int(elapsed / 60))

        prompt_text = _NUDGE_PROMPT.format(
            now_str=now.strftime("%H:%M"),
            period_desc=period_desc,
            elapsed_min=elapsed_min,
            question=question,
            mood_hint=self._mood_hint(uid),
            user_name=self._user_name(),
        )

        ok = await self._execute_proactive_agent(uid, prompt_text, source="proactive_nudge")
        if ok:
            logger.info("proactive_life | 私聊追问成功发出 -> %s (已等 %d 分钟)", uid, elapsed_min)
            self._log_activity("nudge", f"追问 {uid}（已等 {elapsed_min} 分钟）")
            # 追问意味着被晾着，情绪偏委屈
            self._mood_apply(uid, "ignored", "追问没回")
        return ok

    async def check_and_speak(self, now: datetime) -> bool:
        """检查并尝试进行常规私聊主动开口。返回 True 表示成功开口。"""
        if not bool(self.cfg.get("私聊主动_启用", True)):
            return False

        quiet = _quiet_hours(self.cfg.get("免打扰时段"))
        if now.hour in quiet:
            return False

        targets = [str(x).strip() for x in (self.cfg.get("对谁主动") or []) if str(x).strip()]
        blocked = getattr(self, "is_target_blocked", None)
        for uid in targets:
            # 主链："uid" 已是纯 QQ 号；算法与 BlacklistPolicy.is_blacklisted_session 等价
            if callable(blocked) and blocked(uid):
                continue
            spoken = await self._check_single_user(uid, now)
            if spoken:
                return True
        return False

    async def _check_single_user(self, uid: str, now: datetime) -> bool:
        last = float(self.state["last_user_msg"].get(uid, 0) or 0)
        silent_min = (time.time() - last) / 60 if last else 9999
        need = int(self.cfg.get("安静多久才找", 60))
        if silent_min < need:
            return False

        la = float(self.state["last_active"].get(uid, 0) or 0)
        since_active = (time.time() - la) / 60 if la else 9999
        gap = int(self.cfg.get("最短间隔", 180))
        if since_active < gap:
            return False

        today = now.strftime("%Y-%m-%d")
        daily = self.state["daily"].get(uid, {})
        count = int(daily.get("count", 0)) if daily.get("date") == today else 0
        cap = int(self.cfg.get("每天最多几次", 3))
        if count >= cap:
            return False

        p = float(self.cfg.get("触发概率", 0.55))
        if random.random() > p:
            return False

        schedule = self._read_schedule() if bool(self.cfg.get("参考日程", True)) else ""
        recent = await self._read_recent(uid) if bool(self.cfg.get("参考最近聊天", True)) else ""
        period_desc = _period_desc(now)

        candidate_motive = None
        if bool(self.cfg.get("优先消费心事动机", True)) and self.motive_mgr:
            candidate_motive = self.motive_mgr.get_candidate_for_private(recent_chat=recent)

        # 私聊主动不再过模型 YES/NO 判定：概率、冷却、每日上限已经是门槛，
        # 达到就直接开口，避免模型判定把主动权卡死。
        return await self._wake_and_speak(uid, now, silent_min, candidate_motive, schedule, recent, period_desc, today, count)

    async def _wake_and_speak(
        self, uid: str, now: datetime, silent_min: float,
        candidate_motive: dict | None, schedule: str, recent: str, period_desc: str,
        today: str, count: int
    ) -> bool:
        if candidate_motive:
            motive_text = candidate_motive.get("content", "")
            motive_instruction = (
                f"【可选的心事/灵感备选（自然随性，严禁早报复读）】\n"
                f"你刚刚或手头正碰巧琢磨这件事：「{motive_text}」。\n"
                f"你可以随口顺带吐槽一句；如果觉得现在聊这个太突兀或像新闻早报，也可以直接无视它，聊聊你现在的体感（如刚睡醒发呆、琢磨吃什么、天气、游戏）。"
            )
            # 心事分支以前把日程整段丢了：模型只知道心事，不知道自己此刻在做什么、
            # 今天穿什么，一开口就容易脱离生活变成新闻播报。日程作为背景一起给，
            # 并写明「不必复述」，免得模型把日程当成要念的稿子。
            if schedule:
                motive_instruction += (
                    f"\n\n【你的日程与作息状态（背景参考，不必复述）】\n{schedule[:400]}"
                )
        else:
            reason = []
            if schedule:
                reason.append(f"【你的日程与作息状态】\n{schedule[:400]}")
            if recent:
                reason.append(f"【最近你们聊过的话题与残留语境】\n{recent[-500:]}")
            reason_hint = "\n\n".join(reason) if reason else f"【生活状态】手头正好空下来，忽然想找{self._user_name()}说句话。"
            motive_instruction = f"【生活碎碎念参考】\n{reason_hint}"
            # 没有心事时，给一个内置的开口方向，别每次都同一句
            kind, text = self._builtin_motive()
            if kind:
                motive_instruction += (
                    f"\n【这次的开口念头（{kind}）】{text}"
                    f"\n这只是个方向，觉得不合适就按上面的生活状态随便聊。"
                )

        # 日程驱动的开口方向（有就直接给建议，没有就跳过）
        act_hint = self._activity_hint()
        if act_hint:
            motive_instruction = motive_instruction + "\n" + act_hint

        prompt_text = _WAKE_PROMPT.format(
            now_str=now.strftime("%H:%M"),
            period_desc=period_desc,
            silent_min=int(silent_min),
            motive_instruction=motive_instruction,
            mood_hint=self._mood_hint(uid),
            user_name=self._user_name(),
        )

        ok = await self._execute_proactive_agent(uid, prompt_text, source="proactive_life")
        if ok:
            ts = time.time()
            self.state["last_active"][uid] = ts
            self.state["daily"][uid] = {"date": today, "count": count + 1}
            # 强落盘：冷却/每日上限都靠这两个字段。走节流的话，进程重启或
            # 面板刚看完状态时会读到旧值，可能刚说完又立刻开口一次。
            self._save_state(force=True)
            if candidate_motive and self.motive_mgr:
                self.motive_mgr.mark_private_used(candidate_motive["id"])
            logger.info("proactive_life | 私聊主动开口成功 -> %s", uid)
            self._log_activity("private", f"主动私聊开口 → {uid}")
            return True
        return False
