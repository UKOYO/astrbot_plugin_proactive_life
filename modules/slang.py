"""群黑话（融合自 astrbot_plugin_slang_miner 的核心：群内高频短语挖掘）。

共享一次群消息旁路采集；命中后由 -900 注入点作为临时块追加（不落盘）。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any, Iterable

from astrbot.api import logger

_LOG = "proactive_life | 群黑话"
_WORD_RE = re.compile(r"[一-鿿]{2,8}")
_CQ_AT_RE = re.compile(r"\[CQ:at,[^\]]*\]")
_MENTION_RE = re.compile(r"@[^\s@，。！？、；：:,.!?()（）\[\]【】]{1,20}")

_COMMON_WORDS = frozenset(
    "哈哈 呵呵 嘿嘿 嘻嘻 哈哈哈哈 哈哈哈 哈哈哈哈哈 笑死 哈哈哈啊 呜呜 呜呜呜 "
    "好的 好呀 好吧 收到 ok 是的 对的对的 没错 可以 行吧 知道 明白 了解 确实 "
    "谢谢 感谢 辛苦 麻烦 抱歉 对不起 没关系 不用谢 不客气 "
    "在吗 在干嘛 干嘛呢 干嘛 怎么了 什么 为什么 真的吗 是吗 好吧好吧 "
    "好嘞 好哦 好嘛 哎呀 哎呦 唉 额 呃 嗯嗯 嗯呢 哦哦 啊啊 额额 "
    "厉害 牛逼 牛啊 绝了 牛批 太强 牛 可以的 不错 挺好的 挺好 "
    "早安 晚安 中午好 早上好 晚上好 再见 拜拜 晚点聊 回头聊 "
    "我看看 我试试 我懂了 我知道了 我想想 等我 等下 马上 立刻 "
    "这个 那个 这些 那些 这里 那里 这样 那样 怎么 这样啊 是吗是吗 "
    "啊啊啊 哈哈哈 嘿嘿嘿 哦豁 哇哦 哇塞 卧槽 我靠 我去 我去啊 "
    "救命 要命 绝绝子 yyds 哈哈哈哈哈哈 "
    "大佬 群主 管理 管理员 群友 小伙伴 小姐姐 小哥哥 老哥 老弟 老妹 兄弟 姐妹 "
    "朋友 老师 老板 同学 学长 学姐 师傅 大叔 阿姨 宝贝 宝宝 老婆 老公 亲爱的 "
    "大家 各位 诸位 哥们 姐们 佬们 兄弟们 姐妹们".split()
)

_COMMON_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费"
    "廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和"
    "穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜"
    "阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞"
    "万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程"
    "嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧"
    "隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶"
    "幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍璩桑桂濮牛寿通边扈燕冀郏浦尚农温别"
    "庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘国文"
    "东殴沃曾关红游竺权逯盖益桓公"
)
_SURNAME_PREFIX_RE = re.compile(rf"^[小老大阿][{_COMMON_SURNAMES}]$")
_SURNAME_SUFFIX_RE = re.compile(
    rf"^[{_COMMON_SURNAMES}](?:哥|姐|总|神|佬|叔|姨|爷|奶|弟|妹|老师|同学|老板|酱|宝)$"
)

_NORMAL_SPEECH = re.compile(
    r"(我们|你们|他们|咱|大家|自己)"
    r"|(要|在|有|去|来|说|做|吃|喝|买|能|会|想|觉得|知道|应该|可能)"
    r"|(吗|呢|吧)"
)
_NORMAL_END = re.compile(r"(不到|结束|开始|吗|呢|吧|了啊|而已)$")


def _strip_mentions(text: str) -> str:
    """剥离消息里的 @提及 / CQ 艾特码，避免被艾特人的昵称被采成黑话。"""
    s = _CQ_AT_RE.sub(" ", str(text or ""))
    return _MENTION_RE.sub(" ", s)


def _extract_name_tokens(name: str) -> set[str]:
    """从群名片/昵称中提取需要屏蔽的中文人名片段（长度 2..8）。"""
    raw = str(name or "").strip().lstrip("@").strip()
    if not raw:
        return set()
    tokens: set[str] = set()
    if 2 <= len(raw) <= 8 and _WORD_RE.fullmatch(raw):
        tokens.add(raw)
    for sp in _WORD_RE.findall(raw):
        if 2 <= len(sp) <= 8:
            tokens.add(sp)
    return tokens


def _is_common_word(word: str) -> bool:
    """通俗易懂的口水词/普通句子/常见姓氏称呼，不值得当黑话。"""
    w = str(word or "").strip()
    if not w or w in _COMMON_WORDS:
        return True
    if re.fullmatch(r"(哈|嘿|嘻|呵|呜|啊|哦|嗯|额|呃|呀){2,}", w):
        return True
    if _SURNAME_PREFIX_RE.fullmatch(w) or _SURNAME_SUFFIX_RE.fullmatch(w):
        return True
    if _NORMAL_SPEECH.search(w) or _NORMAL_END.search(w):
        return True
    return False


class SlangMiner:
    """按群统计短词频率，产出可注入的高频黑话。"""

    def __init__(self, min_hits: int = 3, window_sec: float = 86400.0) -> None:
        self._min_hits = max(2, int(min_hits))
        self._window = max(60.0, float(window_sec))
        self._counts: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._analysis: dict[str, dict] = {}
        self._ignored_names: set[str] = set()
        self._group_names: dict[str, set[str]] = defaultdict(set)

    def update_config(self, min_hits: int | None = None) -> None:
        if min_hits is not None:
            self._min_hits = max(2, int(min_hits))

    def register_names(self, names: Iterable[str], gid: str | None = None) -> int:
        """登记群成员/熟人名字并顺带清理历史误采的同名条目。"""
        added: set[str] = set()
        target_set = self._group_names[gid] if gid else self._ignored_names
        for raw in names or ():
            for tok in _extract_name_tokens(raw):
                if tok not in target_set:
                    target_set.add(tok)
                    added.add(tok)
                if tok not in self._ignored_names:
                    self._ignored_names.add(tok)
                    added.add(tok)
        if added:
            for bucket in self._counts.values():
                for tok in added:
                    bucket.pop(tok, None)
            self._prune_orphan_analysis(added)
        return len(added)

    def known_names(self, gid: str | None = None, limit: int = 80) -> list[str]:
        pool: set[str] = set(self._ignored_names)
        if gid and gid in self._group_names:
            pool.update(self._group_names[gid])
        else:
            for s in self._group_names.values():
                pool.update(s)
        return sorted(pool)[:max(1, limit)]

    def _is_known_name(self, gid: str, word: str) -> bool:
        if word in self._ignored_names:
            return True
        gset = self._group_names.get(gid)
        return bool(gset and word in gset)

    def _prune_orphan_analysis(self, words: Iterable[str] | None = None) -> None:
        active: set[str] = set()
        for bucket in self._counts.values():
            active.update(bucket.keys())
        targets = list(self._analysis.keys()) if words is None else list(words)
        for w in targets:
            if w not in active:
                self._analysis.pop(w, None)

    def set_analysis(self, items: list[dict]) -> int:
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

    def observe(
        self,
        gid: str,
        text: str,
        now: float | None = None,
        *,
        sender_name: str = "",
        extra_names: Iterable[str] = (),
    ) -> None:
        if not gid:
            return
        names_to_reg: list[str] = []
        if sender_name:
            names_to_reg.append(sender_name)
        if extra_names:
            names_to_reg.extend(extra_names)
        if names_to_reg:
            self.register_names(names_to_reg, gid=gid)
        if not text:
            return
        cleaned = _strip_mentions(text)
        if not cleaned.strip():
            return
        now = now or time.time()
        bucket = self._counts[gid]
        for word in set(_WORD_RE.findall(cleaned)):
            if _is_common_word(word) or self._is_known_name(gid, word):
                continue
            hits = bucket[word]
            hits.append(now)
            cutoff = now - self._window
            while hits and hits[0] < cutoff:
                hits.pop(0)
        for word in [w for w, hits in bucket.items() if not hits]:
            bucket.pop(word, None)
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
        out: list[dict] = []
        for gid, bucket in self._counts.items():
            words = sorted(
                ((len(hits), w) for w, hits in bucket.items()
                 if len(hits) >= self._min_hits and not self._is_known_name(gid, w)),
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
            limit = 4
            only_confirmed = True
            if cfg is not None:
                try:
                    limit = max(1, min(10, int(cfg.get("黑话_最多条数", 4) or 4)))
                except (TypeError, ValueError):
                    limit = 4
                only_confirmed = bool(cfg.get("黑话_只注入已解析", True))
            words = self.top(gid, limit=max(limit * 3, 16))
            if not words:
                return ""
            enriched = []
            for w in words:
                if self._is_known_name(gid, w):
                    continue
                a = self._analysis.get(w)
                if only_confirmed:
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

    def remove(self, word: str, gid: str | None = None) -> int:
        """精确删除单条黑话（指定群则仅删该群；不指定群则从所有群删除该词）。"""
        w = str(word or "").strip()
        if not w:
            return 0
        removed = 0
        if gid:
            bucket = self._counts.get(gid)
            if bucket and w in bucket:
                bucket.pop(w, None)
                removed += 1
                if not bucket:
                    self._counts.pop(gid, None)
        else:
            for g in list(self._counts.keys()):
                bucket = self._counts[g]
                if w in bucket:
                    bucket.pop(w, None)
                    removed += 1
                    if not bucket:
                        self._counts.pop(g, None)
            if w in self._analysis:
                self._analysis.pop(w, None)
                removed = max(removed, 1)
        self._prune_orphan_analysis([w])
        return removed

    def remove_many(self, items: Iterable[dict]) -> int:
        """批量删除选中的黑话记录，每项形如 {"word": "...", "group": "..."}。"""
        total = 0
        touched: set[str] = set()
        for it in items or ():
            if not isinstance(it, dict):
                continue
            w = str(it.get("word") or "").strip()
            g = str(it.get("group") or "").strip() or None
            if not w:
                continue
            touched.add(w)
            if g:
                bucket = self._counts.get(g)
                if bucket and w in bucket:
                    bucket.pop(w, None)
                    total += 1
                    if not bucket:
                        self._counts.pop(g, None)
            else:
                total += self.remove(w, None)
        if touched:
            self._prune_orphan_analysis(touched)
        return total

    def clear(self, gid: str | None = None) -> None:
        if gid:
            self._counts.pop(gid, None)
            self._prune_orphan_analysis()
        else:
            self._counts.clear()
            self._analysis.clear()
