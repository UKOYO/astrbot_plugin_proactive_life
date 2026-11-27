"""v1.5.15 验收：GIF 动图拆帧（融合自 astrbot_plugin_gif_vision_helper）。

覆盖：
1. 按魔数识别 GIF（扩展名是 .jpg 也能认出来），普通 JPEG 不碰；
2. 抽帧数量按总帧数/体积自适应，并受 `视觉_最大帧数` 限制；
3. 抽样索引必含首尾、去重、有序；
4. 端到端：只保留第一帧覆盖原路径，其余帧追加进 image_urls 并注入提示；
5. 非本地/非 GIF 的 url 原样放行；
6. `模块_视觉` 关掉时钩子不介入；
7. 临时帧文件可按 TTL 清理。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from PIL import Image  # noqa: E402

from astrbot_plugin_proactive_life.config_defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_proactive_life.main import ProactiveLifePlugin, _LiveConfig  # noqa: E402
from astrbot_plugin_proactive_life.modules.gif_vision import GifVision  # noqa: E402


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


def _make_gif(path: Path, frames: int = 5) -> Path:
    """造一张真实的多帧 GIF。"""
    colors = [(0, 0, 0), (127, 127, 127), (255, 255, 255), (90, 40, 200), (20, 200, 90)]
    imgs = [Image.new("RGB", (32, 24), colors[i % len(colors)]) for i in range(frames)]
    imgs[0].save(
        path, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0
    )
    return path


def _make_jpg(path: Path) -> Path:
    Image.new("RGB", (16, 16), (10, 20, 30)).save(path, format="JPEG")
    return path


# ---------------------------------------------------------------- 识别与策略


def test_is_gif_by_magic_bytes(tmp_path):
    gif = _make_gif(tmp_path / "anim.jpg")  # 扩展名故意写成 .jpg
    jpg = _make_jpg(tmp_path / "plain.jpg")
    gv = GifVision()
    assert gv._is_gif_file(gif) is True
    assert gv._is_gif_file(jpg) is False
    assert gv._is_gif_file(tmp_path / "missing.gif") is False


def test_decide_frame_count_buckets_and_cap():
    gv = GifVision()
    assert gv._decide_frame_count(1, 1000) == 1
    assert gv._decide_frame_count(3, 1000) == 3
    assert gv._decide_frame_count(6, 1000) == 4
    assert gv._decide_frame_count(12, 1000) == 5
    assert gv._decide_frame_count(40, 1000) == 6
    # 大文件减帧：40 帧先取 6，再因 >10MB 减 2
    assert gv._decide_frame_count(40, 11 * 1024 * 1024) == 4
    # 上限收紧
    assert gv._decide_frame_count(40, 1000, cap=2) == 2


def test_sample_indices_include_ends():
    gv = GifVision()
    assert gv._sample_indices(10, 4)[0] == 0
    assert gv._sample_indices(10, 4)[-1] == 9
    assert gv._sample_indices(10, 4) == sorted(set(gv._sample_indices(10, 4)))
    assert gv._sample_indices(3, 6) == [0, 1, 2]
    assert gv._sample_indices(0, 4) == []


# ---------------------------------------------------------------- 端到端


def _run_apply(gif: GifVision, req, cfg):
    return asyncio.run(gif.apply(req, cfg))


def test_apply_splits_gif_into_frames(tmp_path):
    gif_path = _make_gif(tmp_path / "sticker.jpg")
    req = SimpleNamespace(image_urls=[str(gif_path)], prompt="你看这个")
    gv = GifVision()

    assert _run_apply(gv, req, _cfg()) is True

    urls = req.image_urls
    # 5 帧 → 抽 4 帧：第一帧覆盖原路径，其余 3 帧是新文件
    assert len(urls) == 4
    assert urls[0] == str(gif_path)
    assert all(Path(u).exists() for u in urls)
    # 覆盖后的第一帧已经是 JPEG（不再是 GIF 魔数）
    assert not gv._is_gif_file(Path(urls[0]))
    # 提示注入
    assert "[系统提示]" in req.prompt and "GIF" in req.prompt
    assert req.prompt.endswith("你看这个")


def test_apply_respects_max_frames_and_hint_switch(tmp_path):
    gif_path = _make_gif(tmp_path / "a.gif", frames=8)
    req = SimpleNamespace(image_urls=[str(gif_path)], prompt="原话")
    gv = GifVision()

    assert _run_apply(gv, req, _cfg(视觉_最大帧数=2, 视觉_提示注入=False)) is True
    assert len(req.image_urls) == 2
    assert req.prompt == "原话"


def test_apply_leaves_non_gif_untouched(tmp_path):
    jpg = _make_jpg(tmp_path / "photo.jpg")
    req = SimpleNamespace(image_urls=[str(jpg)], prompt="图")
    gv = GifVision()
    assert _run_apply(gv, req, _cfg()) is False
    assert req.image_urls == [str(jpg)]
    assert req.prompt == "图"


def test_apply_skips_remote_and_data_urls():
    req = SimpleNamespace(
        image_urls=["https://example.com/a.gif", "base64://AAAA", "data:image/gif;base64,BBBB"],
        prompt="p",
    )
    gv = GifVision()
    assert _run_apply(gv, req, _cfg()) is False
    assert len(req.image_urls) == 3


def test_apply_keeps_other_images_after_frames(tmp_path):
    gif_path = _make_gif(tmp_path / "b.gif", frames=4)
    jpg = _make_jpg(tmp_path / "c.jpg")
    req = SimpleNamespace(image_urls=[str(gif_path), str(jpg)], prompt="")
    gv = GifVision()
    assert _run_apply(gv, req, _cfg()) is True
    assert str(jpg) in req.image_urls
    assert req.image_urls.index(str(jpg)) > 0


def test_temp_frames_can_be_expired(tmp_path):
    gif_path = _make_gif(tmp_path / "d.gif", frames=6)
    req = SimpleNamespace(image_urls=[str(gif_path)], prompt="")
    gv = GifVision()
    _run_apply(gv, req, _cfg())
    extras = [Path(u) for u in req.image_urls[1:]]
    assert extras and all(p.exists() for p in extras)

    gv.cleanup_expired(0)
    assert not any(p.exists() for p in extras)
    # 原文件（第一帧）不属于本模块生成的临时帧，不会被删
    assert gif_path.exists()


# ---------------------------------------------------------------- 钩子接线


def test_hook_respects_module_switch(tmp_path):
    gif_path = _make_gif(tmp_path / "e.gif", frames=5)

    def run(enabled: bool):
        plugin = object.__new__(ProactiveLifePlugin)
        plugin._stop_event = asyncio.Event()
        plugin.cfg = _cfg(模块_视觉=enabled)
        plugin._module_on = lambda name: bool(plugin.cfg.get(f"模块_{name}", True))
        plugin.gif_vision = GifVision()
        req = SimpleNamespace(image_urls=[str(gif_path)], prompt="看图")
        asyncio.run(
            ProactiveLifePlugin.on_gif_vision_llm_request(
                plugin, SimpleNamespace(unified_msg_origin="x"), req
            )
        )
        return req

    assert len(run(False).image_urls) == 1
    # 重新造一份 GIF：上一轮可能已经把原文件覆盖成 JPEG
    gif_path = _make_gif(tmp_path / "f.gif", frames=5)
    result = run(True)
    assert len(result.image_urls) > 1
