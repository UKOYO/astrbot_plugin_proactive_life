"""v1.5.20 验收：同源相对导入漏修 + 代码围栏边界（~~~ 与行内反引号）。

背景：`reply_delay.py` 与 `token_guard.py` 都因为「文件在 modules/ 子包下却写
`from .common import ...`」而 ImportError，异常又被 except 吞掉 → 功能静默失效。
第一项做成常驻扫描测试，以后同类错误在 CI 就会红。
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules import token_guard  # noqa: E402
from astrbot_plugin_proactive_life.modules.bare_thinking import (  # noqa: E402
    strip_bare_thinking,
)
from astrbot_plugin_proactive_life.modules.think_filtering import (  # noqa: E402
    filter_reasoning_text,
)

PKG = "astrbot_plugin_proactive_life"
ROOT = pathlib.Path(__file__).resolve().parent.parent


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


# ---------------------------------------------------------------- 相对导入扫描


def test_all_relative_imports_resolve():
    """每个相对导入都必须能解析成真实模块（同一类 bug 的常驻闸门）。"""
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        if rel.parts and rel.parts[0] == "tests":
            continue
        module_name = PKG + "." + ".".join(rel.with_suffix("").parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # noqa: BLE001
            problems.append(f"{rel}: 语法错误 {exc}")
            continue
        pkg_parts = module_name.split(".")[:-1]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            up = node.level - 1
            if up > len(pkg_parts):
                problems.append(f"{rel}:{node.lineno}: 相对层级超出包深度")
                continue
            base = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
            target = ".".join(base + ([node.module] if node.module else []))
            try:
                importlib.import_module(target)
            except Exception as exc:  # noqa: BLE001
                problems.append(
                    f"{rel}:{node.lineno}: {'.' * node.level}{node.module} -> "
                    f"{target} ({type(exc).__name__})"
                )
    assert not problems, "相对导入无法解析:\n" + "\n".join(problems)


# ---------------------------------------------------------------- 闲聊限长


def test_casual_cap_applies_when_enabled():
    """开关打开时必须真的改到 provider 参数（以前因导入错位永远 False）。"""
    provider = SimpleNamespace(
        provider_config={"custom_extra_body": {"temperature": 0.8}},
        get_model=lambda: "gpt-4o",
    )
    plugin = SimpleNamespace(
        cfg=_cfg(拟人_闲聊限长启用=True),
        context=SimpleNamespace(get_using_provider=lambda umo=None: provider),
    )
    event = SimpleNamespace(message_str="在吗", unified_msg_origin="aiocqhttp:FriendMessage:1")
    req = SimpleNamespace(tool_calls_result=None, func_tool=None)

    token_guard._active.clear()
    try:
        assert token_guard.apply_casual_cap(plugin, event, req) is True
        body = provider.provider_config["custom_extra_body"]
        assert body["max_tokens"] == 160
        assert abs(body["frequency_penalty"] - 0.35) < 1e-9
        # 还原后不留痕迹
        assert token_guard.restore() is True
        assert provider.provider_config["custom_extra_body"] == {"temperature": 0.8}
    finally:
        token_guard._active.clear()


def test_casual_cap_skips_long_or_techy_messages():
    provider = SimpleNamespace(
        provider_config={"custom_extra_body": {}}, get_model=lambda: "gpt-4o"
    )
    plugin = SimpleNamespace(
        cfg=_cfg(拟人_闲聊限长启用=True),
        context=SimpleNamespace(get_using_provider=lambda umo=None: provider),
    )
    token_guard._active.clear()
    try:
        for text in ("帮我看看这段代码为什么报错", "这句话很长" * 10):
            event = SimpleNamespace(message_str=text, unified_msg_origin="x")
            req = SimpleNamespace(tool_calls_result=None, func_tool=None)
            assert token_guard.apply_casual_cap(plugin, event, req) is False, text
            assert provider.provider_config["custom_extra_body"] == {}
    finally:
        token_guard._active.clear()


# ---------------------------------------------------------------- 围栏边界


def test_tilde_fence_is_protected():
    text = "示例：\n~~~html\n<thinking>示例内容</thinking>\n~~~\n看完就懂。"
    cleaned, removed = filter_reasoning_text(text, ["thinking"])
    assert "<thinking>示例内容</thinking>" in cleaned
    assert removed == 0
    assert "<thinking>示例内容</thinking>" in strip_bare_thinking(text)


def test_inline_code_span_is_protected():
    # 行内代码里只写了开标签：不保护的话会被当成未闭合思考块，把后面正文全删掉
    text = "把 `<thinking>` 包起来再问，别直接丢进正文。"
    cleaned, removed = filter_reasoning_text(text, ["thinking"])
    assert cleaned == text
    assert removed == 0
    assert strip_bare_thinking(text) == text


def test_backtick_fence_still_protected():
    text = "看这段：\n```xml\n<thinking>示例</thinking>\n```\n结束。"
    cleaned, removed = filter_reasoning_text(text, ["thinking"])
    assert "<thinking>示例</thinking>" in cleaned
    assert removed == 0


def test_tilde_fence_inside_thinking_section_is_kept():
    """思考段里紧跟的 ~~~ 代码块不能被当分析腔吃掉（按行保护要认两种围栏）。"""
    text = (
        "我需要先分析\n"
        "~~~\n"
        "- 示例一\n"
        "- 示例二\n"
        "~~~\n"
        "这就是正文。"
    )
    out = strip_bare_thinking(text)
    assert "- 示例一" in out and "- 示例二" in out, out
    assert "这就是正文。" in out


def test_no_duplicate_module_constants():
    """CODE_FENCE 只应定义一次（重复赋值是重构留下的尾巴）。"""
    src = (ROOT / "modules" / "bare_thinking.py").read_text(encoding="utf-8")
    assert src.count("CODE_FENCE = CODE_SPAN_RE") == 1
