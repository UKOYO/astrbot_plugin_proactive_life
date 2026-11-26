"""面板配置：读取/写入配置、构造下拉选项、类型强制转换。

从 web_api.py 拆出，不改变任何行为。EDITABLE 白名单保留在 web_api.py，
通过模块级函数注入，避免两处重复定义。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from . import name_resolver



async def _persona_options(plugin) -> list[dict]:
    """列出 AstrBot 的全部人设（供面板下拉）。"""
    try:
        from .identity import list_personas

        return await list_personas(getattr(plugin, "context", None))
    except Exception:
        return [{"value": "", "label": "跟随 AstrBot 默认人设"}]

# 面板上允许修改的配置项 -> 期望类型。不在表里的配置项一律拒绝。
EDITABLE: dict[str, type] = {
    "拟人_跨会话记忆": bool,
    "拟人_说话方式约束": bool,
    "拟人_说话方式强度": str,
    "拟人_说话方式补充": str,
    "拟人_情绪落盘": bool,
    "拟人_情绪注入被动": bool,
    "心事_被动注入": bool,
    "生活状态_总字数上限": int,
    # 角色身分
    "角色_AstrBot人设": str,
    "角色_插件提示词模型": str,
    "角色_插件专用提示词": str,
    "角色_自动读取AstrBot人设": bool,
    "角色_Bot自称": str,
    "角色_对方称呼": str,
    "角色_备用称呼": list,
    "角色_生活背景": str,
    "角色_兴趣关键词": list,
    "角色_额外人设": str,
    # 模块级总开关（放在最前，保证每个分栏里它是第一项）
    "模块_私聊": bool,
    "模块_群聊": bool,
    "模块_摸鱼": bool,
    "模块_节奏": bool,
    "模块_日程": bool,
    "模块_上下文": bool,
    "模块_防抖": bool,
    "戳一戳_上下文条数": int,
    "戳一戳_上下文秒数": int,
    "戳一戳_后续合并": bool,
    "戳一戳_纯艾特回应": bool,
    "戳一戳_个人冷却秒": int,
    "戳一戳_群冷却秒": int,
    "戳一戳_反戳": bool,
    "戳一戳_回应概率": float,
    "戳一戳_纯艾特概率": float,
    "跟读_阈值": int,
    "跟读_最小间隔秒": int,
    "跟读_概率": float,
    "跟读_屏蔽词": list,
    "出站_思考标签": list,
    "出站_裸思考过滤": bool,
    "出站_孤闭合处理": str,
    "出站_未闭合处理": str,
    "出站_思考标签大小写敏感": bool,
    "出站_空回复兜底": str,
    "出站_修剪空白": bool,
    "出站_压缩空行": bool,
    "出站_八股_清AI八股": bool,
    "出站_八股_改句式": bool,
    "出站_八股_换词": bool,
    "出站_八股_删八股短语": bool,
    "出站_八股_激进删词": bool,
    "出站_八股_水词配额": int,
    "出站_八股_合并碎句": bool,
    "出站_八股_碎句阈值": int,
    "出站_八股_删结尾升华": bool,
    "出站_八股_保护细节块": bool,
    "出站_八股_保护代码表格": bool,
    "出站_八股_保护引号台词": bool,
    "出站_八股_禁用规则": list,
    "出站_八股_演练模式": bool,
    "模块_天气": bool,
    "天气_城市": str,
    "黑话_自动解析": bool,
    "黑话_解析模型": str,
    "模块_黑话": bool,
    "黑话_最小命中": int,
    "黑话_最多条数": int,
    "黑话_只注入已解析": bool,
    "模块_黑名单": bool,
    "模块_黑名单_ID": list,
    "模块_黑名单_批量文本": str,
    "模块_黑名单_拦主动": bool,
    "模块_黑名单_记日志": bool,

    "模块_撤回取消": bool,
    "模块_戳一戳": bool,
    "模块_跟读": bool,
    "模块_思考过滤": bool,
    "模块_八股清洗": bool,

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
    "日程_被动注入": bool,
    "日程_被动注入字数": int,
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
    "防抖_推理续租秒": int,
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
    "群聊命中词_启用": bool,
    "群聊命中词_关键词": list,
    "群聊命中词_接话概率": float,
    "群聊命中词_最短间隔秒": int,
    "群聊命中词_需包含称呼": bool,
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

async def build_config(plugin) -> dict:
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
        "角色_AstrBot人设": await _persona_options(plugin),
        "角色_插件提示词模型": models,
        "黑话_解析模型": models,
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
