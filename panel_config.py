"""面板配置：读取/写入配置、构造下拉选项、类型强制转换。

从 web_api.py 拆出，不改变任何行为。EDITABLE 白名单现在由
`config_defaults.schema_types()` 从 `_conf_schema.json` 自动生成，
不再手抄一份 178 行的键名+类型表。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from . import name_resolver
from .config_defaults import schema_types



async def _persona_options(plugin) -> list[dict]:
    """列出 AstrBot 的全部人设（供面板下拉）。"""
    try:
        from .identity import list_personas

        return await list_personas(getattr(plugin, "context", None))
    except Exception:
        return [{"value": "", "label": "跟随 AstrBot 默认人设"}]

# 面板上允许修改的配置项 -> 期望类型。不在表里的配置项一律拒绝。
# 单一真源是 `_conf_schema.json`：类型表由它自动生成，不再手抄 178 行，
# 否则加一个配置项漏改一处就会「面板写不进去 / 被静默丢弃」。
EDITABLE: dict[str, type] = schema_types()


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
    TIER_OPTIONS = [
        {"value": "auto", "label": "自动（跟随模型）"},
        {"value": "full", "label": "完整（XML 场景提示）"},
        {"value": "lite", "label": "精简（拍平场景，省 Token）"},
    ]
    POLISH_MODE_OPTIONS = [
        {"value": "minimal", "label": "止损（只扫格式 + 整句废话）"},
        {"value": "full", "label": "完整（允许换词/删词）"},
    ]
    SPEECH_STRENGTH_OPTIONS = [
        {"value": "gentle", "label": "温和（只压客服/百科腔，不限句式）"},
        {"value": "strict", "label": "严格（额外限制短句、不分点）"},
    ]
    ORPHAN_TAG_OPTIONS = [
        {"value": "remove_prefix", "label": "删掉标签前的内容"},
        {"value": "remove_tag", "label": "只删标签本身"},
        {"value": "remove_suffix", "label": "删掉标签后的内容"},
    ]
    models = _model_options(plugin)
    options = {
        "群聊插话_模式": MODE_OPTIONS,
        "节奏_处理方式": RHYTHM_MODE_OPTIONS,
        "拟人_模型档位": TIER_OPTIONS,
        "出站_清洗档位": POLISH_MODE_OPTIONS,
        "拟人_说话方式强度": SPEECH_STRENGTH_OPTIONS,
        "出站_孤闭合处理": ORPHAN_TAG_OPTIONS,
        "出站_未闭合处理": ORPHAN_TAG_OPTIONS,
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

    # 立刻把新参数推进运行态（上下文窗口/冷却/节律/身份），
    # 否则要等到下一条群消息或下一轮协调循环才生效。
    for hook in ("_sync_runtime_state", "_sync_identity"):
        fn = getattr(plugin, hook, None)
        if callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - 同步失败不该让写入失败
                logger.debug(f"proactive_life | 配置写入后 {hook} 失败: {exc}")

    return {
        "ok": True,
        "changed": {key: plugin.cfg.get(key) for key in changed},
        "rejected": rejected,
        "saved": saved,
    }
