"""面板 UI 偏好本地持久化。

AstrBot 插件页面运行在受限 iframe 中，浏览器 localStorage 可能因沙箱或源变化被清空；
把缩放比例、主题、等宽、当前分区和群聊折叠状态落盘到插件数据目录下的 panel_prefs.json，
重载插件或重新打开页面时仍能保持用户设定的界面状态。
"""

from __future__ import annotations

import json
import os
import tempfile
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
    path = _prefs_path(plugin)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _sanitize(data)


def save_prefs(patch: dict[str, Any], plugin: Any = None) -> dict[str, Any]:
    current = load_prefs(plugin)
    cleaned = _sanitize(patch)
    if not cleaned:
        return current

    merged = {**current, **cleaned}
    path = _prefs_path(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=".panel_prefs_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return merged
