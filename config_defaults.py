"""插件默认配置值：单一真源是 `_conf_schema.json`。

以前这里手抄了一份 178 行的 DEFAULT_CONFIG，`panel_config.EDITABLE` 又手抄了
一份类型表，再加上 `_conf_schema.json` 和 `pages/panel/app.js`，同一批键要在
四处对齐——加一个配置项漏改一处就是静默失效（面板写不进去 / 缺键回落成 None）。

现在只维护 `_conf_schema.json`：

- :data:`DEFAULT_CONFIG` 由 schema 的 ``default`` 字段生成；
- :func:`schema_types` 把 schema 的 ``type`` 映射成 Python 类型，供
  `panel_config.EDITABLE`（面板白名单）直接使用。

schema 读不到时不抛异常拖垮插件加载：记一条错误日志并按空表继续，
调用点自带的默认值仍能兜住。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).with_name("_conf_schema.json")

# AstrBot 配置 schema 类型 -> Python 类型（面板 _coerce 按这个转换）
SCHEMA_TYPE_MAP: dict[str, type] = {
    "int": int,
    "float": float,
    "bool": bool,
    "string": str,
    "text": str,
    "list": list,
    "template_list": list,
}


def load_schema(path: Path | None = None) -> dict[str, dict]:
    """读取 `_conf_schema.json`；失败时返回空表（插件仍可加载）。"""
    target = Path(path) if path else SCHEMA_FILE
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("schema 根节点不是对象")
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as exc:  # noqa: BLE001 - 缺文件/坏 JSON 都不该拖垮插件
        try:
            from astrbot.api import logger

            logger.error("proactive_life | 配置 schema 读取失败（%s）: %s", target, exc)
        except Exception:
            pass
        return {}


SCHEMA: dict[str, dict] = load_schema()


def schema_defaults(schema: dict[str, dict] | None = None) -> dict[str, Any]:
    """把 schema 的 default 字段抽成 {key: default} 映射。"""
    src = SCHEMA if schema is None else schema
    out: dict[str, Any] = {}
    for key, spec in src.items():
        if "default" in spec:
            out[key] = spec["default"]
        else:
            out[key] = None
    return out


def schema_types(schema: dict[str, dict] | None = None) -> dict[str, type]:
    """把 schema 的 type 字段映射成 {key: Python 类型}，用于面板白名单。"""
    src = SCHEMA if schema is None else schema
    out: dict[str, type] = {}
    for key, spec in src.items():
        raw = str(spec.get("type") or "").strip().lower()
        out[key] = SCHEMA_TYPE_MAP.get(raw, str)
    return out


DEFAULT_CONFIG: dict[str, Any] = schema_defaults(SCHEMA)
