"""真人节奏修剪 Mixin：消息回复前的拆条与标点处理。

从 main.py 拆出，不改变任何行为。依赖 self.cfg / self._module_on /
self._rhythm_last / self.context.send_message 等，由宿主类提供。
"""

from __future__ import annotations

import asyncio
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .rhythm_trimmer import (
    has_markdown, strip_faces, strip_trailing_punct, split_message,
)


# 补发首条的最小间隔：第一条还挂在 result.chain 里等宿主发送，
# gap 太小时它可能比第一条先到 QQ，导致气泡乱序。
_FIRST_SEG_MIN_GAP = 0.5


class RhythmMixin:
    """节奏修剪；由 ProactiveLifePlugin 继承。"""

    _rhythm_tasks: set = set()  # 补发任务的强引用，避免被 GC 回收；terminate 时统一取消

    @classmethod
    def cancel_rhythm_tasks(cls) -> None:
        """取消所有尚未完成的补发任务。"""
        for task in list(cls._rhythm_tasks):
            try:
                if not task.done():
                    task.cancel()
            except Exception:
                pass
        cls._rhythm_tasks.clear()

    def _rhythm_get(self, key: str, default=None):
        """节奏项取值。

        老配置里这些键曾经不带「节奏_」前缀，所以先查全名、再查短名，
        都查不到才回落 DEFAULT_CONFIG。之前是先查 self.cfg，而
        DEFAULT_CONFIG 里全是全名，短名分支永远走不到，等于死代码。
        """
        live = self.cfg._live
        defaults = getattr(self.cfg, "_defaults", {})
        if key in live:
            return live[key]
        short = key.replace("节奏_", "")
        if short != key and short in live:
            return live[short]
        if key in defaults:
            return defaults[key]
        if short in defaults:
            return defaults[short]
        return default

    def _rhythm_on(self, key: str) -> bool:
        return bool(self._rhythm_get(key, False))

    def _rhythm_num(self, key: str) -> int:
        defaults = getattr(self.cfg, "_defaults", {})
        try:
            return int(self._rhythm_get(key, 0) or 0)
        except (TypeError, ValueError):
            return int(defaults.get(key, 0))

    def _rhythm_target(self) -> int:
        return max(6, self._rhythm_num("节奏_每段字数"))

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
        for i in sorted(plain_idx[1:], reverse=True):
            try:
                del result.chain[i]
            except (IndexError, TypeError):
                pass

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
                # 首条保底延迟：第一条还挂在 result.chain 里等宿主发，
                # gap 太小（如 0.2s）时它可能比第一条先到，导致气泡乱序。
                delay = max(gap, _FIRST_SEG_MIN_GAP)
                await asyncio.sleep(delay)
                try:
                    await event.send(event.plain_result(seg))
                except Exception as exc:
                    logger.warning("[节奏修剪] 补发失败: %s", exc)
                    return

        try:
            task = asyncio.create_task(_send_rest())
            self._rhythm_tasks.add(task)
            task.add_done_callback(self._rhythm_tasks.discard)
        except Exception as exc:
            logger.warning("[节奏修剪] 无法创建补发任务，回退整条发送: %s", exc)
            result.chain[plain_idx[0]].text = fallback_text

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

        # 第一条留在原 chain，其余异步补发。
        # 多余 Plain 组件直接移出 chain（留空文本会让部分适配器报错/发空气泡）。
        result.chain[plain_idx[0]].text = segments[0]
        for i in sorted(plain_idx[1:], reverse=True):
            try:
                del result.chain[i]
            except (IndexError, TypeError):
                pass

        rest = segments[1:]
        if not rest:
            return

        gap = max(0.0, float(self._rhythm_get("节奏_间隔秒数", 0.2) or 0))
        if len(segments) > 6:
            # 长文条数多，别按打字节奏慢慢磨，不然发一分钟
            gap = min(gap, 0.3)

        await self._rhythm_send_rest(event, rest, gap, result, plain_idx, text)
