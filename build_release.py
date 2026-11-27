"""打包发布用 zip（排除开发/运行态文件）。

为什么需要：直接把插件目录压成 zip 发布，会把 `.git/`、`__pycache__/`、
`.pytest_cache/` 以及插件运行时自己写的 `data/` 一起带上——轻则包体膨胀、
泄露本地开发记录，重则在用户机器上覆盖掉他已有的运行数据。

用法：
    python build_release.py            # 输出到 dist/
    python build_release.py --out D:/tmp

不依赖 git（未跟踪的新文件也能进包），只按下面的规则过滤。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "dist"

# 目录名黑名单：出现在路径任意一段就整棵剪掉
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", "node_modules", "dist", "data", ".idea", ".vscode",
}

# 文件名/后缀黑名单
EXCLUDE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".bak", ".zip", ".orig", ".rej",
}
EXCLUDE_NAMES = {
    ".DS_Store", "Thumbs.db", "desktop.ini",
    # 运行态文件：即使被误放进目录也不该发布
    "panel_prefs.json", "schedule_data.json", "mood_state.json",
    "motive_pool.json", "private_state.json", "person_memory.json",
    "mute_until.json", "slang_dict.json", "weather_cache.json",
}


def _version() -> str:
    """从 metadata.yaml 读版本号（读不到就用 unknown）。"""
    meta = ROOT / "metadata.yaml"
    try:
        text = meta.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    m = re.search(r"(?m)^version:\s*[\"']?([^\"'\s]+)", text)
    return m.group(1) if m else "unknown"


def _should_skip(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return path.name in EXCLUDE_NAMES


def iter_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip(path):
            continue
        yield path


def build(out_dir: Path) -> tuple[Path, int]:
    """打包并返回 (zip 路径, 文件数)。"""
    version = _version()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{ROOT.name}-v{version}.zip"
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in iter_files():
            # 统一放在「插件名/」顶层目录里，解压到 AstrBot plugins 目录即可用
            zf.write(path, Path(ROOT.name) / path.relative_to(ROOT))
            count += 1
    return target, count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包插件发布 zip")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    args = parser.parse_args(argv)
    target, count = build(Path(args.out))
    print(f"已打包 {count} 个文件 -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
