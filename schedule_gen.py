"""日程生成（从 astrbot_plugin_life_scheduler 移植并精简）。

参考原插件：每天按提示词生成「穿搭 + 日程」，用创意池（主题/心情/穿搭风格/
日程类型）做随机约束，校验模型必须遵守穿搭风格，失败重试一次。

与原插件的差异（刻意简化，已在文档标注）：
- 不移植「用户补充要求的正则拆解与逐项校验」（原插件约 80 行边界处理），
  改为把补充要求作为最高优先级约束写进 prompt，不做逐项断言。
- 不自动清理 LLM 会话（原插件会 delete_conversation），避免误删用户会话。

存储：中樞自己 data 目录下的 schedule_data.json，结构与原插件兼容。
"""

from __future__ import annotations

import datetime
import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger

# ---------------------------------------------------------------- 创意池（服装库）

POOL_DEFAULTS: dict[str, list[str]] = {
    "daily_themes": [
        "探索日", "社交日", "宅家日", "工作日", "自我提升日", "休闲放松日",
        "创意日", "运动日", "整理日", "美食日", "文艺日", "随性漫游日",
    ],
    "mood_colors": [
        "慵懒", "活力", "优雅", "俏皮", "神秘", "温柔",
        "冷艳", "甜美", "知性", "随性", "浪漫", "清新",
    ],
    "outfit_styles": [
        "知性学院风", "街头休闲风", "温柔淑女风", "酷飒中性风", "慵懒居家风",
        "精致约会风", "运动活力风", "日系森女风", "法式优雅风", "韩系甜美风",
        "复古文艺风", "极简都市风", "甜酷混搭风", "民族风情风", "暗黑系风格",
    ],
    "schedule_types": [
        "户外活动型", "社交聚会型", "独处充电型", "技能学习型", "随性漫游型",
        "家务整理型", "工作专注型", "休闲娱乐型", "健身运动型", "美食探索型",
        "文化艺术型", "购物采买型",
    ],
}

DEFAULT_PROMPT_TEMPLATE = """# Role: Life Scheduler
请根据以下信息，为自己规划一份今天的生活安排。

## Context
- 日期：{date_str} {weekday} {holiday}
- 人设：{persona_desc}

## 今日创意约束（必须遵循）
- 今日主题：【{daily_theme}】
- 心情色彩：【{mood_color}】
- 穿搭风格（必须严格遵循）：【{outfit_style}】
- 日程类型：【{schedule_type}】

## 需要避免的重复内容
{history_schedules}

## 参考信息
{recent_chats}

## 强制约束（必须严格遵循）
- 你必须严格遵循穿搭风格：【{outfit_style}】（不得替换/混用其他风格）。
- 你必须只输出 JSON 对象本体（不要 Markdown/代码块/解释）。
- JSON 必须包含字段 "outfit_style"，且其值必须严格等于 "{outfit_style}"。
- 字段 "outfit" 的第一行必须以 "风格：{outfit_style}" 开头。

## Tasks
1. outfit: 今日穿搭描述（从里到外、从上到下、衣裤内衣裤鞋袜、饰品等，符合天气和心情）
2. schedule: 规划今日日程，每条写成「HH:MM 做什么」的形式，按时间顺序。

## Output Format
请严格返回 JSON：
{{
  "outfit_style": "{outfit_style}",
  "outfit": "...",
  "schedule": "..."
}}
"""

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 配置里的中文池名 -> POOL_DEFAULTS 的英文键
_POOL_ALIAS = {
    "今日主题池": "daily_themes",
    "心情色彩池": "mood_colors",
    "穿搭风格池": "outfit_styles",
    "日程类型池": "schedule_types",
}

_STYLE_PREFIX_RE = re.compile(
    r"^\s*(?:【?风格】?|\[?风格\]?)\s*[:：]\s*(?P<style>.+?)(?:\n|$)"
)
_VAR_RE = re.compile(r"\{(\w+)\}")


@dataclass
class ScheduleData:
    """单日日程（与 life_scheduler 的字段对齐）。"""

    date: str
    outfit_style: str = ""
    outfit: str = ""
    schedule: str = ""
    status: str = "ok"
    reason: str = ""  # 失败原因，供面板显示

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleData":
        return cls(
            date=str(data.get("date") or ""),
            outfit_style=str(data.get("outfit_style") or ""),
            outfit=str(data.get("outfit") or ""),
            schedule=str(data.get("schedule") or ""),
            status=str(data.get("status") or "ok"),
        )


class ScheduleStore:
    """日程的 JSON 存取（原子写，损坏时降级为空而不是清空文件）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, ScheduleData] = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("proactive_life | 日程文件读取失败，按空处理: %s", e)
            return
        if not isinstance(raw, dict):
            return
        for date_str, item in raw.items():
            if isinstance(item, dict):
                try:
                    self._data[str(date_str)] = ScheduleData.from_dict(item)
                except Exception:
                    continue

    def get(self, date: Any) -> ScheduleData | None:
        return self._data.get(_to_date_str(date))

    def set(self, data: ScheduleData) -> None:
        self._data[data.date] = data
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            payload = {d: asdict(v) for d, v in self._data.items()}
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except (OSError, TypeError, ValueError) as e:
            logger.warning("proactive_life | 日程落盘失败: %s", e)

    def history(self, before: datetime.date, days: int) -> list[ScheduleData]:
        out: list[ScheduleData] = []
        for i in range(1, max(0, days) + 1):
            d = self.get(before - datetime.timedelta(days=i))
            if d and d.status == "ok":
                out.append(d)
        return out

    def all(self) -> dict[str, ScheduleData]:
        return dict(self._data)


def _to_date_str(value: Any) -> str:
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _safe_text(resp: Any) -> str:
    if resp is None:
        return ""
    for key in ("completion_text", "completion", "text", "content"):
        v = getattr(resp, key, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def extract_json_obj(text: str) -> dict | None:
    """从模型输出里抠出第一个完整 JSON 对象（容忍 ```json 包裹和前后废话）。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"```\s*$", "", s, flags=re.MULTILINE)
    start = s.find("{")
    if start < 0:
        return None
    brace = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace == 0:
                try:
                    data = json.loads(s[start: i + 1])
                    return data if isinstance(data, dict) else None
                except Exception:
                    return None
    return None


class ScheduleGenerator:
    """按提示词生成日程。"""

    def __init__(self, context: Any, cfg: Any, store: ScheduleStore) -> None:
        self.context = context
        self.cfg = cfg
        self.store = store
        self._busy = False

    # ------------------------------------------------ 配置读取
    def _cfg(self, key: str, default=None):
        try:
            return self.cfg.get(key, default)
        except Exception:
            return default

    def _pool(self, name: str) -> list[str]:
        """取池子：优先配置（日程_<中文池名>），否则回落到内置默认。"""
        raw = self._cfg(f"日程_{name}", None)
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw if str(x).strip()]
        return list(POOL_DEFAULTS.get(_POOL_ALIAS.get(name, name), []))

    def _template(self) -> str:
        t = str(self._cfg("日程_提示词模板", "") or "").strip()
        return t or DEFAULT_PROMPT_TEMPLATE

    # ------------------------------------------------ 上下文
    def _holiday(self, d: datetime.date) -> str:
        try:
            import holidays  # 可选依赖

            name = holidays.CN().get(d)
            return f"今天是 {name}" if name else ""
        except Exception:
            return ""

    async def _persona(self) -> str:
        try:
            p = await self.context.persona_manager.get_default_persona_v3()
            return (p.get("prompt") if isinstance(p, dict) else getattr(p, "prompt", "")) or ""
        except Exception:
            return ""

    def _recent_explorations(self, limit: int = 5) -> str:
        try:
            from .common import hub_data_path

            f = hub_data_path() / "motive_pool.json"
            if not f.exists():
                return "（无近期摸鱼记录）"
            raw = json.loads(f.read_text(encoding="utf-8"))
            items = [
                f"- {str(m.get('content') or '').strip()}"
                for m in (raw.get("motives") or [])[-limit:]
                if str(m.get("content") or "").strip()
            ]
            return "\n".join(items) if items else "（无近期摸鱼记录）"
        except Exception:
            return "（无近期摸鱼记录）"

    async def _recent_chats(self, umo: str | None) -> str:
        try:
            if not umo:
                return "无近期对话"
            cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not cid:
                return "无近期对话"
            conv = await self.context.conversation_manager.get_conversation(umo, cid)
            if not conv or not conv.history:
                return "无近期对话"
            hist = json.loads(conv.history)
            n = max(2, int(self._cfg("日程_参考最近条数", 10) or 10))
            lines = []
            for m in hist[-n:]:
                role = m.get("role", "")
                content = m.get("content", "")
                if not content:
                    continue
                lines.append(f"{'用户' if role == 'user' else '我'}: {str(content)[:160]}")
            return "\n".join(lines) or "无近期对话"
        except Exception as e:
            logger.debug("proactive_life | 日程读取最近聊天失败: %s", e)
            return "无近期对话"

    def _pick(self, d: datetime.date) -> dict[str, str]:
        """按配置池随机挑创意约束；穿搭风格尽量避开近几天的。"""
        def _safe_choice(pool: list[str]) -> str:
            return random.choice(pool) if pool else ""

        styles = self._pool("穿搭风格池")
        lookback = max(0, int(self._cfg("日程_参考历史天数", 3) or 3))
        if styles and lookback > 0 and len(styles) > 1:
            used = {(h.outfit_style or "").strip() for h in self.store.history(d, lookback)}
            used.discard("")
            candidates = [s for s in styles if s not in used]
            style = random.choice(candidates or styles)
        else:
            style = _safe_choice(styles)
        return {
            "daily_theme": _safe_choice(self._pool("今日主题池")),
            "mood_color": _safe_choice(self._pool("心情色彩池")),
            "outfit_style": style,
            "schedule_type": _safe_choice(self._pool("日程类型池")),
        }

    def _history_text(self, d: datetime.date) -> str:
        days = max(0, int(self._cfg("日程_参考历史天数", 3) or 3))
        rows = self.store.history(d, days)
        if not rows:
            return "（无历史记录）"
        out = []
        for h in rows:
            style = h.outfit_style or ""
            out.append(
                f"[{h.date}] 风格：{style} 穿搭：{h.outfit[:40]} 日程：{h.schedule[:60]}"
            )
        return "\n".join(out)

    # ------------------------------------------------ prompt
    def build_prompt(self, ctx: dict, extra: str = "") -> str:
        tmpl = self._template()
        values = dict(ctx)
        # 模板里出现的未知占位符统一补空，避免 KeyError
        for var in _VAR_RE.findall(tmpl):
            values.setdefault(var, "")
        def _repl(m: re.Match) -> str:
            return str(values.get(m.group(1), ""))
        prompt = _VAR_RE.sub(_repl, tmpl)
        if extra:
            prompt += (
                "\n\n## 用户补充强制约束（最高优先级，必须严格遵循）\n"
                f"- 用户补充要求：{extra}\n"
                "- 该要求优先级高于今日主题、心情色彩、穿搭风格、日程类型和历史参考。\n"
                "- 若与随机创意池冲突，一律以用户补充要求为准，不得忽略或弱化。\n"
                '- 必须只输出 JSON 本体，字段为 "outfit_style"、"outfit"、"schedule"。\n'
                '- 用户点名了具体衣物时，"outfit" 必须直接包含这些元素。\n'
            )
        return prompt

    # ------------------------------------------------ 调用与校验
    async def _call_llm(self, prompt: str) -> str:
        from .common import get_chat_provider, get_provider_by_id

        pid = str(self._cfg("日程_生成模型", "") or "").strip()
        prov = get_provider_by_id(self.context, pid)
        if prov is None:
            prov = get_chat_provider(self.context)
        if prov is None:
            raise RuntimeError("没有可用的聊天模型")
        # 空回复重试一次（原插件行为）
        for attempt in range(2):
            resp = await prov.text_chat(prompt=prompt, system_prompt="")
            text = _safe_text(resp)
            if text:
                return text
            if attempt == 0:
                logger.debug("proactive_life | 日程生成空回复，重试一次")
        raise RuntimeError("模型返回为空")

    @staticmethod
    def validate(payload: dict | None, required_style: str) -> tuple[bool, str]:
        if not payload:
            return False, "未能解析出 JSON"
        outfit = str(payload.get("outfit") or "").strip()
        schedule = str(payload.get("schedule") or "").strip()
        if not outfit:
            return False, "outfit 为空"
        if not schedule:
            return False, "schedule 为空"
        if not required_style:
            return True, ""
        model_style = str(payload.get("outfit_style") or "").strip()
        if model_style != required_style:
            return False, f'outfit_style 必须等于 "{required_style}"'
        if not re.match(
            rf"^\s*(?:风格|【风格】|\[风格\])\s*[:：]\s*{re.escape(required_style)}(?:\s|$)",
            outfit,
        ):
            return False, f'outfit 首行必须是 "风格：{required_style}"'
        return True, ""

    # ------------------------------------------------ 对外入口
    async def generate(
        self,
        date: datetime.datetime | None = None,
        umo: str | None = None,
        extra: str | None = None,
    ) -> ScheduleData:
        """生成指定日期的日程；失败返回 status=failed 的数据（不抛）。"""
        if self._busy:
            raise RuntimeError("已有日程生成任务在进行中")
        self._busy = True
        try:
            d = date or datetime.datetime.now()
            date_str = d.strftime("%Y-%m-%d")
            picks = self._pick(d.date())
            ctx = {
                "date_str": d.strftime("%Y年%m月%d日"),
                "weekday": _WEEKDAYS[d.weekday()],
                "holiday": self._holiday(d.date()),
                "persona_desc": await self._persona(),
                "history_schedules": self._history_text(d.date()),
                "recent_chats": await self._recent_chats(umo),
                "recent_explorations": self._recent_explorations(),
                **picks,
            }
            prompt = self.build_prompt(ctx, str(extra or "").strip())
            required_style = "" if extra else picks["outfit_style"]

            content = await self._call_llm(prompt)
            payload = extract_json_obj(content)
            ok, reason = self.validate(payload, required_style)
            if not ok:
                # 风格不符时按原插件思路追问一次
                logger.debug("proactive_life | 日程首次校验失败（%s），重试一次", reason)
                repair = (
                    prompt
                    + f"\n\n## 上次输出不合格：{reason}\n请严格按上面的格式与风格要求重新输出 JSON。"
                )
                content = await self._call_llm(repair)
                payload = extract_json_obj(content)
                ok, reason = self.validate(payload, required_style)
            if not ok or not payload:
                raise ValueError(f"模型未遵循生成约束：{reason}")

            data = ScheduleData(
                date=date_str,
                outfit_style=str(payload.get("outfit_style") or picks["outfit_style"] or "").strip(),
                outfit=str(payload.get("outfit") or "").strip(),
                schedule=str(payload.get("schedule") or "").strip(),
                status="ok",
            )
            self.store.set(data)
            logger.info("proactive_life | 日程生成成功 %s（风格：%s）", date_str, data.outfit_style)
            return data
        except Exception as e:
            logger.warning("proactive_life | 日程生成失败: %s", e)
            return ScheduleData(
                date=(date or datetime.datetime.now()).strftime("%Y-%m-%d"),
                outfit="", schedule="", status="failed", reason=str(e)[:160],
            )
        finally:
            self._busy = False
