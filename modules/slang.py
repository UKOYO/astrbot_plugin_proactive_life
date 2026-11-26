"""群黑话（融合自 astrbot_plugin_slang_miner 的核心：群内高频短语挖掘）。

共享一次群消息旁路采集；命中后由 -900 注入点作为临时块追加（不落盘）。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 群黑话"
_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")

# 通俗易懂的口水词：谁都看得懂，不算黑话，采集时直接丢掉
_COMMON_WORDS = frozenset(
    "哈哈 呵呵 嘿嘿 嘻嘻 哈哈哈哈 哈哈哈 哈哈哈哈哈 笑死 哈哈哈啊 呜呜 呜呜呜 "
    "好的 好呀 好吧 收到  ok 是的 对的对的 没错 可以 行吧 知道 明白 了解 确实 "
    "谢谢 感谢 辛苦 麻烦 抱歉 对不起 没关系 不用谢 不客气 "
    "在吗 在干嘛 干嘛呢 干嘛 怎么了 什么 为什么 真的吗 是吗 好吧好吧 "
    "好嘞 好哦 好嘛 哎呀 哎呦 唉 额 呃 嗯嗯 嗯呢 哦哦 啊啊 额额 "
    "厉害 牛逼 牛啊 绝了 牛批 太强 牛 可以的 不错 挺好的 挺好 "
    "早安 晚安 中午好 早上好 晚上好 再见 拜拜 晚点聊 回头聊 "
    "我看看 我试试 我懂了 我知道了 我想想 等我 等下 马上 立刻 "
    "这个 那个 这些 那些 这里 那里 这样 那样 怎么 这样啊 是吗是吗 "
    "啊啊啊 哈哈哈 嘿嘿嘿 哦豁 哇哦 哇塞 卧槽 我靠 我去 我去啊 "
    "救命 要命 绝绝子 yyds 哈哈哈哈哈哈".split()
)


# 含这些字的多半是普通句子/口语，不是黑话
_NORMAL_SPEECH = re.compile(
    r"(我们|你们|他们|咱|大家|自己)"
    r"|(要|在|有|去|来|说|做|吃|喝|买|能|会|想|觉得|知道|应该|可能)"
    r"|(吗|呢|吧)"
)
# 语气助词结尾的普通短语（「不到」「可能学」「小时结束」这类）
_NORMAL_END = re.compile(r"(不到|结束|开始|吗|呢|吧|了啊|而已)$")


def _is_common_word(word: str) -> bool:
    """通俗易懂的口水词/普通句子，不值得当黑话。"""
    w = str(word or "").strip()
    if not w:
        return True
    if w in _COMMON_WORDS:
        return True
    # 纯重复笑声/语气词（哈哈哈哈、呜呜呜、啊啊啊…）
    if re.fullmatch(r"(哈|嘿|嘻|呵|呜|啊|哦|嗯|额|呃|呀){2,}", w):
        return True
    # 含代词/明确动词的普通短句
    if _NORMAL_SPEECH.search(w):
        return True
    if _NORMAL_END.search(w):
        return True
    return False


class SlangMiner:
    """按群统计短词频率，产出可注入的高频黑话。"""

    def __init__(self, min_hits: int = 3, window_sec: float = 86400.0) -> None:
        self._min_hits = max(2, int(min_hits))
        self._window = max(60.0, float(window_sec))
        self._counts: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._analysis: dict[str, dict] = {}  # word -> {meaning, is_slang, at}

    def update_config(self, min_hits: int | None = None) -> None:
        """面板改完阈值立即生效。"""
        if min_hits is not None:
            self._min_hits = max(2, int(min_hits))

    def set_analysis(self, items: list[dict]) -> int:
        """写入模型解析结果；返回写入条数。"""
        n = 0
        now = time.time()
        for it in items or []:
            word = str(it.get("word") or "").strip()
            if not word:
                continue
            self._analysis[word] = {
                "meaning": str(it.get("meaning") or "").strip()[:120],
                "is_slang": bool(it.get("is_slang", True)),
                "at": now,
            }
            n += 1
        return n

    def update_meaning(self, word: str, meaning: str) -> bool:
        """手动修改某个词的释义；返回是否成功。"""
        w = str(word or "").strip()
        if not w:
            return False
        entry = dict(self._analysis.get(w) or {})
        entry["meaning"] = str(meaning or "").strip()[:120]
        entry.setdefault("is_slang", True)
        entry["at"] = time.time()
        self._analysis[w] = entry
        return True

    def analysis(self) -> list[dict]:
        return [
            {"word": w, **v} for w, v in sorted(
                self._analysis.items(), key=lambda kv: -kv[1].get("at", 0)
            )
        ]

    def observe(self, gid: str, text: str, now: float | None = None) -> None:
        if not gid or not text:
            return
        now = now or time.time()
        bucket = self._counts[gid]
        for word in set(_WORD_RE.findall(text)):
            # 通俗易懂的口水词直接丢，不当黑话
            if _is_common_word(word):
                continue
            hits = bucket[word]
            hits.append(now)
            # 只留窗口内的时间戳
            cutoff = now - self._window
            while hits and hits[0] < cutoff:
                hits.pop(0)
        # 清理冷词，避免无限增长
        for word in [w for w, hits in bucket.items() if not hits]:
            bucket.pop(word, None)
        # 每群最多保留 40 词，超了淘汰命中次数最少的
        if len(bucket) > 40:
            keep = sorted(bucket, key=lambda w: len(bucket[w]), reverse=True)[:40]
            for w in [w for w in bucket if w not in set(keep)]:
                bucket.pop(w, None)

    def top(self, gid: str, limit: int = 8) -> list[str]:
        bucket = self._counts.get(gid) or {}
        ranked = sorted(
            ((sum(1 for _ in hits), w) for w, hits in bucket.items() if hits),
            reverse=True,
        )
        return [w for n, w in ranked if n >= self._min_hits][:max(1, limit)]

    def records(self, limit: int = 50) -> list[dict]:
        """面板用：只列达到阈值的黑话（1 次的普通聊天不算）。"""
        out: list[dict] = []
        for gid, bucket in self._counts.items():
            words = sorted(
                ((len(hits), w) for w, hits in bucket.items()
                 if len(hits) >= self._min_hits),
                reverse=True,
            )
            for count, word in words[:limit]:
                out.append({"group": gid, "word": word, "hits": count})
        out.sort(key=lambda r: (-r["hits"], r["group"], r["word"]))
        return out

    def hint(self, gid: str, cfg: Any = None) -> str:
        if not gid:
            return ""
        try:
            if cfg is not None and not bool(cfg.get("模块_黑话", True)):
                return ""
            # 注入条数读配置（默认收紧到 4 条），且只注入「已解析确认是真黑话」的词
            limit = 4
            only_confirmed = True
            if cfg is not None:
                try:
                    limit = max(1, min(10, int(cfg.get("黑话_最多条数", 4) or 4)))
                except (TypeError, ValueError):
                    limit = 4
                only_confirmed = bool(cfg.get("黑话_只注入已解析", True))
            # 多取一些再过滤，避免过滤后不够
            words = self.top(gid, limit=max(limit * 3, 16))
            if not words:
                return ""
            # 有解析结果的黑话带上含义；未解析/判定非黑话的按需过滤
            enriched = []
            for w in words:
                a = self._analysis.get(w)
                if only_confirmed:
                    # 只放行「模型确认是真黑话」的
                    if not a or not a.get("is_slang", True):
                        continue
                elif a and not a.get("is_slang", True):
                    continue
                if a and a.get("meaning"):
                    enriched.append(f"{w}（{a['meaning']}）")
                else:
                    enriched.append(w)
                if len(enriched) >= limit:
                    break
            if not enriched:
                return ""
            return "群里最近常说的话（可自然带出）：" + "、".join(enriched)
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s | 提示生成失败: %s", _LOG, exc)
            return ""

    def clear(self, gid: str | None = None) -> None:
        if gid:
            self._counts.pop(gid, None)
        else:
            self._counts.clear()
