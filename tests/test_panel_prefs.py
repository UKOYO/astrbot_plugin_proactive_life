"""面板 UI 偏好本地落盘测试。"""

from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_proactive_life import panel_prefs, web_api


def test_load_missing_returns_empty(tmp_path):
    plugin = SimpleNamespace(panel_prefs_file=tmp_path / "panel_prefs.json")
    assert panel_prefs.load_prefs(plugin) == {}


def test_save_and_reload_valid_prefs(tmp_path):
    plugin = SimpleNamespace(panel_prefs_file=tmp_path / "panel_prefs.json")
    saved = panel_prefs.save_prefs(
        {
            "zoom": 1.35,
            "theme": "dark",
            "mono": True,
            "tab": "outbound",
            "group_fold": True,
        },
        plugin,
    )
    assert saved == {
        "zoom": 1.35,
        "theme": "dark",
        "mono": True,
        "tab": "outbound",
        "group_fold": True,
    }
    assert panel_prefs.load_prefs(plugin) == saved


def test_incremental_merge_and_sanitization(tmp_path):
    plugin = SimpleNamespace(panel_prefs_file=tmp_path / "panel_prefs.json")
    panel_prefs.save_prefs({"zoom": 1.5, "theme": "light", "tab": "group"}, plugin)

    merged = panel_prefs.save_prefs(
        {
            "zoom": 9.99,       # 非法档位，应忽略
            "theme": "neon",    # 非法主题，应忽略
            "tab": "rhythm",    # 合法更新
            "unknown": "drop",  # 未知字段，应忽略
        },
        plugin,
    )
    assert merged == {"zoom": 1.5, "theme": "light", "tab": "rhythm"}
    assert panel_prefs.load_prefs(plugin) == merged


def test_corrupted_json_recovers_gracefully(tmp_path):
    prefs_file = tmp_path / "panel_prefs.json"
    prefs_file.write_text("{not valid json", encoding="utf-8")
    plugin = SimpleNamespace(panel_prefs_file=prefs_file)
    assert panel_prefs.load_prefs(plugin) == {}
    saved = panel_prefs.save_prefs({"zoom": 1.2}, plugin)
    assert saved == {"zoom": 1.2}


def test_web_api_exposes_prefs_handler(tmp_path):
    plugin = SimpleNamespace(panel_prefs_file=tmp_path / "panel_prefs.json")
    handlers = web_api.make_handlers(plugin)
    assert "prefs" in handlers
    _, methods, _ = handlers["prefs"]
    assert "GET" in methods and "POST" in methods
