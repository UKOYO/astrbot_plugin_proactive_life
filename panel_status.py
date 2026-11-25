"""面板状态构建：从插件实例收集各类观测数据，返回 status 快照。

从 web_api.py 拆出，不改变任何行为。所有函数保持接受 plugin 实例的签名。
"""

from __future__ import annotations

import datetime
import time


from . import name_resolver
from .panel_common import _ts, _fmt, _left, _split_origin, _quiet_hours


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
        "identity": _identity_block(plugin),
    }

def _identity_block(plugin) -> dict:
    """角色身分当前生效值（含从 AstrBot 自动读取的人设预览）。"""
    try:
        obj = getattr(plugin, "identity", None)
        if obj is None:
            return {"available": False}
        persona = str(obj.persona_text or "")
        plugin_prompt = str(getattr(obj, "plugin_prompt", "") or "")
        return {
            "available": True,
            "bot_name": obj.bot_name,
            "user_name": obj.user_name,
            "aliases": list(obj.aliases),
            "life_bg": obj.life_bg,
            "interests": list(obj.interests),
            "persona_len": len(persona),
            "persona_preview": persona[:160],
            "persona_id": str(plugin.cfg.get("角色_AstrBot人设", "") or ""),
            "plugin_prompt_len": len(plugin_prompt),
            "plugin_prompt_preview": plugin_prompt[:160],
            "using_synth": bool(plugin_prompt),
            "auto": bool(plugin.cfg.get("角色_自动读取AstrBot人设", True)),
        }
    except Exception:
        return {"available": False}

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
