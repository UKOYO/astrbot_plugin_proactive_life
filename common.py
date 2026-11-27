"""跨 handler 共用的常數與路徑解析，避免硬編碼散落各處。"""

import datetime
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger


def _debug(msg: str, err: BaseException | None = None) -> None:
    """统一 debug 日志：框架 API 边界失败时留痕，避免静默失效难排查。"""
    try:
        if err is None:
            logger.debug("proactive_life | %s", msg)
        else:
            logger.debug("proactive_life | %s: %s", msg, err)
    except Exception:
        pass


def _fallback_data_dir() -> Path:
    """跨平台兜底資料目錄：優先用 AstrBot 的 data dir，再次用家目錄。

    不再寫死任何絕對路徑，Linux/macOS/Windows 都能用。
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(str(get_astrbot_data_path())) / "plugin_data"
    except Exception:
        return Path.home() / "AstrBot" / "data" / "plugin_data"

# 舊版 life_scheduler 外掛的資料目錄名；仅用于在 AstrBot data dir 下定位。
SCHEDULER_PLUGIN_DIR = "astrbot_plugin_life_scheduler"

# ---------------------------------------------------------------- 通用小工具

# LLM 回覆里出现这些串，基本可判定为上游错误而非正常回复。
# 之前这段清单在 private/group/worker 各抄一份，还一度漏了最后一条。
LLM_ERROR_MARKERS = (
    "all chat models failed",
    "clienterror",
    "badrequesterror",
    "upstream_error",
    "error code:",
    "invalid request parameter",
    "the requested resource was not found",
)

DEFAULT_QUIET_HOURS = (2, 3, 4, 5, 6)


def is_llm_error_text(text: str) -> bool:
    """判断一段 LLM 输出是不是上游错误串。"""
    t = str(text or "").lower()
    return any(k in t for k in LLM_ERROR_MARKERS)


def get_chat_provider(context: Any, umo: str | None = None):
    """取当前聊天模型。

    注意：AstrBot 4.x 的 `Context` 并没有 `provider_manager` 属性，
    而是直接提供 get_using_provider(umo) / get_provider_by_id(id) /
    get_all_providers()。旧代码写 `context.provider_manager` 会直接
    AttributeError 并被上层吞掉，导致「日程生成 / 模型判定 / 短期总结」
    静默失效（模型根本没被调用）。这里统一走 Context 的公开方法。
    """
    if context is None:
        return None
    # 首选：Context 公开方法
    try:
        fn = getattr(context, "get_using_provider", None)
        if callable(fn):
            return fn(umo) if umo else fn()
    except Exception as e:
        _debug("get_using_provider 调用失败，尝试兼容回退", e)
    # 兼容：某些版本/包装对象仍暴露 provider_manager
    pm = getattr(context, "provider_manager", None)
    if pm is None:
        return None
    try:
        ptype = None
        try:
            from astrbot.core.provider.entities import ProviderType

            ptype = getattr(ProviderType, "CHAT_COMPLETION", None) or getattr(ProviderType, "CHAT", None)
        except Exception:
            ptype = None
        if ptype is not None:
            return pm.get_using_provider(ptype, umo=umo) if umo else pm.get_using_provider(ptype)
        return pm.get_using_provider()
    except Exception as e:
        _debug("provider_manager.get_using_provider 失败", e)
        return None


def get_provider_by_id(context: Any, provider_id: str | None):
    """按 id 取模型；id 为空返回 None。"""
    pid = str(provider_id or "").strip()
    if not pid or context is None:
        return None
    try:
        fn = getattr(context, "get_provider_by_id", None)
        if callable(fn):
            return fn(pid)
    except Exception as e:
        _debug(f"get_provider_by_id({pid}) 失败，尝试兼容回退", e)
    pm = getattr(context, "provider_manager", None)
    try:
        return pm.get_provider_by_id(pid) if pm else None
    except Exception as e:
        _debug(f"provider_manager.get_provider_by_id({pid}) 失败", e)
        return None


def list_chat_providers(context: Any) -> list:
    """列出所有聊天类模型（用于面板下拉）。"""
    if context is None:
        return []
    try:
        fn = getattr(context, "get_all_providers", None)
        if callable(fn):
            return list(fn() or [])
    except Exception:
        pass
    pm = getattr(context, "provider_manager", None)
    try:
        return list(getattr(pm, "provider_insts", None) or [])
    except Exception:
        return []


def parse_quiet_hours(raw, default=DEFAULT_QUIET_HOURS) -> list[int]:
    """把免打扰时段配置解析成小时列表，脏值直接跳过。"""
    if raw is None:
        raw = list(default)
    if isinstance(raw, (int, float)):
        raw = [raw]
    hours: list[int] = []
    for item in raw:
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23:
            hours.append(hour)
    return sorted(set(hours))


# ---------------------------------------------------------------- 会话 Key 归一化

def canonical_group_key(origin_or_gid: Any) -> str:
    """把「完整 origin」或「裸群号」统一成裸群号。

    群静默（mute_until）与群情绪这些表，写入端用的是完整 origin
    （``platform:GroupMessage:gid``），读取端却用裸 gid，两边永远对不上，
    功能就静默失效了。全插件读写群相关 Key 必须过这里。
    """
    s = str(origin_or_gid or "").strip()
    if not s:
        return ""
    if ":" in s:
        return s.rsplit(":", 1)[-1].strip()
    return s


def canonical_session_key(event: Any) -> str:
    """统一会话 Key：私聊用 uid，群聊用裸群号（与情绪/静默的读写端一致）。"""
    if event is None:
        return ""
    try:
        if event.is_private_chat():
            return str(event.get_sender_id() or "")
        gid = getattr(event, "get_group_id", lambda: "")()
        return canonical_group_key(gid)
    except Exception as e:  # noqa: BLE001 - 事件字段缺失时按空 Key 处理
        _debug("会话 Key 归一化失败", e)
        return ""


# ---------------------------------------------------------------- 平台实例 / UMO

def iter_platform_insts(context: Any) -> list:
    """遍历已加载的平台实例。

    AstrBot 不同调用点/版本属性名不一致（``platform_manager.get_insts()`` 与
    ``platform_manager.platform_insts``），这里兼容两套，避免某处静默拿不到。
    """
    pm = getattr(context, "platform_manager", None) if context is not None else None
    if pm is None:
        return []
    getter = getattr(pm, "get_insts", None)
    if callable(getter):
        try:
            insts = list(getter() or [])
            if insts:
                return insts
        except Exception as e:  # noqa: BLE001 - 退回属性访问
            _debug("platform_manager.get_insts() 失败，改用 platform_insts", e)
    try:
        return list(getattr(pm, "platform_insts", None) or [])
    except Exception as e:  # noqa: BLE001
        _debug("platform_manager.platform_insts 读取失败", e)
        return []


def platform_id(context: Any, default: str = "") -> str:
    """取第一个已加载平台适配器的 id（拼 UMO 用）。"""
    for inst in iter_platform_insts(context):
        pid = getattr(getattr(inst, "metadata", None), "id", None)
        if pid:
            return str(pid)
    return default


def resolve_umo(
    context: Any, msg_type: str, target_id: str, default_platform: str = "aiocqhttp"
) -> str:
    """拼一个会话 UMO：``platform:MessageType:target_id``。"""
    pid = platform_id(context, default_platform) or default_platform
    return f"{pid}:{msg_type}:{target_id}"



def scheduler_data_path() -> Path:
    """回傳 life_scheduler 的資料目錄。

    優先用 AstrBot 自己的 data dir 推導，找不到時才回退到舊的絕對路徑，
    避免換安裝目錄就失效，同時保留舊環境的相容性。
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(str(get_astrbot_data_path())) / "plugin_data" / SCHEDULER_PLUGIN_DIR
    except Exception:
        return _fallback_data_dir() / SCHEDULER_PLUGIN_DIR


HUB_PLUGIN_NAME = "astrbot_plugin_proactive_life"


def hub_data_path() -> Path:
    """中樞自己的資料目錄（日程、動機池、狀態檔都放這）。"""
    try:
        from astrbot.core.star.star_tools import StarTools

        return Path(str(StarTools.get_data_dir(HUB_PLUGIN_NAME)))
    except Exception:
        return _fallback_data_dir() / HUB_PLUGIN_NAME


def schedule_files() -> list[Path]:
    """日程檔候選：中樞自產的優先，其次沿用 life_scheduler 產的。"""
    return [hub_data_path() / "schedule_data.json",
            scheduler_data_path() / "schedule_data.json"]


# ---------------------------------------------------------------- 日程读取（只读取，不生成）

_SCHEDULE_TIME_RE = re.compile(
    r"(?m)^\s*(?:[-*•·]\s*)?(?:\d+[.)、]\s*)?(?:[^\d\n]{0,12}?\s*)?"
    r"(?P<hour>[01]?\d|2[0-3])(?:[:：](?P<minute>[0-5]?\d)"
    r"|点(?:(?P<half>半)|(?P<minute_cn>[0-5]?\d)?分?))\s*(?P<text>.+?)\s*$"
)


def _business_date(schedule_time: str | None, now=None) -> str:
    """按日程生成时间边界计算「业务今日」，与 life_scheduler 的 resolve_business_now 一致。"""
    now = now or datetime.datetime.now()
    try:
        h, m = map(int, str(schedule_time or "00:00").split(":", 1))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            h, m = 0, 0
    except Exception:
        h, m = 0, 0
    boundary = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < boundary:
        now = now - datetime.timedelta(days=1)
    return now.strftime("%Y-%m-%d")


# 日程解析结果缓存：被动回复热路径 + 面板每 4 秒轮询都会读，没必要每次都读盘。
# Key = (业务日期, ((path, mtime_ns, size), ...))，文件一变就自动失效。
_SCHEDULE_ENTRY_CACHE: dict[tuple, dict] = {}
_SCHEDULE_CACHE_MAX = 8


class ScheduleReader:
    """只读读取日程（中樞自产的优先，其次 life_scheduler 产），提供穿搭/当前活动。"""

    def __init__(self, base_dir: Path | None = None, schedule_time: str | None = None,
                 files: list[Path] | None = None):
        self.base = base_dir or scheduler_data_path()
        self.file = self.base / "schedule_data.json"
        self._files = list(files) if files else None
        self.schedule_time = schedule_time or "00:00"

    def _candidates(self) -> list[Path]:
        return self._files if self._files else [self.file]

    def _stamp(self, date_str: str) -> tuple:
        """文件戳：任何一个候选文件被改写都会让缓存失效。"""
        items: list[tuple] = []
        for path in self._candidates():
            try:
                st = path.stat()
                items.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                items.append((str(path), 0, 0))
        return (date_str, tuple(items))

    def _read_entry(self, date_str: str) -> dict:
        """按候选顺序取第一份「当天有内容」的原始条目（带文件戳缓存）。"""
        stamp = self._stamp(date_str)
        cached = _SCHEDULE_ENTRY_CACHE.get(stamp)
        if cached is not None:
            return cached
        from .safe_io import sync_read_json

        entry: dict = {}
        for path in self._candidates():
            raw = sync_read_json(path, None)
            candidate = raw.get(date_str) if isinstance(raw, dict) else None
            if isinstance(candidate, dict) and str(candidate.get("schedule") or ""):
                entry = candidate
                break
        if len(_SCHEDULE_ENTRY_CACHE) >= _SCHEDULE_CACHE_MAX:
            _SCHEDULE_ENTRY_CACHE.clear()
        _SCHEDULE_ENTRY_CACHE[stamp] = entry
        return entry

    def read(self, now=None) -> dict:
        """返回 {date, outfit, outfit_style, schedule, current_activity, time_desc}。

        按 candidates 顺序取第一份「当天有内容」的；都没有则返回空结构。
        读盘结果按文件戳缓存，但「此刻在做 / 时段」始终按传入的 now 现算。
        """
        date_str = _business_date(self.schedule_time, now)
        entry = self._read_entry(date_str)
        schedule = str(entry.get("schedule") or "")
        return {
            "date": date_str,
            "outfit": str(entry.get("outfit") or ""),
            "outfit_style": str(entry.get("outfit_style") or ""),
            "schedule": schedule,
            "current_activity": self._current_activity(schedule, now),
            "time_desc": self._time_desc((now or datetime.datetime.now()).hour),
        }

    @staticmethod
    def _time_desc(h: int) -> str:
        h = h % 24
        return ("深夜" if h < 6 else "清晨" if h < 9 else "上午" if h < 12 else
                "中午" if h < 14 else "下午" if h < 18 else "晚上" if h < 22 else "深夜")

    @staticmethod
    def _current_activity(schedule: str, now=None) -> str:
        items = []
        for m in _SCHEDULE_TIME_RE.finditer(schedule or ""):
            hour = int(m.group("hour"))
            minute = 30 if m.group("half") else int(m.group("minute") or m.group("minute_cn") or "0")
            text = (m.group("text") or "").strip()
            if text:
                items.append((hour * 60 + minute, text))
        if not items:
            return ""
        items.sort(key=lambda x: x[0])
        now = now or datetime.datetime.now()
        cur = now.hour * 60 + now.minute
        # 当前时刻明显早于今日第一条日程（超过 30 分钟），说明今日日程尚未开始
        # （典型场景：凌晨 00:00~06:00 还没到 10:30 起床），不要硬报第一条白天活动。
        if cur + 30 < items[0][0]:
            return ""
        result = items[0][1]
        for minute, text in items:
            if minute <= cur:
                result = text
            else:
                break
        return result


def read_schedule(schedule_time: str | None = "00:00", now=None) -> dict:
    """读取当天日程（中樞自产优先，其次 life_scheduler），供各 handler 直接用。"""
    return ScheduleReader(schedule_time=schedule_time, files=schedule_files()).read(now)
