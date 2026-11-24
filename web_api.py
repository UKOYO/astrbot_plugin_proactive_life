"""自主生活与主动中枢 · WebUI 后端接口。

面板只做观测与轻量调节，真正的调度逻辑仍然全在 main.py 的协调中枢里。
这里所有函数都接受插件实例，不持有自己的状态，热重载时不会留下残留。
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response
from astrbot.api.web import request as web_request

from . import name_resolver

PLUGIN_NAME = "astrbot_plugin_proactive_life"
API_PREFIX = f"/{PLUGIN_NAME}/panel"

# 面板上允许修改的配置项 -> 期望类型。不在表里的配置项一律拒绝，
# 避免前端手滑把整个配置结构写坏。
EDITABLE: dict[str, type] = {
    # 模块级总开关（放在最前，保证每个分栏里它是第一项）
    "模块_私聊": bool,
    "模块_群聊": bool,
    "模块_摸鱼": bool,
    "模块_节奏": bool,
    "模块_日程": bool,
    "模块_上下文": bool,
    "模块_防抖": bool,
    # 中枢
    "全局调度间隔": int,
    "免打扰时段": list,
    # 私聊
    "私聊主动_启用": bool,
    "私聊追问_启用": bool,
    "对谁主动": list,
    "最短间隔": int,
    "每天最多几次": int,
    "触发概率": float,
    "安静多久才找": int,
    "追问等待秒数": int,
    "追问过期秒数": int,
    "追问最多次数": int,
    "参考几条": int,
    "参考日程": bool,
    "日程生成时间": str,
    "日程_自动生成": bool,
    "日程_参考历史天数": int,
    "日程_参考最近条数": int,
    "日程_生成模型": str,
    "日程_穿搭风格池": list,
    "日程_提示词模板": str,
    # 消息防抖
    "防抖_群聊启用": bool,
    "防抖_私聊启用": bool,
    "防抖_群聊等待秒": float,
    "防抖_群聊最长等待秒": float,
    "防抖_私聊等待秒": float,
    "防抖_私聊最长等待秒": float,
    "防抖_单次图片上限": int,
    "防抖_情绪拆分": bool,
    "防抖_过滤表情包": bool,
    "防抖_锁超时秒": float,
    "参考最近聊天": bool,
    # 群聊
    "群聊自发破冰_启用": bool,
    "群聊冷场判定时间": int,
    "群聊自发破冰概率": float,
    "群聊插话_启用": bool,
    "群聊插话_模式": str,
    "群聊插话_概率": float,
    "群聊插话_冷却时间": float,
    "群聊接话_最短间隔": float,
    "群聊插话_饥饿保底条数": int,
    "群聊插话_保底最短间隔": int,
    "群聊插话_判定窗口": float,
    "群聊插话_闭嘴静默分钟": float,
    "群聊插话_嫌烦静默分钟": float,
    "群聊插话_诊断日志": bool,
    "群聊白名单": list,
    "群聊白名单_概率倍数": float,
    "群聊独立分区配置": list,
    "群聊判定模型": str,
    "场景感知_启用": bool,
    "场景感知_让位contextaware": bool,
    "场景感知_对话窗口": int,
    "场景感知_显示对话流": bool,
    "场景感知_严格模式": bool,
    "场景感知_回复特征词": list,
    "场景感知_调试推断": bool,
    "场景感知_总结启用": bool,
    "场景感知_总结每N条": int,
    "场景感知_总结间隔秒": int,
    "场景感知_总结条数": int,
    "场景感知_总结模型": str,
    "拟人_话多程度": int,
    "拟人_沉默倾向": int,
    "拟人_吐槽强度": int,
    "拟人_成员印象": bool,
    "拟人_情绪启用": bool,
    "拟人_情绪衰减小时": float,
    "拟人_动机多样化": bool,
    # 摸鱼
    "后台摸鱼_启用": bool,
    "摸鱼干活最短间隔": int,
    "摸鱼干活概率": float,
    "摸鱼_搜索方向": str,
    "摸鱼_搜索方向_模式": str,
    "优先消费心事动机": bool,
    # 节奏
    "节奏_启用": bool,
    "节奏_处理方式": str,
    "节奏_每段字数": int,
    "节奏_最多几条": int,
    "节奏_间隔秒数": float,
    "节奏_去句末标点": bool,
    "节奏_去颜文字": bool,
    "节奏_表格代码不拆": bool,
    "节奏_花括号不拆": bool,
    "节奏_打印处理详情": bool,
    "节奏_只对群聊": bool,
    "节奏_演练模式": bool,
    "模拟分段打字": bool,
    "分段发送延迟": float,
}


def _ts(value: Any) -> float | None:
    try:
        if not value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str | None:
    ts = _ts(value)
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _left(until_ts: float | None, now: float) -> int:
    if not until_ts:
        return 0
    return max(0, int(round(until_ts - now)))


def _split_origin(origin: str) -> tuple[str, str]:
    """origin 形如 platform_id:message_type:session_id，取平台与末段。"""
    parts = str(origin or "").split(":")
    if len(parts) >= 3:
        return parts[0], parts[2]
    return "", str(origin or "")


def _quiet_hours(cfg: dict) -> list[int]:
    """免打扰时段（统一走 common 的解析，避免多份实现漂移）。"""
    from .common import parse_quiet_hours

    return parse_quiet_hours(cfg.get("免打扰时段"))


# ---------------------------------------------------------------- status


def _mood_snapshot(plugin, key: str) -> dict:
    """取某个会话/对象的当前情绪，供面板展示。"""
    store = getattr(plugin, "mood", None)
    if store is None:
        return {}
    try:
        return store.snapshot(str(key))
    except Exception:
        return {}


def _private_block(plugin, now: float, now_dt: datetime.datetime) -> dict:
    cfg = plugin.cfg
    state = plugin.private_h.state
    today = now_dt.strftime("%Y-%m-%d")

    targets = [str(x) for x in (cfg.get("对谁主动") or [])]
    gap = int(cfg.get("最短间隔", 180) or 0)
    cap = int(cfg.get("每天最多几次", 3) or 0)
    quiet_min = int(cfg.get("安静多久才找", 60) or 0)
    wait_sec = int(cfg.get("追问等待秒数", 300) or 0)
    expire_sec = int(cfg.get("追问过期秒数", 1800) or 0)
    max_nudge = int(cfg.get("追问最多次数", 1) or 0)

    pnames = name_resolver.resolve(["FriendMessage:" + u for u in targets])
    users = []
    for uid in targets:
        last_user = _ts(state.get("last_user_msg", {}).get(uid))
        last_active = _ts(state.get("last_active", {}).get(uid))
        daily = state.get("daily", {}).get(uid) or {}
        count = int(daily.get("count", 0) or 0) if daily.get("date") == today else 0

        pending_raw = state.get("pending_inquiries", {}).get(uid) or {}
        pending = None
        if pending_raw:
            asked_at = _ts(pending_raw.get("asked_at")) or 0.0
            nudged = int(pending_raw.get("nudged_count", 0) or 0)
            pending = {
                "question": str(pending_raw.get("question", "") or "")[:120],
                "asked_at": _fmt(asked_at),
                "nudged_count": nudged,
                "max_nudge": max_nudge,
                "due_in": _left(asked_at + wait_sec, now),
                "expire_in": _left(asked_at + expire_sec, now),
            }

        # 三项独立门槛里离“能开口”最远的那一个，就是实际倒计时
        silence_left = _left(last_user + quiet_min * 60, now) if last_user else 0
        gap_left = _left(last_active + gap, now) if last_active else 0
        blockers = []
        if silence_left > 0:
            blockers.append({"kind": "silence", "label": "还需安静", "left": silence_left})
        if gap_left > 0:
            blockers.append({"kind": "gap", "label": "间隔冷却", "left": gap_left})
        if cap and count >= cap:
            blockers.append(
                {"kind": "cap", "label": "今日已满", "left": 0, "text": f"{count}/{cap}"}
            )

        users.append(
            {
                "uid": uid,
                "name": pnames.get("FriendMessage:" + uid, ""),
                "last_user_msg": _fmt(last_user),
                "last_active": _fmt(last_active),
                "daily_count": count,
                "daily_cap": cap,
                "silence_left": silence_left,
                "gap_left": gap_left,
                "pending": pending,
                "blockers": blockers,
                "ready": not blockers,
                "mood": _mood_snapshot(plugin, uid),
            }
        )

    return {
        "enabled": bool(cfg.get("私聊主动_启用", True)),
        "nudge_enabled": bool(cfg.get("私聊追问_启用", True)),
        "probability": float(cfg.get("触发概率", 0.55) or 0),
        "users": users,
    }


def _group_block(plugin, now: float) -> dict:
    cfg = plugin.cfg
    gh = plugin.group_h

    cooldown = float(cfg.get("群聊插话_冷却时间", 45.0) or 0)
    origins: set[str] = set()
    for bucket in (
        gh._active_stamp,
        gh._last_spoke_stamp,
        gh._last_roll_stamp,
        gh._muted_until,
        gh._miss_streak,
        gh._recent_bot_spoke,
        gh.recent_context_cache,
    ):
        origins.update(str(k) for k in bucket.keys())

    names = name_resolver.resolve(origins)
    groups = []
    for origin in sorted(origins):
        platform_id, session_id = _split_origin(origin)
        spoke_at = _ts(gh._last_spoke_stamp.get(origin))
        muted_until = _ts(gh._muted_until.get(origin))
        active_at = _ts(gh._active_stamp.get(origin))
        recent = gh._recent_bot_spoke.get(origin) or {}
        groups.append(
            {
                "origin": origin,
                "platform": platform_id,
                "session": session_id,
                "name": names.get(origin, ""),
                "last_spoke": _fmt(spoke_at) if spoke_at else None,
                "cooldown_left": _left(spoke_at + cooldown, now) if spoke_at else None,
                "muted_left": _left(muted_until, now) if muted_until else 0,
                "last_active": _fmt(active_at) if active_at else None,
                "active_ago": int(max(0, now - active_at)) if active_at else None,
                "miss_streak": int(gh._miss_streak.get(origin, 0) or 0),
                "cache_size": len(gh.recent_context_cache.get(origin) or []),
                "recent_text": str(recent.get("text", "") or "")[:60],
                "recent_turns": int(recent.get("turns", 0) or 0),
            }
        )

    return {
        "icebreak_enabled": bool(cfg.get("群聊自发破冰_启用", True)),
        "icebreak_silence": int(cfg.get("群聊冷场判定时间", 120) or 0),
        "icebreak_probability": float(cfg.get("群聊自发破冰概率", 0.35) or 0),
        "interject_enabled": bool(cfg.get("群聊插话_启用", False)),
        "interject_mode": str(cfg.get("群聊插话_模式", "probability") or "probability"),
        "interject_probability": float(cfg.get("群聊插话_概率", 0.05) or 0),
        "cooldown": cooldown,
        "whitelist": [str(x) for x in (cfg.get("群聊白名单") or [])],
        "groups": groups,
    }


def _rhythm_block(plugin) -> dict:
    last = dict(getattr(plugin, "_rhythm_last", {}) or {})
    return {
        "enabled": bool(plugin._rhythm_on("节奏_启用")),
        "mode": str(plugin._rhythm_get("节奏_处理方式", "split")),
        "target": plugin._rhythm_target(),
        "max_segments": plugin._rhythm_num("节奏_最多几条"),
        "gap": float(plugin._rhythm_get("节奏_间隔秒数", 0.2) or 0),
        "strip_punct": bool(plugin._rhythm_on("节奏_去句末标点")),
        "strip_faces": bool(plugin._rhythm_on("节奏_去颜文字")),
        "group_only": bool(plugin._rhythm_on("节奏_只对群聊")),
        "dry_run": bool(plugin._rhythm_on("节奏_演练模式")),
        "last": {
            "before": int(last.get("before", 0) or 0),
            "segments": int(last.get("segments", 0) or 0),
            "lens": list(last.get("lens") or []),
            "hits": list(last.get("hits") or []),
            "mode": str(last.get("处理方式", "") or ""),
        }
        if last
        else None,
    }


def build_status(plugin) -> dict:
    now = time.time()
    now_dt = datetime.datetime.now()
    cfg = plugin.cfg

    interval_min = max(3, int(cfg.get("全局调度间隔", 10) or 10))
    last_tick = _ts(getattr(plugin, "_last_macro_tick", 0.0)) or 0.0
    next_tick_at = (last_tick + interval_min * 60) if last_tick else now
    task = getattr(plugin, "_main_loop_task", None)
    running = bool(task) and not task.done()

    hours = _quiet_hours(cfg)
    in_quiet = now_dt.hour in hours

    return {
        "status": "ok",
        "now": _fmt(now),
        "loop": {
            "running": running,
            "interval_min": interval_min,
            "last_tick": _fmt(last_tick),
            "next_tick_in": _left(next_tick_at, now),
            "quiet_hours": hours,
            "in_quiet": in_quiet,
            "quiet_left_hours": _quiet_left(hours, now_dt),
        },
        "private": _private_block(plugin, now, now_dt),
        "group": _group_block(plugin, now),
        "worker": {
            "enabled": bool(cfg.get("后台摸鱼_启用", True)),
            "min_interval": int(cfg.get("摸鱼干活最短间隔", 240) or 0),
            "probability": float(cfg.get("摸鱼干活概率", 0.4) or 0),
        },
        "rhythm": _rhythm_block(plugin),
        "motive": _motive_block(plugin),
        "context": _context_block(plugin),
        "activity": _activity_block(plugin),
        "schedule": _schedule_block(cfg),
        "conflicts": _conflicts_block(plugin),
        "debounce": _debounce_block(plugin),
    }


def _debounce_block(plugin) -> dict:
    """防抖运行状态。"""
    try:
        st = plugin.debounce.stats()
    except Exception:
        return {}
    st["enabled"] = bool(plugin._module_on("防抖"))
    try:
        st["group_on"] = bool(plugin.cfg.get("防抖_群聊启用", True))
        st["private_on"] = bool(plugin.cfg.get("防抖_私聊启用", True))
    except Exception:
        st["group_on"] = st["private_on"] = True
    return st


def _conflicts_block(plugin) -> list[dict]:
    """检测与其它插件的功能重叠，提示用户二选一，避免双重作用。"""
    out: list[dict] = []
    ctx = getattr(plugin, "context", None)
    cfg = plugin.cfg

    def _star_active(name: str) -> bool:
        try:
            meta = ctx.get_registered_star(name)
            return bool(meta and getattr(meta, "activated", False))
        except Exception:
            return False

    try:
        if (
            _star_active("astrbot_plugin_life_scheduler")
            and plugin._module_on("日程")
            and bool(cfg.get("日程_自动生成", True))
        ):
            out.append({
                "level": "warn",
                "text": "life_scheduler 仍在启用，两边都会生成日程。建议停用其一（本插件生成结果优先被读取）。",
            })
        if (
            _star_active("astrbot_plugin_smart_debounce")
            and plugin._module_on("防抖")
        ):
            out.append({
                "level": "warn",
                "text": "smart_debounce 仍在启用，两边都会合并消息（可能重复防抖）。建议停用其一。",
            })
        if (
            _star_active("astrbot_plugin_context_aware")
            and plugin._module_on("上下文")
            and not bool(cfg.get("场景感知_让位contextaware", True))
        ):
            out.append({
                "level": "warn",
                "text": "context_aware 与本插件都会注入场景提示，且「让位」已关闭。建议开启让位或停用其一。",
            })
    except Exception:
        pass
    return out


def _schedule_block(cfg) -> dict:
    """今日日程（中樞自产优先，其次 life_scheduler 产）。"""
    try:
        from .common import read_schedule
        st = str(cfg.get("日程生成时间", "07:00") or "07:00")
        info = read_schedule(st)
        return {
            "date": info.get("date", ""),
            "outfit": info.get("outfit", ""),
            "outfit_style": info.get("outfit_style", ""),
            "current_activity": info.get("current_activity", ""),
            "time_desc": info.get("time_desc", ""),
            "schedule": info.get("schedule", ""),
            "available": bool(info.get("schedule")),
            "auto": bool(cfg.get("日程_自动生成", True)),
        }
    except Exception:
        return {"available": False}


def _activity_block(plugin) -> list[dict]:
    """最近活动日志（最新在前），供总览展示。"""
    items = list(getattr(plugin, "_activity", []) or [])
    out: list[dict] = []
    for it in reversed(items[-20:]):
        try:
            out.append({
                "at": _fmt(it.get("at")),
                "kind": str(it.get("kind", "") or ""),
                "text": str(it.get("text", "") or ""),
                "origin": str(it.get("origin", "") or ""),
            })
        except Exception:
            continue
    return out


def _context_block(plugin) -> dict:
    """上下文/场景感知状态：让面板看到每次说话实际注入了什么。"""
    gh = getattr(plugin, "group_h", None)
    cfg = plugin.cfg
    enabled = bool(cfg.get("场景感知_启用", True))
    tracker = getattr(gh, "_scene", None)
    last_scene = getattr(gh, "_last_scene", {}) or {}

    # 汇总各会话的追踪窗口
    sessions = 0
    total_msgs = 0
    if tracker is not None:
        wins = getattr(tracker, "_sessions", {}) or {}
        sessions = len(wins)
        total_msgs = sum(len(w) for w in wins.values())

    # 最近一次注入的快照（按时间取最新）
    latest = None
    if last_scene:
        origin, snap = max(last_scene.items(), key=lambda kv: kv[1].get("at", 0))
        try:
            plat, sess = _split_origin(origin)
        except Exception:
            plat, sess = "", origin
        latest = {
            "at": _fmt(snap.get("at")),
            "group": sess,
            "trigger": snap.get("trigger", ""),
            "talking_to": snap.get("talking_to", ""),
            "chars": int(snap.get("chars", 0) or 0),
            "flow_count": int(snap.get("flow_count", 0) or 0),
            "preview": str(snap.get("text", ""))[:900],
        }

    return {
        "enabled": enabled,
        "sessions": sessions,
        "tracked_messages": total_msgs,
        "inject_window": int(cfg.get("场景感知_对话窗口", 8) or 8),
        "cache_cap": int(getattr(getattr(gh, "_scene", None), "_max", 40) or 40),
        "latest": latest,
        "summary": _summary_block(gh, cfg),
        "members": _members_block(gh, cfg),
    }


def _members_block(gh, cfg) -> dict:
    """成员印象状态：面板展示最近活跃群里已知的成员画像。"""
    store = getattr(gh, "_members", None)
    enabled = bool(cfg.get("拟人_成员印象", True))
    if store is None:
        return {"enabled": enabled, "sessions": 0, "latest": []}
    sessions = store.sessions()
    # 取最近有活动的会话的成员画像（按最后发言时间排序，最多 8 人）
    latest: list[dict] = []
    touched = getattr(store, "_touched", {}) or {}
    if touched:
        origin = max(touched, key=lambda k: touched.get(k, 0))
        try:
            _, sess = _split_origin(origin)
        except Exception:
            sess = origin
        profiles = store.members(origin)
        for pid, p in sorted(
            profiles.items(), key=lambda kv: kv[1].last_seen, reverse=True
        )[:8]:
            latest.append({
                "group": sess,
                "name": p.name or pid,
                "messages": p.messages,
                "to_bot": p.to_bot,
                "impression": p.render(),
            })
    return {"enabled": enabled, "sessions": sessions, "latest": latest}


def _summary_block(gh, cfg) -> dict:
    """短期总结状态：让面板看到最近沉淀的笔记。"""
    store = getattr(gh, "_summary", None)
    enabled = bool(cfg.get("场景感知_总结启用", True))
    if store is None:
        return {"enabled": enabled, "sessions": 0, "latest": None}
    # 取最近更新的一版笔记
    touched = getattr(store, "_touched", {}) or {}
    latest = None
    if touched:
        origin = max(touched, key=lambda k: touched.get(k, 0))
        note = store.get(origin)
        if note:
            try:
                _, sess = _split_origin(origin)
            except Exception:
                sess = origin
            latest = {
                "at": _fmt(note.at),
                "group": sess,
                "constraints": list(note.constraints),
                "corrections": list(note.corrections),
                "decisions": list(note.decisions),
                "todos": list(note.todos),
            }
    return {
        "enabled": enabled,
        "sessions": store.count(),
        "every_n": int(cfg.get("场景感知_总结每N条", 12) or 12),
        "interval_sec": int(cfg.get("场景感知_总结间隔秒", 180) or 180),
        "latest": latest,
    }


def _quiet_left(hours: list[int], now_dt: datetime.datetime) -> int:
    """免打扰时段是离散小时列表，算出还要多久能走出这片静默。"""
    if now_dt.hour not in hours:
        return 0
    if not hours:
        return 0
    hour_set = set(hours)
    remain = 0
    cursor = now_dt
    while cursor.hour in hour_set and remain < 24:
        remain += 1
        cursor = cursor + datetime.timedelta(hours=1)
    # 当前这一小时已经走过多少，剩下的折进去
    minutes_left_in_hour = 60 - now_dt.minute
    return max(0, (remain - 1) * 60 + minutes_left_in_hour)


def _motive_block(plugin) -> dict:
    try:
        data = plugin.motive_mgr._load_data()
        pool = list(data.get("motives", []) or [])
    except Exception:
        pool = []

    now = time.time()
    items = []
    for item in pool[-20:]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        created = _ts(item.get("created_at")) or 0.0
        age_h = int((now - created) / 3600) if created else None
        items.append(
            {
                "id": str(item.get("id", "") or ""),
                "text": content[:100],
                "scope": str(item.get("scope", "") or ""),
                "private_used": bool(item.get("private_used")),
                "used_groups": len(item.get("used_groups") or []),
                "age_hours": age_h,
            }
        )
    return {"count": len(pool), "items": items}


# ---------------------------------------------------------------- config


def _model_options(plugin) -> list[dict]:
    """只列 LLM（聊天）模型，供判定模型下拉使用。

    AstrBot 4.x 的 Context 没有 provider_manager，改用
    context.get_all_providers()（其实现返回的正是聊天类实例）。
    回传 [{value, label}]，value 是写进配置的 provider id。
    """
    from .common import list_chat_providers

    insts = list_chat_providers(getattr(plugin, "context", None))
    out: list[dict] = []
    for inst in insts:
        try:
            meta = inst.meta()
            iid = str(getattr(meta, "id", "") or "").strip()
            model = str(getattr(meta, "model", "") or "").strip()
        except Exception:
            continue
        if not iid:
            continue
        out.append({"value": iid, "label": f"{model or iid}（{iid}）"})
    out.sort(key=lambda x: x["value"])
    return out


def build_config(plugin) -> dict:
    cfg = plugin.cfg
    # 節奏項走 _rhythm_get，才能反映舊設定（無「節奏_」前綴）的有效值；
    # 其餘設定維持原本的活視圖讀取。
    values = {
        key: (plugin._rhythm_get(key) if key.startswith("节奏_") and hasattr(plugin, "_rhythm_get")
              else cfg.get(key))
        for key in EDITABLE
    }
    # 群专属分区补上群名，供面板显示（_coerce 提交时会丢弃这个展示字段）
    partitions = values.get("群聊独立分区配置") or []
    if isinstance(partitions, list) and partitions:
        origins = [f"小组:GroupMessage:{str(r.get('group_id', '')).strip()}"
                   for r in partitions if isinstance(r, dict)]
        names = name_resolver.resolve(origins)
        enriched = []
        for r in partitions:
            if not isinstance(r, dict):
                enriched.append(r)
                continue
            gid = str(r.get("group_id", "") or "").strip()
            item = dict(r)
            item["group_name"] = names.get(f"小组:GroupMessage:{gid}", "")
            enriched.append(item)
        values["群聊独立分区配置"] = enriched
    schema = [
        {"key": key, "type": typ.__name__} for key, typ in EDITABLE.items()
    ]
    # 特定字段的下拉选项：模式固定中文，模型从运行时读取
    MODE_OPTIONS = [
        {"value": "probability", "label": "概率插话"},
        {"value": "model_choice", "label": "模型判定"},
    ]
    RHYTHM_MODE_OPTIONS = [
        {"value": "split", "label": "拆条发送"},
        {"value": "punctuation", "label": "仅去标点"},
        {"value": "trim", "label": "截断长文"},
    ]
    DIRECTION_MODE_OPTIONS = [
        {"value": "replace", "label": "只搜我写的"},
        {"value": "append", "label": "我写的优先，兼顾默认"},
    ]
    models = _model_options(plugin)
    options = {
        "群聊插话_模式": MODE_OPTIONS,
        "节奏_处理方式": RHYTHM_MODE_OPTIONS,
        "群聊判定模型": models,
        "场景感知_总结模型": models,
        "日程_生成模型": models,
        "摸鱼_搜索方向_模式": DIRECTION_MODE_OPTIONS,
    }
    return {"status": "ok", "values": values, "schema": schema, "options": options}


def _coerce(key: str, value: Any, typ: type) -> Any:
    if typ is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是", "开")
    if typ is int:
        return int(value)
    if typ is float:
        return float(value)
    if typ is list:
        # 群聊独立分区配置是 list[dict]，要保留结构并做字段级转换，
        # 不能当成逗号分隔的字符串处理，否则后端 _get_group_custom_param 读不到。
        if key == "群聊独立分区配置":
            raw_items = value if isinstance(value, list) else []
            out = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                gid = str(item.get("group_id", "") or "").strip()
                if not gid:
                    continue
                row = {"group_id": gid}
                for f, cast in (("icebreak_prob", float), ("chitchat_prob", float), ("idle_minutes", int)):
                    raw_v = item.get(f)
                    if raw_v is None or raw_v == "":
                        continue
                    try:
                        row[f] = cast(raw_v)
                    except (TypeError, ValueError):
                        continue
                out.append(row)
            return out
        raw_items = value if isinstance(value, list) else str(value or "").replace("，", ",").split(",")
        out = []
        for item in raw_items:
            token = str(item).strip()
            if not token:
                continue
            if key == "免打扰时段":
                try:
                    hour = int(token)
                except ValueError:
                    continue
                if 0 <= hour <= 23:
                    out.append(hour)
            else:
                out.append(token)
        return out
    return str(value)


def apply_config(plugin, patch: dict) -> dict:
    if not isinstance(patch, dict) or not patch:
        return {"ok": False, "message": "没有要改的项"}

    changed: dict[str, Any] = {}
    rejected: list[str] = []
    for key, raw in patch.items():
        typ = EDITABLE.get(key)
        if typ is None:
            rejected.append(key)
            continue
        try:
            changed[key] = _coerce(key, raw, typ)
        except (TypeError, ValueError):
            rejected.append(key)

    if not changed:
        return {"ok": False, "message": "没有可写入的项", "rejected": rejected}

    # 面板改的值先落到 AstrBotConfig 上：plugin.cfg 是活视图，会立刻读到新值
    raw_conf = getattr(plugin, "_conf", None)
    saved = False
    if raw_conf is not None:
        try:
            raw_conf.update(changed)
            if hasattr(raw_conf, "save_config"):
                raw_conf.save_config()
            saved = True
        except Exception as exc:
            logger.warning(f"proactive_life | 配置落盘失败: {exc}")
    if not saved:
        plugin.cfg.update(changed)

    # handler 若还抱着旧快照，重新绑一次；有 update_config 的走它自己的钩子
    for handler in (
        getattr(plugin, "private_h", None),
        getattr(plugin, "group_h", None),
        getattr(plugin, "worker_h", None),
    ):
        if handler is None:
            continue
        if hasattr(handler, "update_config"):
            handler.update_config(plugin.cfg)
        elif getattr(handler, "cfg", None) is not plugin.cfg:
            handler.cfg = plugin.cfg

    return {
        "ok": True,
        "changed": {key: plugin.cfg.get(key) for key in changed},
        "rejected": rejected,
        "saved": saved,
    }


# ---------------------------------------------------------------- action


async def do_action(plugin, payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "message": "请求体不是 JSON 对象"}
    action = str(payload.get("action", "") or "").strip()

    if action == "schedule_generate":
        extra = str(payload.get("extra", "") or "").strip()
        try:
            data = await plugin.schedule_gen.generate(
                datetime.datetime.now(), umo=None, extra=extra or None
            )
        except Exception as exc:
            logger.warning("proactive_life | 手动生成日程失败: %s", exc)
            return {"ok": False, "message": "生成失败，详情见日志"}
        if data.status != "ok":
            why = (getattr(data, "reason", "") or "").strip()
            return {"ok": False, "message": f"生成失败：{why}" if why else "生成失败，详情见日志"}
        return {
            "ok": True,
            "message": f"已生成 {data.date} 的日程（风格：{data.outfit_style or '—'}）",
            "schedule": _schedule_block(plugin.cfg),
        }

    if action == "schedule_save":
        day = str(payload.get("date", "") or "").strip() or datetime.datetime.now().strftime("%Y-%m-%d")
        outfit = str(payload.get("outfit", "") or "")
        sched = str(payload.get("schedule", "") or "")
        style = str(payload.get("outfit_style", "") or "")
        if not sched.strip():
            return {"ok": False, "message": "日程不能为空"}
        try:
            from .schedule_gen import ScheduleData

            plugin.schedule_store.set(
                ScheduleData(date=day, outfit_style=style, outfit=outfit,
                             schedule=sched, status="ok")
            )
        except Exception as exc:
            logger.warning("proactive_life | 保存日程失败: %s", exc)
            return {"ok": False, "message": "保存失败，详情见日志"}
        return {"ok": True, "message": f"已保存 {day} 的日程", "schedule": _schedule_block(plugin.cfg)}

    return {"ok": False, "message": f"未知动作: {action or '(空)'}"}


# ---------------------------------------------------------------- handlers


def make_handlers(plugin):
    """把接口函数绑到插件实例上，返回 {route_suffix: (handler, methods, desc)}。"""

    async def api_status():
        try:
            return json_response(build_status(plugin))
        except Exception as exc:
            logger.warning(f"proactive_life | 面板状态读取失败: {exc}", exc_info=True)
            return error_response("状态读取失败", status_code=500)

    async def api_config():
        if web_request.method.upper() == "POST":
            payload = await web_request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体不是 JSON 对象", status_code=400)
            return json_response(apply_config(plugin, payload))
        try:
            return json_response(build_config(plugin))
        except Exception as exc:
            logger.warning(f"proactive_life | 面板配置读取失败: {exc}")
            return error_response("配置读取失败", status_code=500)

    async def api_action():
        try:
            payload = await web_request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体不是 JSON 对象", status_code=400)
            return json_response(await do_action(plugin, payload))
        except Exception as exc:
            logger.warning("proactive_life | 面板动作执行失败: %s", exc, exc_info=True)
            return error_response("动作执行失败", status_code=500)

    return {
        "status": (api_status, ["GET"], "主动中枢：运行状态快照"),
        "config": (api_config, ["GET", "POST"], "主动中枢：读取与写入配置"),
        "action": (api_action, ["POST"], "主动中枢：日程生成与保存"),
    }


def register(plugin) -> None:
    for suffix, (handler, methods, desc) in make_handlers(plugin).items():
        plugin.context.register_web_api(
            f"{API_PREFIX}/{suffix}", handler, methods, desc
        )
    logger.info("proactive_life | 面板接口已注册 %s", API_PREFIX)
