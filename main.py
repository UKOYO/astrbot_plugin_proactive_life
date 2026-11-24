import asyncio
import datetime
import json
import re
import time
import urllib.request
from collections import deque
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .common import DEFAULT_TARGET_UID
from .debounce import DebounceManager, MessageExtractor, attach_finalizer
from .mood import MoodStore
from .schedule_gen import ScheduleGenerator, ScheduleStore
from .motive_pool import MotiveManager
from .private_handler import PrivateProactiveHandler
from .group_handler import GroupProactiveHandler
from .worker_handler import AutonomousWorkerHandler
from . import web_api
from .rhythm_trimmer import (
    has_markdown,
    strip_faces,
    strip_trailing_punct,
    split_message,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    # 模块级总开关：关掉后该模块完全不介入，便于和别的插件共存
    "模块_私聊": True,
    "模块_群聊": True,
    "模块_摸鱼": True,
    "模块_节奏": True,
    "模块_日程": True,
    "模块_上下文": True,
    "模块_防抖": True,

    "全局调度间隔": 10,
    "免打扰时段": [2, 3, 4, 5, 6],
    
    # 私聊主动与追问配置
    "私聊主动_启用": True,
    "私聊追问_启用": True,
    "追问等待秒数": 300,
    "追问最多次数": 1,
    "追问过期秒数": 1800,
    "对谁主动": [DEFAULT_TARGET_UID],
    "安静多久才找": 60,
    "最短间隔": 180,
    "每天最多几次": 3,
    "触发概率": 0.55,
    "参考日程": True,
    "日程生成时间": "07:00",   # 每日自动生成日程的时刻，也作为业务日边界

    # 日程（提示词自动生成）
    "日程_自动生成": True,
    "日程_参考历史天数": 3,
    "日程_参考最近条数": 10,
    "日程_生成模型": "",
    "日程_穿搭风格池": [
        "知性学院风", "街头休闲风", "温柔淑女风", "酷飒中性风", "慵懒居家风",
        "精致约会风", "运动活力风", "日系森女风", "法式优雅风", "韩系甜美风",
        "复古文艺风", "极简都市风", "甜酷混搭风", "民族风情风", "暗黑系风格",
    ],
    "日程_提示词模板": "",

    # 消息防抖（多条消息合并成一批再交给模型）
    "防抖_群聊启用": True,
    "防抖_私聊启用": True,
    "防抖_群聊等待秒": 1.2,
    "防抖_群聊最长等待秒": 2.5,
    "防抖_私聊等待秒": 0.8,
    "防抖_私聊最长等待秒": 1.5,
    "防抖_单次图片上限": 5,
    "防抖_情绪拆分": True,
    "防抖_过滤表情包": True,
    "防抖_锁超时秒": 15.0,
    "参考最近聊天": True,
    "参考几条": 12,
    "优先消费心事动机": True,
    "模拟分段打字": True,
    "分段发送延迟": 1.2,

    # 群聊配置
    "群聊白名单": [],
    "群聊自发破冰_启用": True,
    "群聊冷场判定时间": 120,
    "群聊自发破冰概率": 0.35,
    "群聊插话_启用": False,
    "群聊插话_模式": "probability",
    "群聊插话_概率": 0.05,
    "群聊插话_判定窗口": 30.0,
    "群聊插话_冷却时间": 45.0,
    "群聊插话_饥饿保底条数": 30,
    "群聊插话_保底最短间隔": 12,
    "群聊接话_最短间隔": 8.0,
    "群聊插话_诊断日志": True,
    "群聊插话_闭嘴静默分钟": 15.0,
    "群聊插话_嫌烦静默分钟": 8.0,
    "群聊白名单_概率倍数": 2.5,
    "群聊独立分区配置": [],
    "群聊判定模型": "",
    "场景感知_启用": True,
    "场景感知_让位contextaware": True,  # 原 context_aware 启用时不重复注入 conversation_scene
    "场景感知_对话窗口": 8,        # 注入 LLM 的最近对话流条数
    "场景感知_显示对话流": True,    # 是否在场景提示里列出「谁→谁」
    "场景感知_严格模式": False,     # 主动插入场景强制判为「对群里说」，不推断成在跟 Bot 说
    "场景感知_回复特征词": [],      # Bot 刚回过后，哪些短句算「在回应 Bot」；留空用内置默认
    "场景感知_调试推断": False,     # 推断结果写入 debug 日志
    "场景感知_总结启用": True,      # 每几轮背景提炼约束/纠正/决定/待办，注入时一起给模型
    "场景感知_总结每N条": 12,       # 攒多少条消息触发一次总结（最少 4）
    "场景感知_总结间隔秒": 180,     # 两次总结的最小间隔（最少 30 秒）
    "场景感知_总结条数": 6,         # 每次注入最多几条笔记
    "场景感知_总结模型": "",        # 总结用模型 id，留空跟随当前会话

    # 拟人说话风格（影响群聊接话的 prompt）
    "拟人_话多程度": 50,      # 0~100：低=惜字如金，高=话多爱接
    "拟人_沉默倾向": 45,      # 0~100：高=没话就说、少接，低=乐于回应
    "拟人_吐槽强度": 50,      # 0~100：高=毒舌吐槽，低=温和附和
    "拟人_成员印象": True,     # 累积群成员的说话习惯印象，让回应因人而异
    "拟人_情绪启用": True,      # 跨轮情绪：被冷落/被回应/被喊闭嘴会改变语气
    "拟人_情绪衰减小时": 3.0,   # 情绪多久衰减回「平静」
    "拟人_动机多样化": True,    # 没心事时给内置开口动机（想念/无聊/报备/分享…），避免每次同一句

    # 后台自主摸鱼/干活
    "后台摸鱼_启用": True,
    "摸鱼干活最短间隔": 240,
    "摸鱼干活概率": 0.4,
    "摸鱼_搜索方向": "",        # 自定义摸鱼搜索主题，多行；留空用内置默认
    "摸鱼_搜索方向_模式": "replace",  # replace=完全替代默认；append=优先自定义并保留默认

    # 真人节奏修剪（原 human_rhythm 插件并入）
    "节奏_启用": True,
    "节奏_处理方式": "split",
    "节奏_每段字数": 45,
    "节奏_最多几条": 5,
    "节奏_间隔秒数": 0.2,
    "节奏_去句末标点": True,
    "节奏_去颜文字": True,
    "节奏_表格代码不拆": True,
    "节奏_花括号不拆": True,
    "节奏_只对群聊": False,
    "节奏_打印处理详情": True,
    "节奏_演练模式": False,
}


class _LiveConfig:
    """始终读 AstrBotConfig 的活视图，缺键回落到 DEFAULT_CONFIG。

    以前这里是 `{**DEFAULT_CONFIG, **config}` 的快照，插件启动后再在后台
    改配置，内存里那份永远是旧的。改成视图后，后台和面板改哪边都能立刻
    读到新值，不需要重载插件。
    """

    def __init__(self, owner, defaults: dict):
        self._owner = owner
        self._defaults = defaults

    @property
    def _live(self):
        # 每次现取：万一 AstrBot 换了 config 对象，这里也跟着走。
        # 这里不能用 `or {}` 兜底——AstrBotConfig 为空时是 falsy，
        # 会返回一个临时 dict，__setitem__ 写进去就丢了。
        conf = getattr(self._owner, "_conf", None)
        if conf is None:
            conf = {}
            try:
                self._owner._conf = conf
            except Exception:
                pass
        return conf

    def get(self, key, default=None):
        if key in self._live:
            return self._live[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def __getitem__(self, key):
        if key in self._live:
            return self._live[key]
        return self._defaults[key]

    def __setitem__(self, key, value):
        self._live[key] = value

    def __contains__(self, key):
        return key in self._live or key in self._defaults

    def __iter__(self):
        seen = set(self._live.keys())
        yield from self._live.keys()
        for key in self._defaults:
            if key not in seen:
                yield key

    def __len__(self):
        return len(set(self._live.keys()) | set(self._defaults.keys()))

    def keys(self):
        return list(iter(self))

    def items(self):
        return [(key, self[key]) for key in self]

    def values(self):
        return [self[key] for key in self]

    def update(self, other=None, **kwargs):
        self._live.update(dict(other or {}, **kwargs))

    def copy(self):
        return {key: self[key] for key in self}


@register(
    "astrbot_plugin_proactive_life",
    "keyou",
    "自主生活与主动中枢",
    "1.3.0",
    "融合私聊主动/追问、群聊破冰/插话、后台摸鱼、真人节奏、日程生成、消息防抖与上下文场景的一体化活人感中枢",
)
class ProactiveLifePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._conf = config
        self.cfg = _LiveConfig(self, DEFAULT_CONFIG)
        self.motive_pool_file = StarTools.get_data_dir("astrbot_plugin_proactive_life") / "motive_pool.json"
        self.state_file = StarTools.get_data_dir("astrbot_plugin_proactive_life") / "private_state.json"
        
        self.motive_mgr = MotiveManager(self.motive_pool_file)
        self.private_h = PrivateProactiveHandler(context, self.cfg, self.motive_mgr, self.state_file)
        self.group_h = GroupProactiveHandler(context, self.cfg, self.motive_mgr)
        self.worker_h = AutonomousWorkerHandler(context, self.cfg, self.motive_mgr)
        # 日程：自产（提示词生成）存中樞 data 目录；读取时回退到 life_scheduler
        self.schedule_store = ScheduleStore(
            StarTools.get_data_dir("astrbot_plugin_proactive_life") / "schedule_data.json"
        )
        self.schedule_gen = ScheduleGenerator(context, self.cfg, self.schedule_store)
        self._last_schedule_day: str = ""
        # 消息防抖：多条消息合并成一批再交给模型
        self.debounce = DebounceManager(self.cfg)
        # 跨轮情绪：让开口有连续性（衰减时长取自配置，改配置后重载生效）
        try:
            _decay = float(self.cfg.get("拟人_情绪衰减小时", 3.0) or 3.0)
        except (TypeError, ValueError):
            _decay = 3.0
        self.mood = MoodStore(decay_hours=_decay)
        # 让各 handler 能把成功事件写进面板活动日志、读写情绪
        for _h in (self.private_h, self.group_h, self.worker_h):
            try:
                _h.activity_logger = self.log_activity
                _h.mood_store = self.mood
            except Exception:
                pass

        self._rhythm_last: dict = {}
        self._main_loop_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_macro_tick: float = 0.0
        # 轻量活动日志：记录最近发生的事，供总览展示（内存、有界）
        self._activity: deque = deque(maxlen=30)

    def log_activity(self, kind: str, text: str, origin: str = "") -> None:
        """记录一条活动，供面板「最近活动」展示。

        kind: private/group/worker/nudge/icebreak 等，前端按此映射中文标签。
        """
        try:
            self._activity.append({
                "at": time.time(),
                "kind": str(kind or "other"),
                "text": str(text or "")[:120],
                "origin": str(origin or ""),
            })
        except Exception:
            pass

    def _module_on(self, name: str) -> bool:
        """模块级总开关（与 handler 共用同一套判定）。"""
        from .handler_base import module_enabled

        return module_enabled(self.cfg, name)

    def _rhythm_get(self, key: str, default=None):
        """节奏项取值。

        老配置里这些键曾经不带「节奏_」前缀，所以先查全名、再查短名，
        都查不到才回落 DEFAULT_CONFIG。之前是先查 self.cfg，而
        DEFAULT_CONFIG 里全是全名，短名分支永远走不到，等于死代码。
        """
        live = self.cfg._live
        if key in live:
            return live[key]
        short = key.replace("节奏_", "")
        if short != key and short in live:
            return live[short]
        if key in DEFAULT_CONFIG:
            return DEFAULT_CONFIG[key]
        if short in DEFAULT_CONFIG:
            return DEFAULT_CONFIG[short]
        return default

    def _rhythm_on(self, key: str) -> bool:
        return bool(self._rhythm_get(key, False))

    def _rhythm_num(self, key: str) -> int:
        try:
            return int(self._rhythm_get(key, 0) or 0)
        except (TypeError, ValueError):
            return int(DEFAULT_CONFIG.get(key, 0))

    def _rhythm_target(self) -> int:
        return max(6, self._rhythm_num("节奏_每段字数"))

    async def initialize(self):
        self._stop_event.clear()
        self._last_macro_tick = time.time()
        self._main_loop_task = asyncio.create_task(self._coordinator_loop())
        try:
            web_api.register(self)
        except Exception as exc:  # 注册失败不该拖垮中枢
            logger.warning(f"proactive_life | 面板接口注册失败: {exc}")
        # 启动时把与其它插件的功能重叠打出来，省得用户翻面板才发现双重作用
        try:
            for c in web_api._conflicts_block(self):
                logger.warning("proactive_life | 冲突提示: %s", c.get("text", ""))
        except Exception:
            pass
        logger.info("proactive_life | 自主生活与主动中枢已启动，协调节律与追问中枢已激活")

    async def terminate(self):
        self._stop_event.set()
        if self._main_loop_task and not self._main_loop_task.done():
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("proactive_life | 协调中枢退出时出现异常", exc_info=True)
        self._main_loop_task = None
        try:
            self.debounce.clear()
        except Exception:
            pass
        logger.info("proactive_life | 自主生活与主动中枢已停止")

    # ---------- 消息事件监听 ----------

    # ---------- 消息防抖（原 smart_debounce 插件并入） ----------
    # 优先级最高：先拦消息决定「合并」还是「放行」，放行后才轮到后面的主动逻辑。

    def _debounce_on(self, kind: str) -> bool:
        if not self._module_on("防抖"):
            return False
        key = "防抖_群聊启用" if kind == "group" else "防抖_私聊启用"
        try:
            return bool(self.cfg.get(key, True))
        except Exception:
            return True

    def _is_wake_command(self, text: str) -> bool:
        """带唤醒前缀的消息当命令处理，不参与合并。"""
        t = (text or "").strip()
        if not t:
            return False
        try:
            cfg = self.context.get_config()
            prefixes = cfg.get("wake_prefix", ["/"])
        except Exception:
            prefixes = ["/"]
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        return any(t.startswith(p) for p in prefixes if p)

    async def _debounce_handle(self, event: AstrMessageEvent, kind: str) -> None:
        raw = (getattr(event, "message_str", "") or "").strip()
        if self._is_wake_command(raw):
            return
        if kind == "group" and not getattr(event, "is_at_or_wake_command", False):
            return

        extractor = MessageExtractor(
            filter_stickers=bool(self.cfg.get("防抖_过滤表情包", True))
        )
        try:
            item = extractor.extract(event, enable_emotion_split=(kind == "group"))
        except Exception as e:
            logger.debug("proactive_life | 防抖抽取消息失败，按原样放行: %s", e)
            return
        if not (item.text or item.image_urls or item.reply_text or item.reply_image_urls):
            return

        umo = event.unified_msg_origin
        pipe = self.debounce.pipeline(umo)
        try:
            payload = await pipe.ingest(item)
        except Exception as e:
            logger.warning("proactive_life | 防抖合并失败，按原样放行: %s", e)
            return
        if payload is None:
            # 已并入当前批次：这条不单独回复，但记账不能丢，
            # 否则场景感知/群上下文/静音判定会看不到这些被合并的消息。
            try:
                if kind == "group":
                    self.group_h.on_group_message_received(event)
                elif self._module_on("私聊"):
                    self.private_h.record_user_message(str(event.get_sender_id()))
            except Exception as e:
                logger.debug("proactive_life | 防抖吸收消息记账失败: %s", e)
            event.stop_event()
            return

        event.set_extra("防抖_payload", payload)
        event.set_extra("防抖_lock_token", payload.lock_token)
        attach_finalizer(event, pipe.lease_lock, payload.lock_token)
        self.log_activity(
            "debounce", f"合并 {payload.count} 条消息后发车", umo
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=999999)
    async def on_group_debounce(self, event: AstrMessageEvent):
        """群聊消息防抖：艾特/唤醒时把连发的多条消息合并成一批，等封口后一次性交给模型。

        合并期间到达的后续消息会被吸收（不单独回复，但仍记入场景与上下文）。
        """
        if self._stop_event.is_set():
            return
        if not self._debounce_on("group"):
            return
        await self._debounce_handle(event, "group")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999999)
    async def on_private_debounce(self, event: AstrMessageEvent):
        """私聊消息防抖：对方连发几条时合并理解，避免只回最后半句。"""
        if self._stop_event.is_set():
            return
        if not self._debounce_on("private"):
            return
        await self._debounce_handle(event, "private")

    @filter.on_llm_request()
    async def on_debounce_llm_request(self, event: AstrMessageEvent, req):
        """把合并后的 prompt / 图片覆盖进请求。"""
        try:
            payload = event.get_extra("防抖_payload")
        except Exception:
            payload = None
        if not payload:
            return
        pipe = self.debounce.get_if_exists(event.unified_msg_origin)
        if pipe is not None:
            pipe.lease_lock.renew(payload.lock_token, ttl=60.0, stage="llm_inference")
        req.prompt = payload.prompt
        # 必须无条件覆盖：宿主已把表情包 URL 塞进 req.image_urls，不覆盖过滤就白做了
        req.image_urls = list(payload.image_urls)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=9000)
    async def on_private_message(self, event: AstrMessageEvent):
        """记录私聊来信时间，作为「安静多久才找」的计时依据。"""
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        if not self._module_on("私聊"):
            return
        uid = str(event.get_sender_id())
        self.private_h.record_user_message(uid)

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        """发送消息后：释放防抖租约、记录唯笑的发言，开启私聊智能追问与群聊接话窗口。"""
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        # 防抖租约释放：无论成功失败都要放，否则会话会卡住
        try:
            _tok = event.get_extra("防抖_lock_token")
            if _tok:
                _pipe = self.debounce.get_if_exists(event.unified_msg_origin)
                if _pipe is not None:
                    _pipe.lease_lock.release(_tok, "normal")
        except Exception:
            pass
        try:
            res = event.get_result()
            if not res:
                return
            txt = res.get_plain_text() if hasattr(res, "get_plain_text") else ""
            if not txt:
                return

            if event.is_private_chat():
                if not self._module_on("私聊"):
                    return
                uid = str(event.get_sender_id() or "")
                self.private_h.record_bot_message(uid, txt)
            else:
                if not self._module_on("群聊"):
                    return
                # 记录群聊发言，激活接话时间窗口
                origin = event.unified_msg_origin
                self.group_h.record_bot_group_message(origin, txt)
        except Exception as e:
            logger.debug(f"proactive_life | after_message_sent 记录异常: {e}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群聊插话：判断是否该接话（直接接话 / 话题跟进 / 概率插话），必要时生成回复。"""
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        if not self._module_on("群聊"):
            return
        try:
            self.group_h.on_group_message_received(event)
            async for resp in self.group_h.handle_group_message_passive(event):
                yield resp
        except Exception as e:
            logger.warning(f"proactive_life | 群聊插话链路异常: {e}")

    # ---------- 摸鱼冲浪工具（原 web_surfer 插件并入） ----------

    @staticmethod
    async def _fetch_json(url: str) -> dict:
        """在 thread 裡做同步 HTTP，避免阻塞事件循環。"""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @filter.llm_tool(name="pl_bilibili_hot")
    async def browse_bilibili_hot(self, event: AstrMessageEvent, count: int = 5) -> str:
        """刷B站热门视频榜单。当你手头闲着、想看看B站大家在看什么、或者想找点好玩的话题跟优可吐槽时调用。

        Args:
            count(number): 获取几条热门视频，默认5条，范围1到10。
        """
        try:
            count = min(max(1, int(count or 5)), 10)
            url = f"https://api.bilibili.com/x/web-interface/popular?ps={count}&pn=1"
            data = await asyncio.to_thread(self._fetch_json, url)
            if data.get("code") != 0:
                return f"刷B站失败：API返回 {data.get('message')}"

            items = data.get("data", {}).get("list", [])[:count]
            results = []
            for it in items:
                title = it.get("title", "")
                up = it.get("owner", {}).get("name", "")
                desc = (it.get("desc", "") or "").replace("\n", " ")[:60]
                bvid = it.get("bvid", "")
                tname = it.get("tname", "")
                results.append(f"【{tname}】{title} (UP主: {up}, BV: {bvid})\n简介: {desc}")
            return "当前B站热门前列：\n" + "\n\n".join(results)
        except Exception as e:
            logger.warning(f"[WebSurfer] 刷B站出错: {e}")
            return f"刷B站出错啦: {e}"

    @filter.llm_tool(name="pl_tieba_hot")
    async def browse_tieba_hot(self, event: AstrMessageEvent, count: int = 5) -> str:
        """逛百度贴吧热议话题榜。想看看贴吧老哥在讨论什么时事、大瓜、游戏或烂梗时调用。

        Args:
            count(number): 获取几条热议话题，默认5条，范围1到10。
        """
        try:
            count = min(max(1, int(count or 5)), 10)
            url = "https://tieba.baidu.com/hottopic/browse/topicList"
            data = await asyncio.to_thread(self._fetch_json, url)
            if data.get("errno") != 0:
                return f"逛贴吧失败：errno {data.get('errno')}"

            topics = data.get("data", {}).get("bang_topic", {}).get("topic_list", [])[:count]
            results = []
            for t in topics:
                name = t.get("topic_name", "")
                desc = (t.get("topic_desc", "") or "").replace("\n", " ")[:60]
                results.append(f"· {name}\n  概况: {desc}")
            return "当前贴吧热议话题：\n" + "\n".join(results)
        except Exception as e:
            logger.warning(f"[WebSurfer] 逛贴吧出错: {e}")
            return f"逛贴吧出错啦: {e}"

    # ---------- 统一节律中枢协程 ----------

    async def _coordinator_loop(self):
        # 外层 supervisor：单次循环崩溃后自动重启，避免中枢永久死亡
        while not self._stop_event.is_set():
            try:
                await self._coordinator_once()
                return  # 正常退出（收到 stop）
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("proactive_life | 协调中枢异常退出，30 秒后自动重启: %s", e, exc_info=True)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass

    async def _coordinator_once(self):
        # 启动后先等待 60 秒供其它插件（TTS、Provider等）完全装载
        await asyncio.sleep(60)
        self._last_macro_tick = time.time()

        while not self._stop_event.is_set():
            now = datetime.datetime.now()

            # 1. 优先执行私聊追问检查（超低开销，秒级响应未回答提问）
            try:
                if self._module_on("私聊"):
                    await self.private_h.check_and_nudge(now)
            except Exception as e:
                logger.warning(f"proactive_life | 私聊追问巡检异常: {e}")

            # 1.5 到点就生成当天日程（每天一次，失败不重试，避免刷模型）
            try:
                await self._maybe_generate_schedule(now)
            except Exception as e:
                logger.warning(f"proactive_life | 日程自动生成异常: {e}")

            # 2. 判断是否到达宏观节律轮询周期（冷场主动/群聊破冰/后台摸鱼）
            now_ts = time.time()
            macro_interval = max(3, int(self.cfg.get("全局调度间隔", 10))) * 60
            if now_ts - self._last_macro_tick >= macro_interval:
                self._last_macro_tick = now_ts
                try:
                    await self._macro_tick(now)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"proactive_life | 中枢宏观节律轮询异常: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def _maybe_generate_schedule(self, now: datetime.datetime) -> None:
        """到设定的生成时间后，为当天生成一次日程。

        每天只尝试一次（失败也不重试），面板可手动重新生成。
        """
        if not self._module_on("日程"):
            return
        if not bool(self.cfg.get("日程_自动生成", True)):
            return
        try:
            hh, mm = (int(x) for x in str(self.cfg.get("日程生成时间", "07:00") or "07:00").split(":", 1))
        except Exception:
            hh, mm = 7, 0
        if (now.hour, now.minute) < (hh, mm):
            return
        day = now.strftime("%Y-%m-%d")
        if day == self._last_schedule_day:
            return
        self._last_schedule_day = day  # 先占位，避免每 30 秒重复触发
        existing = self.schedule_store.get(day)
        if existing and existing.status == "ok":
            return
        data = await self.schedule_gen.generate(now, umo=None)
        if data.status == "ok":
            self.log_activity("schedule", f"自动生成日程：{data.outfit_style or '—'}")
        else:
            logger.warning("proactive_life | 当天日程自动生成失败，可在面板手动重试")

    async def _macro_tick(self, now: datetime.datetime) -> dict:
        """跑一轮调度。返回各模块结果，供面板「手动跑一轮」展示原因。"""
        result = {"private": False, "group": False, "worker": False, "skipped": []}

        # 调度优先级与全局互斥：
        # 1. 优先判定私聊（与最重要的人开口）
        if self._module_on("私聊"):
            result["private"] = await self.private_h.check_and_speak(now)
            if result["private"]:
                return result
        else:
            result["skipped"].append("私聊")

        # 2. 其次判定群聊冷场破冰
        if self._module_on("群聊"):
            result["group"] = await self.group_h.check_idle_groups()
            if result["group"]:
                return result
        else:
            result["skipped"].append("群聊")

        # 3. 若私聊群聊都没开口，且处于空闲，触发后台自主摸鱼干活（逛贴吧/B站吃瓜并沉淀成果）
        if self._module_on("摸鱼"):
            result["worker"] = await self.worker_h.check_and_work(now)
        else:
            result["skipped"].append("摸鱼")
        return result

    # ---------- 真人节奏修剪（原 human_rhythm 插件并入） ----------

    def _rhythm_plain_chain(self, result):
        """驗證 result 是否為純文字 LLM 回覆，回傳 (full, plain_idx) 或 None。"""
        from astrbot.core.message.components import Plain

        plain_idx = [i for i, c in enumerate(result.chain) if isinstance(c, Plain)]
        # 有非文本组件（图片/@/表情）就不拆，拆了会错位
        if len(plain_idx) != len(result.chain):
            logger.debug("[节奏修剪] 含非文本组件，不拆条")
            return None
        if not plain_idx:
            return None
        full = "".join(result.chain[i].text for i in plain_idx)
        if not full.strip():
            return None
        # 干活场景直接放过（代码/表格/列表）
        if self._rhythm_on("节奏_表格代码不拆") and has_markdown(full):
            logger.debug("[节奏修剪] 含 Markdown 结构，不拆条")
            return None
        return full, plain_idx

    def _rhythm_prepare(self, full: str) -> tuple[str, list[str]]:
        """純文字修飾：去颜文字、合并空白；回傳 (text, hits)。"""
        hits: list[str] = []
        text = full
        if self._rhythm_on("节奏_去颜文字"):
            text, n = strip_faces(text)
            if n:
                hits.append(f"颜文字×{n}")
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        return (text or full), hits

    def _rhythm_replace_single(self, result, plain_idx, new_text: str, full: str) -> None:
        """把单条结果写回原 chain；演练模式或等于原文时不动。"""
        if self._rhythm_on("节奏_演练模式") or new_text == full:
            return
        result.chain[plain_idx[0]].text = new_text
        for i in plain_idx[1:]:
            result.chain[i].text = ""

    def _rhythm_set_last(self, mode: str, full: str, hits: list[str], segments=None) -> None:
        """统一记录上次处理结果，供面板与「节奏」命令读取。"""
        if segments is None:
            self._rhythm_last = {"处理方式": mode, "before": len(full),
                                 "segments": 1, "hits": hits}
        else:
            self._rhythm_last = {
                "处理方式": mode,
                "before": len(full),
                "segments": len(segments),
                "lens": [len(s) for s in segments],
                "hits": hits,
            }

    async def _rhythm_send_rest(
        self, event, rest: list[str], gap: float, result, plain_idx, fallback_text: str
    ) -> None:
        """非同步补发其余气泡；创建任务失败时回退整条发送。"""

        async def _send_rest():
            for seg in rest:
                if gap:
                    await asyncio.sleep(gap)
                try:
                    await event.send(event.plain_result(seg))
                except Exception as exc:
                    logger.warning("[节奏修剪] 补发失败: %s", exc)
                    return

        try:
            asyncio.create_task(_send_rest())
        except Exception as exc:
            logger.warning("[节奏修剪] 无法创建补发任务，回退整条发送: %s", exc)
            result.chain[plain_idx[0]].text = fallback_text

    @filter.on_decorating_result(priority=-600)
    async def reshape(self, event: AstrMessageEvent) -> None:
        """回复前处理：把长回复拆成短句分段发送，并去掉句末标点与颜文字。"""
        if not self._module_on("节奏"):
            return
        if not self._rhythm_on("节奏_启用"):
            return

        if self._rhythm_on("节奏_只对群聊"):
            try:
                if not event.get_group_id():
                    return
            except AttributeError as e:
                # 无法判定群聊时维持原行为：继续处理（由后续 chain 检查兜底）
                logger.debug("[节奏修剪] 无法判定是否群聊: %s", e)

        result = event.get_result()
        if result is None or not getattr(result, "chain", None):
            return

        # 只处理 LLM 结果，不动插件自己发的卡片/图片
        try:
            if not result.is_llm_result():
                return
        except AttributeError as e:
            # 无法判定时维持原行为：继续处理
            logger.debug("[节奏修剪] 无法判定是否 LLM 结果: %s", e)

        parsed = self._rhythm_plain_chain(result)
        if parsed is None:
            return
        full, plain_idx = parsed
        text, hits = self._rhythm_prepare(full)
        mode = str(self._rhythm_get("节奏_处理方式", "split")).lower()

        def _single(new_text: str, fmt: str, *args) -> None:
            """单条分支的统一收尾：记录状态 -> 打印详情 -> 写回 chain。"""
            self._rhythm_set_last(mode, full, hits)
            if self._rhythm_on("节奏_打印处理详情"):
                logger.info(fmt, *args)
            self._rhythm_replace_single(result, plain_idx, new_text, full)

        # 多行回复即使总字数不超也要进拆分流程，让每行走一个气泡
        line_count = len([ln for ln in text.split("\n") if ln.strip()])

        # ---- 只改标点 / 本来就不长（且不是多行）----
        if mode == "punctuation" or (
                len(text) <= self._rhythm_target() and line_count < 2):
            if self._rhythm_on("节奏_去句末标点"):
                text, n = strip_trailing_punct(text)
                if n:
                    hits.append("去句末标点")
            if not text or not re.search(r"[\w\u4e00-\u9fff]", text):
                text = full
            _single(text, "[节奏修剪] %d 字，单条不拆 | %s",
                    len(full), ", ".join(hits) or "无改动")
            return

        # ---- 旧截断模式 ----
        if mode == "trim":
            max_c = self._rhythm_num("节奏_每段字数")
            if len(text) > max_c:
                text = text[:max_c].rstrip("，,、 ")
                hits.append(f"截断{len(full)}→{len(text)}（内容不完整）")
            if self._rhythm_on("节奏_去句末标点"):
                text, n = strip_trailing_punct(text)
            _single(text, "[节奏修剪] 截断 %d→%d 字 | %s",
                    len(full), len(text), ", ".join(hits))
            return

        # ---- split 模式：拆条 ----
        segments = split_message(
            text, self._rhythm_target(), self._rhythm_num("节奏_最多几条"),
            protect_brace=self._rhythm_on("节奏_花括号不拆"))
        if len(segments) <= 1:
            seg = segments[0] if segments else text
            if self._rhythm_on("节奏_去句末标点"):
                seg, n = strip_trailing_punct(seg)
                if n:
                    hits.append("去句末标点")
            _single(seg, "[节奏修剪] %d 字，无需拆分 | %s",
                    len(full), ", ".join(hits) or "无改动")
            return

        # 每条都是独立气泡，句末标点逐条抠（问号保留），中间不再留断点标点
        if self._rhythm_on("节奏_去句末标点"):
            cleaned: list[str] = []
            stripped = 0
            for seg in segments:
                seg2, n = strip_trailing_punct(seg)
                stripped += n
                cleaned.append(seg2 if seg2.strip() else seg)
            segments = cleaned
            if stripped:
                hits.append(f"去句末标点×{stripped}")

        self._rhythm_set_last(mode, full, hits, segments=segments)
        if self._rhythm_on("节奏_打印处理详情"):
            logger.info(
                "[节奏修剪] 拆分 %d 字 → %d 条 %s | %s",
                len(full), len(segments),
                str([len(s) for s in segments]), ", ".join(hits) or "无改动")

        if self._rhythm_on("节奏_演练模式"):
            return

        # 第一条留在原 chain，其余异步补发
        result.chain[plain_idx[0]].text = segments[0]
        for i in plain_idx[1:]:
            result.chain[i].text = ""

        rest = segments[1:]
        if not rest:
            return

        gap = max(0.0, float(self._rhythm_get("节奏_间隔秒数", 0.2) or 0))
        if len(segments) > 6:
            # 长文条数多，别按打字节奏慢慢磨，不然发一分钟
            gap = min(gap, 0.3)

        await self._rhythm_send_rest(event, rest, gap, result, plain_idx, text)
