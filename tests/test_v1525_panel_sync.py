"""v1.5.25 验收：WebUI 与配置同步。

面板有三层容易「落后于配置」：
1. LABELS —— 缺标签就会把原始键名（英文/下划线）直接显示给用户；
2. GROUP_OF / SECTION_OF —— 缺规则的新键会掉进「总览/通用」；
3. 下拉选项与状态块 —— 枚举键渲染成裸输入框、状态块里看不到新开关。

这里锁住 1 和 3（2 需要执行 JS，靠 `node` 审计脚本人工核对）。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.panel_config import build_config  # noqa: E402
from astrbot_plugin_proactive_life.panel_status import _outbound_block  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "pages" / "panel" / "app.js").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


class _Conf:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, patch):
        self._data.update(patch)


def _cfg(**overrides) -> _LiveConfig:
    return _LiveConfig(SimpleNamespace(_conf=_Conf(overrides)), DEFAULT_CONFIG)


def _labels() -> dict[str, str]:
    m = re.search(r"const LABELS = \{(.*?)\n  \};", APP_JS, re.S)
    assert m, "app.js 里找不到 LABELS"
    return dict(re.findall(r'"([^"]+)":\s*"([^"]*)"', m.group(1)))


def test_every_key_has_chinese_label():
    labels = _labels()
    missing = [k for k in SCHEMA if k not in labels]
    assert not missing, "面板缺中文标签（会显示原始键名）:\n" + "\n".join(missing)


def test_no_stale_labels():
    labels = _labels()
    stale = [k for k in labels if k not in SCHEMA]
    assert not stale, "面板 LABELS 里残留了已删除的键:\n" + "\n".join(stale)


def test_enum_keys_have_dropdown_options():
    plugin = SimpleNamespace(cfg=_cfg(), context=SimpleNamespace())
    data = asyncio.run(build_config(plugin))
    options = data.get("options") or {}
    for key in (
        "拟人_模型档位",
        "出站_清洗档位",
        "拟人_说话方式强度",
        "出站_孤闭合处理",
        "出站_未闭合处理",
    ):
        assert key in options, f"{key} 缺下拉选项"
        values = [o["value"] for o in options[key]]
        default = SCHEMA[key].get("default")
        assert default in values, f"{key} 的默认值 {default} 不在选项里"


def test_outbound_block_exposes_mode_and_history_guard():
    plugin = SimpleNamespace(cfg=_cfg(出站_清洗档位="full", 出站_历史防污染=False,
                                      出站_历史清洗阈值=5))
    block = _outbound_block(plugin)
    assert block["mode"] == "full"
    assert block["history_guard"] is False
    assert block["history_threshold"] == 5


def test_group_section_rules_cover_new_prefixes():
    """新功能键必须有分栏规则，否则会掉进总览的「通用」（这里锁关键前缀）。"""
    for prefix in (
        '"作息延迟_"',
        '"视觉_"',
        '"群聊白名单"',
        '"出站_历史"',
        '"拟人_闲聊限长"',
    ):
        assert prefix in APP_JS, f"GROUP_OF/SECTION_OF 缺少 {prefix} 的归类规则"
