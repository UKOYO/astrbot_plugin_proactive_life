"""面板动作：处理前端的手动触发请求。

从 web_api.py 拆出，不改变任何行为。
"""

from __future__ import annotations

import datetime

from astrbot.api import logger

from .panel_status import _identity_block, _schedule_block
from .panel_config import apply_config


async def do_action(plugin, payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "message": "请求体不是 JSON 对象"}
    action = str(payload.get("action", "") or "").strip()

    if action == "persona_reload":
        await plugin._load_persona()
        return {"ok": True, "message": "已重新载入所选人设", "identity": _identity_block(plugin)}

    if action == "persona_synthesize":
        extra = str(payload.get("extra", "") or "").strip()
        ident = getattr(plugin, "identity", None)
        source = (ident.persona_text if ident else "") or ""
        if not source.strip():
            # 快取为空时即时重读一次（用户可能刚在下拉里换了人设）
            await plugin._load_persona()
            ident = getattr(plugin, "identity", None)
            source = (ident.persona_text if ident else "") or ""
        if not source.strip():
            return {"ok": False, "message": "没读到所选人设的内容。请确认：1)「自动读取 AstrBot 人设」已勾选；2) 下拉选的人设确实有提示词正文；3) 点一次「重新载入人设」后看日志里的「人设读取」字数"}
        from .identity import synthesize_plugin_prompt

        model_id = str(plugin.cfg.get("角色_插件提示词模型", "") or "")
        result, err = await synthesize_plugin_prompt(
            plugin.context, source, extra, model_id
        )
        if err or not result:
            return {"ok": False, "message": f"生成失败：{err or '空结果'}"}
        try:
            patch = {
                "角色_插件专用提示词": result.get("prompt", ""),
                "角色_Bot自称": result.get("bot_name", ""),
                "角色_对方称呼": result.get("user_name", ""),
                "角色_备用称呼": result.get("aliases", []),
                "角色_生活背景": result.get("life_bg", ""),
                "角色_兴趣关键词": result.get("interests", []),
                "角色_额外人设": result.get("extra_persona", ""),
            }
            ok = apply_config(plugin, patch)
            if not ok.get("ok"):
                return {"ok": False, "message": "生成成功但写入配置失败：" + str(ok.get("message", ""))}
        except Exception as e:
            return {"ok": False, "message": f"生成成功但写入失败：{e}"}
        plugin._sync_identity()
        n = len(result.get("prompt", ""))
        return {
            "ok": True,
            "message": (
                f"已生成并保存（提示词 {n} 字，另含自称/称呼/别名/背景/兴趣等字段）"
            ),
            "identity": _identity_block(plugin),
        }

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
