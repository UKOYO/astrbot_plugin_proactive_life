import asyncio
import datetime
import json
import time
import urllib.request
from collections import deque
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .identity import Identity, from_config as identity_from_config
from .life_inject import build_life_state_block, inject_life_state
from .debounce import DebounceManager, MessageExtractor, attach_finalizer
from .modules.blacklist import BlacklistPolicy, silence_event
from .modules.recall_cancel import RecallStateManager
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
from .rhythm_mixin import RhythmMixin

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

from .config_defaults import DEFAULT_CONFIG


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
    "1.5.0",
    "一体化活人感中枢：贯通入站准入、主动生活、天气穿搭日程、人物情绪与群黑话感知、出站清洗与真人节奏",
)
class ProactiveLifePlugin(Star, RhythmMixin):
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
        self.blacklist = BlacklistPolicy(self.cfg)
        self.recall_state = RecallStateManager()
        # 跨会话「人」档案：群聊/私聊共用一份，注入时按可见域裁剪防串味
        try:
            from .modules.person_memory import PersonMemory

            self.person_memory = PersonMemory(
                path=StarTools.get_data_dir("astrbot_plugin_proactive_life") / "person_memory.json"
            ) if bool(self.cfg.get("拟人_跨会话记忆", True)) else None
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 人物档案初始化失败: %s", e)
            self.person_memory = None
        from .modules.interact_context import InteractContext
        self.interact_ctx = InteractContext(
            window=int(self.cfg.get("戳一戳_上下文条数", 6) or 6),
            ttl=float(self.cfg.get("戳一戳_上下文秒数", 20) or 20),
        )
        # 主动私聊/追问目标过滤：黑名单命中则跳过。
        # 注意：这里必须在 lambda 内部实时读配置，否则面板开关不会生效。
        self.private_h.is_target_blocked = lambda uid: (
            self._module_on("黑名单")
            and bool(self.cfg.get("模块_黑名单_拦主动", True))
            and self.blacklist.is_blacklisted_session(f"aiocqhttp:FriendMessage:{uid}")
        )
        # 角色身分：先按配置建好，persona 文本在 initialize() 异步补齐
        self.identity = identity_from_config(self.cfg)
        for _h in (self.private_h, self.group_h, self.worker_h, self.schedule_gen):
            try:
                _h.identity = self.identity
            except Exception:
                pass
        # 跨轮情绪：让开口有连续性（衰减时长取自配置，改配置后重载生效）
        try:
            _decay = float(self.cfg.get("拟人_情绪衰减小时", 3.0) or 3.0)
        except (TypeError, ValueError):
            _decay = 3.0
        self.mood = MoodStore(
            decay_hours=_decay,
            persist_path=str(
                StarTools.get_data_dir("astrbot_plugin_proactive_life") / "mood_state.json"
            ) if bool(self.cfg.get("拟人_情绪落盘", True)) else None,
        )
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





    async def initialize(self):
        self._stop_event.clear()
        self._last_macro_tick = time.time()
        # 角色身分：按所选 AstrBot 人设读取，并加载插件专用提示词
        await self._load_persona()
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
        # 人物档案：退出时落盘
        try:
            if getattr(self, "person_memory", None) is not None:
                self.person_memory.save()
        except Exception:
            pass
        # 私聊状态：把节流期间积压的写出去
        try:
            self.private_h.flush_state()
        except Exception:
            pass
        # 情绪落盘：退出时强制保存一次，避免防抖窗口内的更新丢失
        try:
            self.mood.save(force=True)
        except Exception:
            pass
        # 取消群聊的 fire-and-forget 任务（总结/闭嘴判定），避免卸载后还在跑
        try:
            self.group_h.cancel_background_tasks()
        except Exception:
            pass
        # 节奏补发任务同样纳入终止清理
        try:
            self.cancel_rhythm_tasks()
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

        filter_stickers = (
            bool(self.cfg.get("防抖_私聊过滤表情包", False))
            if kind == "private"
            else bool(self.cfg.get("防抖_过滤表情包", True))
        )
        extractor = MessageExtractor(filter_stickers=filter_stickers)
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

    @filter.event_message_type(filter.EventMessageType.ALL, priority=2_000_000)
    async def on_recall_event(self, event: AstrMessageEvent):
        """撤回取消：OneBot 撤回通知到达时清理防抖缓冲并标记已撤回。"""
        if self._stop_event.is_set() or not self._module_on("撤回取消"):
            return
        try:
            from .modules.recall_cancel import (
                is_recall_event, cleanup_debounce,
            )

            hit, mid, operator = is_recall_event(event)
            if not hit:
                return
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            n = cleanup_debounce(self.debounce, umo)
            await self.recall_state.mark_recalled(umo, mid, operator)
            logger.debug(
                "proactive_life | 撤回 %s（操作者 %s），清理防抖 %d 条", mid, operator, n
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 撤回处理异常，按未处理跳过: %s", e)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=999999)
    async def on_group_debounce(self, event: AstrMessageEvent):
        """群聊消息防抖：艾特/唤醒时把连发的多条消息合并成一批，等封口后一次性交给模型。

        合并期间到达的后续消息会被吸收（不单独回复，但仍记入场景与上下文）。
        """
        if self._stop_event.is_set():
            return
        # 黑名单准入：最高优先级先于防抖，命中直接中止事件
        if self._module_on("黑名单") and self.blacklist.is_blacklisted_event(event):
            silence_event(event, self.blacklist)
            return
        if not self._debounce_on("group"):
            return
        await self._debounce_handle(event, "group")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999999)
    async def on_private_debounce(self, event: AstrMessageEvent):
        """私聊消息防抖：对方连发几条时合并理解，避免只回最后半句。"""
        if self._stop_event.is_set():
            return
        if self._module_on("黑名单") and self.blacklist.is_blacklisted_event(event):
            silence_event(event, self.blacklist)
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
            # 推理阶段的续租 TTL 可配（默认 120s）：慢模型 + 多轮工具调用
            # 超过 60s 时，看门狗会提前放锁，导致同一会话并发冲进 LLM。
            try:
                infer_ttl = float(self.cfg.get("防抖_推理续租秒", 120) or 120)
            except (TypeError, ValueError):
                infer_ttl = 120.0
            pipe.lease_lock.renew(
                payload.lock_token, ttl=max(30.0, infer_ttl), stage="llm_inference"
            )
        req.prompt = payload.prompt
        # 若未过滤表情包且没有吸收消息带来的附加图，优先复用宿主已预处理好的本地图/动图拼帧，
        # 避免覆写回原始远程 URL 导致重复下载及残留 “not included in this request” 提示。
        existing_images = list(getattr(req, "image_urls", None) or [])
        can_reuse_prepared = (
            not payload.filtered_stickers
            and not payload.has_extra_merged_images
            and bool(payload.image_urls)
            and len(existing_images) == len(payload.image_urls)
        )
        if not can_reuse_prepared:
            req.image_urls = list(payload.image_urls)

    @filter.on_llm_request(priority=-900)
    async def on_life_state_llm_request(self, event: AstrMessageEvent, req):
        """被动回复链路：把「此刻的生活状态 / 今日穿搭 / 日程」挂进本次请求。

        用户主动发消息走的是 AstrBot 正常回复链路，system prompt 里只有人设，
        模型并不知道自己今天穿什么、现在在做什么。这里补上，让被动回复和主动
        开口用的是同一套生活设定。

        优先级 -900 是算过的：`persona_guard`（100）的伪造标签清洗已经跑完，
        我们挂上去的内容不会被它剥掉；`nsfw_restorer`（-1000）的脱敏还没跑，
        挂上去的文字同样会被它过一遍，不会成为绕过脱敏的口子。
        """
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        try:
            # 说话方式硬约束：低智模型容易退化成解说员，放在临时块最前面
            try:
                from .life_inject import inject_temp_part
                from .modules.speech_style import MARKER as SPEECH_MARKER, speech_style_block

                # 取当前会话真实模型名，用于追加一句针对性避坑
                model_name = ""
                try:
                    from .common import get_chat_provider

                    umo = str(getattr(event, "unified_msg_origin", "") or "")
                    prov = get_chat_provider(self.context, umo or None)
                    if prov is not None:
                        model_name = str(prov.get_model() or "")
                except Exception:  # noqa: BLE001
                    pass

                style = speech_style_block(self.cfg, model_name)
                if style:
                    inject_temp_part(req, style, SPEECH_MARKER)
            except Exception as e:  # noqa: BLE001
                logger.debug("proactive_life | 说话方式注入失败: %s", e)
            # 情绪 / 心事与日程共用同一份临时注入块（priority=-900、mark_as_temp）
            # 情绪 key 必须和写入端一致：
            #   私聊写入用 uid（private_handler.record_user_message）
            #   群聊写入用 group_id / umo
            session_key = ""
            try:
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                if event.is_private_chat():
                    session_key = str(event.get_sender_id() or umo)
                else:
                    session_key = str(event.get_group_id() or umo)
            except Exception:  # noqa: BLE001 - 事件缺字段时仅跳过情绪，不影响日程注入
                pass
            motive_hint = ""
            if bool(self.cfg.get("心事_被动注入", True)):
                try:
                    # 用带缓存的只读方法，避免每次被动回复都读盘
                    motive_hint = self.motive_mgr.peek_latest_unused()
                except Exception:  # noqa: BLE001
                    pass
            # 群黑话：与心事共用同一临时块，受总字数上限约束
            _sync_cfg = getattr(self, "_sync_runtime_state", None)
            if callable(_sync_cfg):
                _sync_cfg()
            try:
                _on_module = getattr(self, "_module_on", None)
                _black_on = callable(_on_module) and _on_module("黑话")
                _gid = str(getattr(event, "get_group_id", lambda: "")() or "")
                if _black_on and _gid:
                    slang = getattr(self, "_slang", None)
                    if slang is None:
                        from .modules.slang import SlangMiner
                        slang = SlangMiner(int(self.cfg.get("黑话_最小命中", 3) or 3))
                        self._slang = slang
                    hint = slang.hint(str(event.get_group_id()), self.cfg)
                    if hint:
                        motive_hint = (motive_hint + "\n" + hint).strip()
            except Exception:  # noqa: BLE001
                pass
            # 跨会话人物档案：群聊只带公开字段，私聊才带温度/欠着的事
            person_block = ""
            try:
                _pm = getattr(self, "person_memory", None)
                if _pm is not None:
                    _scope = "private" if event.is_private_chat() else "group"
                    person_block = _pm.block(
                        str(event.get_sender_id() or ""),
                        scope=_scope,
                        name=str(event.get_sender_name() or ""),
                        current_text=str(getattr(event, "message_str", "") or ""),
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("proactive_life | 人物档案注入失败: %s", e)

            block = build_life_state_block(
                self.cfg, mood=getattr(self, "mood", None),
                motive=motive_hint, session_key=session_key,
                person=person_block,
            )
            if not block:
                return
            if inject_life_state(req, block):
                logger.debug("proactive_life | 已注入当前生活状态（%d 字）", len(block))
        except Exception as e:  # noqa: BLE001 - 注入失败不能影响正常回复
            logger.debug(f"proactive_life | 生活状态注入异常，本次跳过: {e}")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=9000)
    async def on_private_message(self, event: AstrMessageEvent):
        """记录私聊来信时间，作为「安静多久才找」的计时依据。"""
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        if not self._module_on("私聊"):
            return
        uid = str(event.get_sender_id())
        self.private_h.record_user_message(uid)
        # 人物档案：私聊可写私密字段（温度、欠着的事）
        try:
            pm = getattr(self, "person_memory", None)
            if pm is not None:
                pm.resolve_pending(uid)
                pm.touch(
                    uid, str(getattr(event, "message_str", "") or ""),
                    name=str(event.get_sender_name() or ""), scope="private",
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 私聊人物档案更新失败: %s", e)

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        """发送消息后：释放防抖租约、记录 Bot 的发言，开启私聊智能追问与群聊接话窗口。"""
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
    async def on_group_reread(self, event: AstrMessageEvent):
        """群聊跟读：尾 N 条相同短消息时自然复读；闭嘴期/@ 消息绝不触发。"""
        if self._stop_event.is_set() or not self._module_on("跟读"):
            return
        _sync_cfg = getattr(self, "_sync_runtime_state", None)
        if callable(_sync_cfg):
            _sync_cfg()
        try:
            if getattr(event, "is_at_or_wake_command", False):
                return
            gid = str(getattr(event, "get_group_id", lambda: "")() or "")
            if not gid or self.group_h._is_muted(gid):
                return
            from .modules.reread import RereadState, make_fingerprint

            state = getattr(self, "_reread_state", None)
            if state is None:
                state = RereadState(
                    int(self.cfg.get("跟读_阈值", 2) or 2),
                    float(self.cfg.get("跟读_最小间隔秒", 8) or 8),
                )
                self._reread_state = state
            # 屏蔽词
            text = str(getattr(event, "message_str", "") or "")
            blocked = [str(x) for x in (self.cfg.get("跟读_屏蔽词") or []) if str(x).strip()]
            if any(w in text for w in blocked):
                return
            fp = make_fingerprint(list(getattr(event, "get_messages", lambda: [])() or []))
            if not fp:
                return
            async with state.lock:
                candidate = state.observe(gid, fp)
                if not candidate:
                    return
                import random

                if random.random() > float(self.cfg.get("跟读_概率", 0.35) or 0.35):
                    return
                state.mark_handled(gid, candidate)
            # 复用原消息文本复读，不走拟人管线（避免二次润饰）
            from astrbot.api.message_components import Plain

            yield event.chain_result([Plain(text)])
            # 复读已成事实：截断事件，避免同一条消息又走群聊插话再回一次
            event.stop_event()
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 跟读处理异常，按未触发跳过: %s", e)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群聊插话：判断是否该接话（直接接话 / 话题跟进 / 概率插话），必要时生成回复。"""
        if self._stop_event.is_set():  # 重载残留的旧实例 handler，直接空转
            return
        if (not self._module_on("群聊") and not self._module_on("黑话")
                and not self._module_on("戳一戳")):
            return
        # 人物档案（群聊只写公开字段：称呼、兴趣、最近话题）
        try:
            _pm = getattr(self, "person_memory", None)
            if _pm is not None:
                _pm.touch(
                    str(event.get_sender_id() or ""),
                    str(getattr(event, "message_str", "") or ""),
                    name=str(event.get_sender_name() or ""),
                    group=str(event.get_group_id() or ""),
                    scope="group",
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 群聊人物档案更新失败: %s", e)

        # 群黑话旁路采集（共享本次事件，不重复注册回调）
        if self._module_on("黑话"):
            _sync_cfg = getattr(self, "_sync_runtime_state", None)
            if callable(_sync_cfg):
                _sync_cfg()
            try:
                slang = getattr(self, "_slang", None)
                if slang is None:
                    from .modules.slang import SlangMiner
                    slang = SlangMiner(int(self.cfg.get("黑话_最小命中", 3) or 3))
                    self._slang = slang
                slang.observe(
                    str(event.get_group_id() or ""), str(getattr(event, "message_str", "") or "")
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("proactive_life | 群黑话采集异常: %s", e)
        # 戳一戳/纯艾特的上下文采集：记录本群最近消息（文字+图片）
        ctx = getattr(self, "interact_ctx", None)
        gid = str(event.get_group_id() or "")
        pending_hit = None
        if ctx and gid and self._module_on("戳一戳"):
            try:
                from .debounce import MessageExtractor

                item = MessageExtractor(filter_stickers=False).extract(
                    event, enable_emotion_split=False
                )
                ctx.observe(gid, str(event.get_sender_id() or ""),
                            item.text or str(getattr(event, "message_str", "") or ""),
                            list(item.image_urls or []))
                # 情景 B：前面已戳/艾特，这条才来 → 合并发出
                if bool(self.cfg.get("戳一戳_后续合并", True)):
                    pending_hit = ctx.pop_pending(gid)
                if pending_hit:
                    items = ctx.recent(gid)
                    from .modules.interact_context import build_prompt

                    prompt, images = build_prompt(pending_hit.kind, pending_hit, items)
                    req = event.request_llm(prompt=prompt)
                    if images:
                        existing = list(getattr(req, "image_urls", None) or [])
                        req.image_urls = list(dict.fromkeys(existing + images))
                    yield req
                    return  # 情景 B 已由这条消息合并回复，不再走常规群聊插话
            except Exception as e:  # noqa: BLE001
                logger.debug("proactive_life | 互动上下文采集异常: %s", e)
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
    def _fetch_json(url: str) -> dict:
        """同步 HTTP（由 asyncio.to_thread 放到线程里执行，避免阻塞事件循环）。

        注意：这里必须是同步函数。若写成 async def，to_thread 只会返回一个
        未执行的 coroutine，调用方 .get() 会直接 AttributeError。
        """
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @filter.llm_tool(name="pl_bilibili_hot")
    async def browse_bilibili_hot(self, event: AstrMessageEvent, count: int = 5) -> str:
        """刷B站热门视频榜单。手头闲着、想看看大家在看什么、或者想找点好玩的话题时调用。

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

            # 0. 配置可能在面板被改，同步角色身分（纯内存，开销可忽略）
            await self._refresh_identity()

            # 1. 优先执行私聊追问检查（超低开销，秒级响应未回答提问）
            # 加超时：模型/网络卡住时不能拖死整个协调循环
            try:
                if self._module_on("私聊"):
                    await asyncio.wait_for(
                        self.private_h.check_and_nudge(now), timeout=60
                    )
            except asyncio.TimeoutError:
                logger.warning("proactive_life | 私聊追问巡检超时（60s），跳过本轮")
            except Exception as e:
                logger.warning(f"proactive_life | 私聊追问巡检异常: {e}")

            # 1.5 到点就生成当天日程（每天一次，失败不重试，避免刷模型）
            try:
                await asyncio.wait_for(
                    self._maybe_generate_schedule(now), timeout=180
                )
            except asyncio.TimeoutError:
                logger.warning("proactive_life | 日程自动生成超时（180s），跳过本轮")
            except Exception as e:
                logger.warning(f"proactive_life | 日程自动生成异常: {e}")

            # 2. 判断是否到达宏观节律轮询周期（冷场主动/群聊破冰/后台摸鱼）
            now_ts = time.time()
            macro_interval = max(3, int(self.cfg.get("全局调度间隔", 10))) * 60
            if now_ts - self._last_macro_tick >= macro_interval:
                self._last_macro_tick = now_ts
                try:
                    # 宏观轮询含最多三链模型调用，给足上限但仍不能无限等
                    await asyncio.wait_for(self._macro_tick(now), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning("proactive_life | 宏观节律轮询超时（300s），跳过本轮")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"proactive_life | 中枢宏观节律轮询异常: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    def _sync_identity(self, persona_text: str | None = None) -> None:
        """用当前配置重建 Identity，并注入所有 handler。

        persona_text 为 None 时保留上一次读到的文本（面板改配置后不必重读）。
        """
        try:
            fresh = identity_from_config(self.cfg)
            fresh.persona_text = (
                self.identity.persona_text if persona_text is None else persona_text
            )
            if persona_text is None:
                fresh.plugin_prompt = str(
                    self.cfg.get("角色_插件专用提示词", "") or ""
                ).strip()
            self.identity = fresh
            for _h in (self.private_h, self.group_h, self.worker_h, self.schedule_gen):
                try:
                    _h.identity = fresh
                except Exception:
                    pass
        except Exception:
            pass

    async def _load_persona(self) -> None:
        """按配置所选的人设读取 prompt（切换人设/重载时调用）。"""
        try:
            from .identity import read_persona_by_id

            if bool(self.cfg.get("角色_自动读取AstrBot人设", True)):
                pid = str(self.cfg.get("角色_AstrBot人设", "") or "")
                text = await read_persona_by_id(self.context, pid)
                logger.info(
                    "proactive_life | 人设读取：选择=%r，读到 %d 字%s",
                    pid, len(text), "（所选人设为空，已回退默认）" if pid and text else "",
                )
            else:
                text = ""
                logger.info("proactive_life | 自动读取人设已关闭")
            self._sync_identity(persona_text=text)
            if text:
                logger.info("proactive_life | 已读取人设（%d 字）", len(text))
        except Exception as e:
            logger.debug("proactive_life | 读取 AstrBot 人设失败，改用手动配置: %s", e)

    async def _refresh_identity(self) -> None:
        """配置可能在面板被改，每轮把身份字段同步一次（人设文本保留）。"""
        self._sync_identity()

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

    async def _auto_analyze_slang(self, now: datetime.datetime) -> None:
        """自动解析新挖到的黑话：复用调度循环，30 分钟节流，只解析没解析过的词。"""
        if not self._module_on("黑话") or not bool(self.cfg.get("黑话_自动解析", True)):
            return
        slang = getattr(self, "_slang", None)
        if slang is None:
            return
        # 节流：30 分钟一次
        last = float(getattr(self, "_slang_auto_at", 0) or 0)
        if time.time() - last < 1800:
            return
        try:
            records = slang.records(limit=30)
            analyzed = {a.get("word") for a in slang.analysis()}
            words = list(dict.fromkeys(
                r["word"] for r in records
                if r.get("word") and r["word"] not in analyzed
            ))
            if not words:
                self._slang_auto_at = time.time()  # 没新词也记一次，避免每轮都扫
                return
            from .modules.slang_analyze import analyze_slang

            result = await asyncio.wait_for(
                analyze_slang(
                    self.context, self.cfg, words[:12], event=None,
                    umo=str((self.cfg.get("对谁主动") or [""])[0] or ""),
                ),
                timeout=90,
            )
            self._slang_auto_at = time.time()
            if result.get("ok"):
                n = slang.set_analysis(result["items"])
                if n:
                    logger.info("proactive_life | 自动解析黑话 %d 个", n)
        except Exception as e:  # noqa: BLE001
            self._slang_auto_at = time.time()
            logger.debug("proactive_life | 自动解析黑话失败: %s", e)

    async def _macro_tick(self, now: datetime.datetime) -> dict:
        """跑一轮调度。返回各模块结果，供面板「手动跑一轮」展示原因。"""
        result = {"private": False, "group": False, "worker": False, "skipped": []}
        try:
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

            # 3. 若私聊群聊都没开口，且处于空闲，触发后台自主摸鱼干活
            if self._module_on("摸鱼"):
                result["worker"] = await self.worker_h.check_and_work(now)
            else:
                result["skipped"].append("摸鱼")
            return result
        finally:
            # 黑话自动解析：每轮都检查（内部 30 分钟节流），不另起后台线程
            await self._auto_analyze_slang(now)

    def _runtime_config_sig(self) -> tuple:
        """把各状态机的可调参数打包成签名，用于判断是否需要同步。"""
        def num(key, default, cast=float):
            try:
                return cast(self.cfg.get(key, default) or default)
            except (TypeError, ValueError):
                return cast(default)

        return (
            num("戳一戳_上下文条数", 6, int),
            num("戳一戳_上下文秒数", 20),
            num("黑话_最小命中", 3, int),
            num("跟读_阈值", 2, int),
            num("跟读_最小间隔秒", 8),
            num("戳一戳_个人冷却秒", 30),
            num("戳一戳_群冷却秒", 60),
        )

    def _sync_runtime_state(self) -> None:
        """把面板改过的参数同步到已创建的状态机（无需重载插件）。"""
        try:
            sig = self._runtime_config_sig()
            if sig == getattr(self, "_runtime_sig_cache", None):
                return
            self._runtime_sig_cache = sig
            (win, ttl, min_hits, re_th, re_iv, cd_p, cd_g) = sig
            ctx = getattr(self, "interact_ctx", None)
            if ctx is not None:
                ctx.update(window=win, ttl=ttl)
            slang = getattr(self, "_slang", None)
            if slang is not None:
                slang.update_config(min_hits=min_hits)
            reread = getattr(self, "_reread_state", None)
            if reread is not None:
                reread.update_config(threshold=re_th, min_interval=re_iv)
            cd = getattr(self, "_poke_cd", None)
            if cd is not None:
                cd.update_config(personal_sec=cd_p, group_sec=cd_g)
        except Exception as e:  # noqa: BLE001 - 同步失败不该影响主流程
            logger.debug("proactive_life | 运行态配置同步失败: %s", e)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_poke_event(self, event: AstrMessageEvent):
        """戳一戳 / 纯艾特：共用一套冷却与情绪逻辑，附带该用户的上下文与图片。

        情景 A：用户先说话/发图（忘 @），随后补 @ 或戳 → 带前面的内容一起回。
        情景 B：先 @ / 戳，后续发消息 → 记 pending，由 on_group_message 合并发出。
        """
        if self._stop_event.is_set() or not self._module_on("戳一戳"):
            return
        _sync_cfg = getattr(self, "_sync_runtime_state", None)
        if callable(_sync_cfg):
            _sync_cfg()
        try:
            from .modules.poke import PokeCooldown, is_poke_event
            from .modules.interact_context import build_prompt, is_pure_at

            hit, bot_id, sender, group = is_poke_event(event)
            kind = ""
            if hit and sender:
                kind = "poke"
            else:
                # 纯艾特：消息组件全是 @Bot/@全体，没有任何文字/图片
                if bool(self.cfg.get("戳一戳_纯艾特回应", True)):
                    try:
                        bot = str(event.get_self_id() or "")
                        msgs = list(event.get_messages() or [])
                        if is_pure_at(msgs, bot):
                            hit = True
                            kind = "at"
                            sender = str(event.get_sender_id() or "")
                            group = str(event.get_group_id() or "")
                    except Exception:  # noqa: BLE001
                        pass
            if not hit or not sender:
                return
            # 黑名单用户直接忽略
            if self._module_on("黑名单") and self.blacklist.is_blacklisted_event(event):
                return
            cooldown = getattr(self, "_poke_cd", None)
            if cooldown is None:
                cooldown = PokeCooldown(
                    float(self.cfg.get("戳一戳_个人冷却秒", 30) or 30),
                    float(self.cfg.get("戳一戳_群冷却秒", 60) or 60),
                )
                self._poke_cd = cooldown
            try:
                admin = bool(event.is_admin())
            except Exception:  # noqa: BLE001
                admin = False
            if cooldown.is_cooling(sender, group, is_admin=admin):
                return
            cooldown.mark(sender, group)
            if self._module_on("上下文"):
                try:
                    self.mood.apply(sender or group or "私聊", "replied", "被戳/艾特了一下")
                except Exception:  # noqa: BLE001
                    pass
            import random

            prob_key = "戳一戳_纯艾特概率" if kind == "at" else "戳一戳_回应概率"
            if random.random() > float(self.cfg.get(prob_key, 0.7) or 0.7):
                return

            # 两条分支互斥，避免「既立刻回、又被后续消息再触发一次」的双回复：
            #   情景 A：前面有内容 → 本次直接带上下文回复，不埋 pending
            #   情景 B：前面没内容 → 埋 pending，等后面那条消息一起合并回复，本次不回
            ctx = getattr(self, "interact_ctx", None)
            items = ctx.recent(group, exclude_sender="") if (ctx and group) else []

            event.set_extra("_poke_trigger", True)
            event.set_extra("_poke_sender_id", sender)

            if not items:
                # 情景 B：没有任何上下文，等用户补正文/图片再一起回
                if ctx and group:
                    ctx.set_pending(group, kind, sender)
                return

            # 情景 A：带着前面的上下文现在就回
            prompt, images = build_prompt(kind, None, items)
            req = event.request_llm(prompt=prompt)
            if images:
                try:
                    existing = list(getattr(req, "image_urls", None) or [])
                    req.image_urls = list(dict.fromkeys(existing + images))
                except Exception:  # noqa: BLE001
                    pass
            yield req
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 戳一戳/纯艾特处理异常，按未触发跳过: %s", e)

    @filter.on_llm_response(priority=1000)
    async def on_outbound_think_filter(self, event: AstrMessageEvent, resp):
        """出站第 1 道：过滤模型误吐的思考过程（在分片发送前）。"""
        if self._stop_event.is_set() or not self._module_on("思考过滤"):
            return
        try:
            from .modules.outbound import clean_llm_response

            if clean_llm_response(resp, self.cfg):
                logger.debug("proactive_life | 已过滤回复中的误吐思考")
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 思考过滤异常，按原样发送: %s", e)

    @filter.on_llm_response(priority=900)
    async def on_outbound_polish_response(self, event: AstrMessageEvent, resp):
        """出站第 2 道（提前版）：在进 history 之前就洗掉八股。

        为什么前移：`on_decorating_result` 只改发出去的气泡，AstrBot 写进
        conversation.history 的仍是带八股的原文，模型下一轮会读到自己那段八股
        并自我强化。这里直接在 completion_text 上清洗，历史里就是干净文本。

        安全边界：
        - 只有纯文本、且没有未完成的工具调用时才动
        - 洗完为空则回滚原文（宁可八股也不让回复消失）
        - 代码块/表格/引号台词由 polish 内部保护区原样保留
        """
        if self._stop_event.is_set() or not self._module_on("八股清洗"):
            return
        try:
            # 有未完成的工具调用：这一条不是最终回复，碰了会破坏工具链
            if getattr(resp, "tools_call_name", None):
                return
            text = str(getattr(resp, "completion_text", "") or "")
            if not text.strip():
                return

            from .modules.outbound import polish_text

            cleaned = polish_text(text, self.cfg, event=event, stage="polish_resp")
            if not cleaned:
                # 整条都是八股：保留原文，别让这条回复变空
                logger.debug("proactive_life | 整条均为八股，保留原文")
                return
            if cleaned != text:
                try:
                    resp.completion_text = cleaned
                except Exception:  # noqa: BLE001
                    return
                event.set_extra("_pl_polished", True)
                logger.debug("proactive_life | 已在响应阶段清洗八股（%d→%d 字）", len(text), len(cleaned))
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 响应阶段八股清洗异常，按原样继续: %s", e)

    @filter.on_decorating_result(priority=-500)
    async def on_outbound_polish(self, event: AstrMessageEvent):
        """出站兜底：响应阶段没洗到的情况（非 LLM 产物等）在这里洗。

        正常路径已在 on_llm_response 提前洗过（历史不落八股），
        这里靠 _pl_polished 标记去重。确定性早于节奏修剪（-600）。

        注意：AstrBot 按 -priority 排序，数值越大越先执行。
        所以这里必须 > -600，否则会变成「先按节奏拆条、再逐条洗八股」。
        """
        if self._stop_event.is_set() or not self._module_on("八股清洗"):
            return
        # 响应阶段（on_llm_response）已经洗过就不重复洗，避免同一段文本洗两遍
        try:
            if event.get_extra("_pl_polished"):
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            from astrbot.api.message_components import Plain

            from .modules.outbound import polish_text

            result = event.get_result()
            if result is None or not getattr(result, "chain", None):
                return
            try:
                if not result.is_llm_result():
                    return
            except AttributeError:
                pass
            drop: list = []
            originals: dict = {}
            for comp in list(result.chain):
                if isinstance(comp, Plain) and comp.text:
                    originals[id(comp)] = comp.text
                    new_text = polish_text(comp.text, self.cfg, event=event)
                    if new_text == "":
                        drop.append(comp)      # 整段都是八股：整段丢弃
                    elif new_text != comp.text:
                        comp.text = new_text
            # 兜底：整条消息都是八股时不能发出空消息（部分适配器会报错），
            # 宁可保留原文也不要让这条回复变成空的。
            remaining = [c for c in result.chain if c not in drop]
            if not remaining and drop:
                drop[0].text = originals.get(id(drop[0]), drop[0].text)
                logger.debug("proactive_life | 整条均为八股，保留原文以免发出空消息")
                drop = drop[1:]
            for comp in drop:
                try:
                    result.chain.remove(comp)
                except (ValueError, AttributeError):
                    comp.text = ""
        except Exception as e:  # noqa: BLE001
            logger.debug("proactive_life | 八股清洗异常，按原样发送: %s", e)

    @filter.on_decorating_result(priority=-600)
    async def reshape(self, event: AstrMessageEvent) -> None:
        """回复前处理（实际逻辑在 RhythmMixin，保留此壳以确保 filter 注册位置不变）。"""
        return await RhythmMixin.reshape(self, event)
