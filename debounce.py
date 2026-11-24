"""消息防抖（从 astrbot_plugin_smart_debounce 移植并精简）。

作用：同一个人连续发多条消息（或多人同时艾特）时，不逐条回复，而是等一小段
静默后合并成「一批」再交给模型，避免抢答、碎片回复。

机制：
1. 最高优先级拦截群聊（艾特/唤醒）与私聊消息；
2. 每条消息进对应的「桶」，重置防抖计时器；桶封口后合并成一段 prompt；
3. 封口前到达的消息用 `event.stop_event()` 吸收（不回复）；
4. 封口的消息通过 `on_llm_request` 覆盖 `req.prompt` / `req.image_urls`；
5. 令牌租约 + 看门狗，避免异常路径把会话锁死。

与原插件的差异（刻意）：
- 配置项改为中樞的 `防抖_*` 命名，并受 `模块_防抖` 总开关控制；
- 参数变化时自动重建管线，不必重载插件；
- 增加 `stats()` 供面板观测。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
import weakref
from dataclasses import dataclass, field
from typing import Any, Optional

from astrbot.api import logger

MAX_IMAGES_PER_BATCH = 5

# 表情包/贴纸的 summary 关键字
STICKER_SUMMARY_TAGS = ("动画表情", "商城表情", "表情", "贴纸", "sticker")

# 情绪倾诉识别：这类消息单独成批，避免和正经提问混在一起污染语气
EMOTION_PATTERN = re.compile(
    r"(累死|好累|心累|烦死|烦死了|难受|崩溃|emo|难过|想哭|哭了|委屈|压力大|焦虑|失眠|受不了|撑不住|不想活|没意思|好丧|破防)"
)

# 明确的「看图」意图：此时不该过滤表情包
VISUAL_INTENT_PATTERN = re.compile(
    r"(这张?图|这个?图|图里|图中|图上|表情包?|贴纸|出处|哪部|哪个番|什么番|什么动漫|谁啊|P图|改图|看图|识别|提取|OCR)",
    re.IGNORECASE,
)


@dataclass
class MultimodalMessageItem:
    sender_name: str
    sender_id: str
    text: str
    image_urls: list[str] = field(default_factory=list)
    reply_sender: Optional[str] = None
    reply_text: Optional[str] = None
    reply_image_urls: list[str] = field(default_factory=list)
    emotional: bool = False


@dataclass
class MergedBatchPayload:
    prompt: str
    image_urls: list[str]
    lock_token: str
    count: int = 0


@dataclass
class BatchBucket:
    items: list[MultimodalMessageItem] = field(default_factory=list)
    flush_event: asyncio.Event = field(default_factory=asyncio.Event)
    debounce_task: Optional[asyncio.Task] = None
    max_wait_task: Optional[asyncio.Task] = None
    created_at: float = field(default_factory=time.time)


class TokenizedLeaseLock:
    """带看门狗令牌的互斥锁：异常路径也能自动释放，不会永久卡死。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current_token: Optional[str] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    async def acquire(self, initial_ttl: float = 3.0) -> str:
        await self._lock.acquire()
        token = uuid.uuid4().hex
        self._current_token = token
        self._arm_watchdog(token, initial_ttl, "routing")
        return token

    def renew(self, token: Optional[str], ttl: float, stage: str = "inference") -> None:
        if not token or token != self._current_token:
            return
        self._arm_watchdog(token, ttl, stage)

    def release(self, token: Optional[str], reason: str = "normal") -> bool:
        if not token or token != self._current_token:
            return False
        self._current_token = None
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None
        if self._lock.locked():
            self._lock.release()
        if reason != "normal":
            logger.warning("proactive_life | 防抖锁兜底释放: %s", reason)
        return True

    def _arm_watchdog(self, token: str, ttl: float, stage: str) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = asyncio.create_task(self._watchdog_timer(token, ttl, stage))

    async def _watchdog_timer(self, token: str, ttl: float, stage: str) -> None:
        try:
            await asyncio.sleep(ttl)
            self.release(token, f"watchdog_timeout({stage}, {ttl}s)")
        except asyncio.CancelledError:
            pass


class DebouncePipeline:
    """单个会话的防抖管线。"""

    def __init__(
        self,
        debounce_sec: float = 1.2,
        max_wait_sec: float = 2.5,
        max_images: int = MAX_IMAGES_PER_BATCH,
        lease_ttl: float = 15.0,
        enable_emotion_split: bool = True,
    ) -> None:
        self.debounce_sec = max(0.1, float(debounce_sec))
        self.max_wait_sec = max(self.debounce_sec, float(max_wait_sec))
        self.max_images = max(1, int(max_images))
        self.lease_ttl = max(3.0, float(lease_ttl))
        self.enable_emotion_split = bool(enable_emotion_split)
        self.current_bucket: Optional[BatchBucket] = None
        self.lease_lock = TokenizedLeaseLock()
        self.last_flush_at: float = 0.0
        self.last_batch_count: int = 0
        self.total_batches: int = 0
        self.absorbed: int = 0

    async def ingest(self, item: MultimodalMessageItem) -> Optional[MergedBatchPayload]:
        """喂入一条消息。返回 None 表示已被当前批次吸收（不要回复）。"""
        cur = self.current_bucket
        same_kind = (
            cur is not None
            and cur.items
            and (not self.enable_emotion_split or cur.items[0].emotional == item.emotional)
        )
        if same_kind:
            cur.items.append(item)
            self._reset_debounce(cur)
            self.absorbed += 1
            return None

        bucket = BatchBucket()
        bucket.items.append(item)
        self.current_bucket = bucket
        bucket.max_wait_task = asyncio.create_task(self._max_wait_timer(bucket))
        self._reset_debounce(bucket)

        await bucket.flush_event.wait()
        token = await self.lease_lock.acquire(initial_ttl=self.lease_ttl)
        prompt, images = self._build_payload(bucket.items)
        self.last_flush_at = time.time()
        self.last_batch_count = len(bucket.items)
        self.total_batches += 1
        return MergedBatchPayload(
            prompt=prompt, image_urls=images, lock_token=token, count=len(bucket.items)
        )

    # ------------------------------------------------ 计时器
    def _reset_debounce(self, bucket: BatchBucket) -> None:
        if bucket.debounce_task and not bucket.debounce_task.done():
            bucket.debounce_task.cancel()
        bucket.debounce_task = asyncio.create_task(self._debounce_timer(bucket))

    async def _debounce_timer(self, bucket: BatchBucket) -> None:
        try:
            await asyncio.sleep(self.debounce_sec)
            self._seal_bucket(bucket)
        except asyncio.CancelledError:
            pass

    async def _max_wait_timer(self, bucket: BatchBucket) -> None:
        try:
            await asyncio.sleep(self.max_wait_sec)
            self._seal_bucket(bucket)
        except asyncio.CancelledError:
            pass

    def _seal_bucket(self, bucket: BatchBucket) -> None:
        if self.current_bucket is bucket:
            self.current_bucket = None
        for t in (bucket.debounce_task, bucket.max_wait_task):
            if t and not t.done():
                t.cancel()
        bucket.flush_event.set()

    # ------------------------------------------------ 合并成 prompt
    def _build_payload(self, items: list[MultimodalMessageItem]) -> tuple[str, list[str]]:
        ordered_images: list[str] = []
        dropped = 0

        def register_images(urls: list[str]) -> str:
            nonlocal dropped
            tags = []
            for u in urls or []:
                if u in ordered_images:
                    tags.append(f"[附图#{ordered_images.index(u) + 1}]")
                    continue
                if len(ordered_images) >= self.max_images:
                    dropped += 1
                    continue
                ordered_images.append(u)
                tags.append(f"[附图#{len(ordered_images)}]")
            return " ".join(tags)

        entries: list[str] = []
        senders = {it.sender_id for it in items}

        for it in items:
            reply_prefix = ""
            if it.reply_text or it.reply_image_urls:
                r_tags = register_images(it.reply_image_urls)
                r_content = " ".join([x for x in (it.reply_text, r_tags) if x]).strip()
                reply_prefix = f'(引用了 [{it.reply_sender or "群友"}] 的消息: "{r_content}") '

            msg_tags = register_images(it.image_urls)
            body = it.text or ("请看图" if (msg_tags or it.reply_image_urls) else "")
            content = " ".join([x for x in (body, msg_tags) if x]).strip()

            if len(senders) == 1:
                entries.append(f"{reply_prefix}{content}".strip())
            else:
                entries.append(
                    f"- {it.sender_name}(QQ:{it.sender_id}) {reply_prefix}: {content}".strip()
                )

        if dropped:
            entries.append(f"[本批还有 {dropped} 张图超出单次上限，已省略]")

        emotional_batch = bool(items) and items[0].emotional
        if len(senders) == 1:
            head = f"[{items[0].sender_name}(QQ:{items[0].sender_id})]: " if len(items) > 1 else ""
            if emotional_batch:
                head = "[情绪倾诉，先接住情绪，别急着给方案] " + head
            return head + "\n".join(entries), ordered_images

        header = (
            "[这条是情绪倾诉，先接住情绪，别急着给方案或讲道理，语气放软]："
            if emotional_batch
            else "[群聊多位成员同时向你提问，请结合对应编号的附图与引用上下文，在一整段话中自然回应大家]："
        )
        return header + "\n" + "\n".join(entries), ordered_images

    def stats(self) -> dict:
        bucket = self.current_bucket
        return {
            "pending": len(bucket.items) if bucket else 0,
            "locked": self.lease_lock._lock.locked(),
            "absorbed": self.absorbed,
            "batches": self.total_batches,
            "last_flush": self.last_flush_at,
            "last_count": self.last_batch_count,
        }


class DebounceManager:
    """按会话维护管线；配置变化时自动重建。"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._pipelines: dict[str, DebouncePipeline] = {}
        self._params: dict[str, tuple] = {}

    def _get(self, key: str, default):
        try:
            return self.cfg.get(key, default)
        except Exception:
            return default

    def _params_for(self, is_friend: bool) -> tuple:
        if is_friend:
            return (
                float(self._get("防抖_私聊等待秒", 0.8) or 0.8),
                float(self._get("防抖_私聊最长等待秒", 1.5) or 1.5),
                int(self._get("防抖_单次图片上限", MAX_IMAGES_PER_BATCH) or MAX_IMAGES_PER_BATCH),
                float(self._get("防抖_锁超时秒", 15.0) or 15.0),
                False,  # 私聊不做情绪拆分：一个人连发几句本来就是一件事
            )
        return (
            float(self._get("防抖_群聊等待秒", 1.2) or 1.2),
            float(self._get("防抖_群聊最长等待秒", 2.5) or 2.5),
            int(self._get("防抖_单次图片上限", MAX_IMAGES_PER_BATCH) or MAX_IMAGES_PER_BATCH),
            float(self._get("防抖_锁超时秒", 15.0) or 15.0),
            bool(self._get("防抖_情绪拆分", True)),
        )

    def pipeline(self, umo: str) -> DebouncePipeline:
        is_friend = "FriendMessage" in umo
        params = self._params_for(is_friend)
        old = self._pipelines.get(umo)
        if old is not None and self._params.get(umo) == params:
            return old
        pipe = DebouncePipeline(*params)
        self._pipelines[umo] = pipe
        self._params[umo] = params
        return pipe

    def get_if_exists(self, umo: str) -> Optional[DebouncePipeline]:
        return self._pipelines.get(umo)

    def stats(self) -> dict:
        active = [p for p in self._pipelines.values() if p.current_bucket is not None]
        return {
            "sessions": len(self._pipelines),
            "active": len(active),
            "pending": sum(len(p.current_bucket.items) for p in active if p.current_bucket),
            "locked": sum(1 for p in self._pipelines.values() if p.lease_lock._lock.locked()),
            "batches": sum(p.total_batches for p in self._pipelines.values()),
            "absorbed": sum(p.absorbed for p in self._pipelines.values()),
        }

    def clear(self) -> None:
        self._pipelines.clear()
        self._params.clear()


class MessageExtractor:
    """把一条 AstrBot 事件抽成 MultimodalMessageItem（文本 + 图片 + 引用）。"""

    def __init__(self, filter_stickers: bool = True) -> None:
        self.filter_stickers = filter_stickers

    @staticmethod
    def _raw_segments(event: Any) -> list[dict]:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if raw is None:
            return []
        segs = raw.get("message") if isinstance(raw, dict) else getattr(raw, "message", None)
        return segs if isinstance(segs, list) else []

    def _raw_image_meta(self, event: Any) -> dict[str, dict]:
        meta_map: dict[str, dict] = {}
        for seg in self._raw_segments(event):
            if not isinstance(seg, dict) or seg.get("type") not in ("image", "mface"):
                continue
            data = seg.get("data") or {}
            if not isinstance(data, dict):
                continue
            info = {
                "seg_type": seg.get("type"),
                "sub_type": data.get("sub_type", data.get("subType", 0)),
                "summary": str(data.get("summary", "")),
            }
            for key in (data.get("url"), data.get("file")):
                if key:
                    meta_map[str(key)] = info
        return meta_map

    def _is_meaningless_sticker(self, comp: Any, raw_meta: dict[str, dict], filter_gif: bool = True) -> bool:
        img_url = getattr(comp, "url", "") or ""
        img_file = getattr(comp, "file", "") or ""
        info = raw_meta.get(img_url) or raw_meta.get(img_file) or {}

        if info.get("seg_type") == "mface":
            return True
        try:
            if int(info.get("sub_type", 0)) != 0:
                return True
        except (ValueError, TypeError):
            pass
        summary = info.get("summary", "")
        if any(tag in summary for tag in STICKER_SUMMARY_TAGS):
            return True
        if filter_gif:
            lower = (img_file or img_url).lower()
            if lower.endswith(".gif") or "format=gif" in lower:
                return True
        return False

    def extract(self, event: Any, enable_emotion_split: bool = True) -> MultimodalMessageItem:
        from astrbot.api.message_components import Image, Plain, Reply

        text = (getattr(event, "message_str", "") or "").strip()
        keep_stickers = (not self.filter_stickers) or bool(VISUAL_INTENT_PATTERN.search(text))
        raw_meta = self._raw_image_meta(event)

        direct_images: list[str] = []
        reply_images: list[str] = []
        reply_text_parts: list[str] = []
        reply_sender: Optional[str] = None
        filtered = 0

        try:
            comps = list(event.get_messages() or [])
        except Exception:
            comps = []

        for comp in comps:
            if isinstance(comp, Image):
                src = getattr(comp, "url", None) or getattr(comp, "file", None)
                if not src:
                    continue
                if not keep_stickers and self._is_meaningless_sticker(comp, raw_meta):
                    filtered += 1
                    continue
                direct_images.append(src)
            elif isinstance(comp, Reply):
                reply_sender = (
                    getattr(comp, "sender_nickname", None)
                    or str(getattr(comp, "sender_id", "") or "")
                    or "群友"
                )
                for sub in getattr(comp, "chain", None) or []:
                    if isinstance(sub, Plain):
                        t = (getattr(sub, "text", "") or "").strip()
                        if t:
                            reply_text_parts.append(t)
                    elif isinstance(sub, Image):
                        s = getattr(sub, "url", None) or getattr(sub, "file", None)
                        if not s:
                            continue
                        if not keep_stickers and self._is_meaningless_sticker(sub, raw_meta):
                            filtered += 1
                            continue
                        reply_images.append(s)
                if not reply_text_parts:
                    fallback = getattr(comp, "message_str", None) or getattr(comp, "text", None)
                    if fallback:
                        reply_text_parts.append(str(fallback).strip())

        clean_text = text.replace("[图片]", "").replace("[动画表情]", "").strip()
        if not clean_text and not direct_images and not reply_text_parts and not reply_images:
            if filtered:
                clean_text = "[发了一个表情包]"

        return MultimodalMessageItem(
            sender_name=(getattr(event, "get_sender_name", None) and event.get_sender_name()) or "群友",
            sender_id=str(getattr(event, "get_sender_id", lambda: "")() or ""),
            text=clean_text,
            image_urls=direct_images,
            reply_sender=reply_sender,
            reply_text=" ".join(reply_text_parts) if reply_text_parts else None,
            reply_image_urls=reply_images,
            emotional=bool(
                enable_emotion_split and clean_text and EMOTION_PATTERN.search(clean_text)
            ),
        )


def attach_finalizer(event: Any, lock: TokenizedLeaseLock, token: str) -> None:
    """事件被回收时兜底释放锁，避免异常路径永久占用。"""
    try:
        fin = weakref.finalize(event, lock.release, token, "event_gc_finalizer")
        event.set_extra("防抖_finalizer", fin)
    except (TypeError, AttributeError):
        pass
