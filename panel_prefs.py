"""面板 UI 偏好本地持久化。

AstrBot 插件页面运行在受限 iframe 中，浏览器 localStorage 可能因沙箱或源变化被清空；
把缩放比例、主题、等宽、当前分区和群聊折叠状态落盘到插件数据目录下的 panel_prefs.json，
重载插件或重新打开页面时仍能保持用户设定的界面状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import hub_data_path

PREFS_FILENAME = "panel_prefs.json"

ALLOWED_ZOOMS: tuple[float, ...] = (1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0)
ALLOWED_THEMES: frozenset[str] = frozenset({"light", "dark"})
ALLOWED_TABS: frozenset[str] = frozenset({
    "overview",
    "private",
    "group",
    "slang",
    "interact",
    "worker",
    "rhythm",
    "outbound",
    "schedule",
    "debounce",
    "identity",
    "context",
    "blacklist",
})


def _prefs_path(plugin: Any = None) -> Path:
    custom = getattr(plugin, "panel_prefs_file", None) if plugin is not None else None
    if custom:
        return Path(custom)
    return hub_data_path() / PREFS_FILENAME


def _sanitize(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}

    if "zoom" in raw:
        try:
            z = float(raw["zoom"])
            matched = next((s for s in ALLOWED_ZOOMS if abs(s - z) < 0.001), None)
            if matched is not None:
                out["zoom"] = matched
        except (TypeError, ValueError):
            pass

    if "theme" in raw and isinstance(raw["theme"], str):
        t = raw["theme"].strip().lower()
        if t in ALLOWED_THEMES:
            out["theme"] = t

    if "mono" in raw and isinstance(raw["mono"], bool):
        out["mono"] = raw["mono"]

    if "tab" in raw and isinstance(raw["tab"], str):
        tab = raw["tab"].strip()
        if tab in ALLOWED_TABS:
            out["tab"] = tab

    if "group_fold" in raw and isinstance(raw["group_fold"], bool):
        out["group_fold"] = raw["group_fold"]

    return out


def load_prefs(plugin: Any = None) -> dict[str, Any]:
    """读取面板偏好；文件缺失或损坏时返回空表。"""
    from .safe_io import sync_read_json

    return _sanitize(sync_read_json(_prefs_path(plugin), {}))


def save_prefs(patch: dict[str, Any], plugin: Any = None) -> dict[str, Any]:
    """合并并原子落盘；写入失败抛 OSError（由接口层转成 500）。"""
    current = load_prefs(plugin)
    cleaned = _sanitize(patch)
    if not cleaned:
        return current

    merged = {**current, **cleaned}
    path = _prefs_path(plugin)
    # 原子写统一走 safe_io，不再自己维护一份临时文件 + replace 逻辑
    from .safe_io import sync_write_json

    if not sync_write_json(path, merged, log_prefix="proactive_life | 面板偏好落盘失败"):
        raise OSError(f"写入 {path} 失败")
    return merged
