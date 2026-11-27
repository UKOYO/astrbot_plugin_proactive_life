"""GIF 动图拆帧（融合自 astrbot_plugin_gif_vision_helper）。

为什么要它：QQ 传过来的动图到 AstrBot 手里经常只有一个本地临时文件（扩展名
可能还是 .jpg/.png），视觉模型只看到「第一帧」——表情包的动作、前后变化全丢了，
模型只能靠猜。这里按魔数认出真 GIF，等间距抽若干帧转成 JPEG：

- 第一帧**覆盖原临时文件路径**，兼容 AstrBot 原有调用逻辑（不新增第一张图）；
- 其余帧追加到 ``req.image_urls``，让视觉模型一次看到动作过程；
- 帧数按总帧数与文件体积自适应（默认最多 6 帧），并限制单帧最长边；
- 可选注入一句「这是动图抽帧」的提示，避免模型把多帧当成多张不同的图。

两个踩过的坑（照搬原实现的经验）：
1. **Pillow 解压炸弹误判**：GIF 是「大画布 + 每帧只存一小块」，Pillow 拿逻辑
   画布算像素，正常动图也可能被判成炸弹。这里先用二进制头读画布，按真实量级
   临时放宽阈值，用完立刻还原，绝不永久关闭安全检查。
2. **临时文件自清理**：自己生成的帧文件登记在册，按 TTL 过期清理、插件卸载时
   全清；不碰 AstrBot 自己的 temp 目录管理。
"""

from __future__ import annotations

import asyncio
import struct
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 动图拆帧"

# 单次请求最多处理几张 GIF：多图消息里连拆三张会让 image_urls 爆炸
_MAX_GIFS_PER_REQUEST = 2
# 体积硬上限：再大就直接放行原始请求，不值得为一张表情包解码几十 MB
_MAX_FILE_BYTES = 30 * 1024 * 1024
# 过期清理的最小间隔（秒）：原来每个请求都起一个清理任务，纯浪费
_CLEANUP_INTERVAL = 3600.0

# 不是本地文件路径的写法：这些直接跳过（远程 URL / base64 / data URI）
_NON_LOCAL_PREFIXES = ("http://", "https://", "base64://", "data:", "file://")


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _cfg_int(cfg: Any, key: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int(_cfg(cfg, key, default) or default)
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


class GifVision:
    """GIF → 多帧 JPEG 抽样 + 提示注入 + 临时文件管理。"""

    def __init__(self) -> None:
        # 仅记录本模块创建的附加帧文件，不碰 AstrBot 自己的 temp 管理
        self._temp_files: set[Path] = set()
        self._temp_cache: dict[Path, float] = {}
        self._lock = threading.Lock()
        # 转换期间会临时改 Pillow 的全局阈值，串行化避免并发互相踩
        self._convert_lock = threading.Lock()
        self._last_cleanup = 0.0
        # 重采样算法懒加载（不在插件 init 阶段就把 Pillow 拖进来）
        self._resample: Any = False

    def _get_resample(self) -> Any:
        """Pillow 的重采样常量；没装 Pillow 或版本太老时返回 None。"""
        if self._resample is not False:
            return self._resample
        try:
            from PIL import Image

            self._resample = getattr(
                getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC
            )
        except Exception:  # noqa: BLE001
            self._resample = None
        return self._resample

    # ---------------------------------------------------------------- 对外入口

    async def apply(self, req: Any, cfg: Any) -> bool:
        """按需把请求里的 GIF 拆成多帧；返回是否改动过请求。"""
        urls = list(getattr(req, "image_urls", None) or [])
        if not urls:
            return False
        self._maybe_cleanup_expired(cfg)
        max_frames = _cfg_int(cfg, "视觉_最大帧数", 6, 1, 12)
        max_side = _cfg_int(cfg, "视觉_单帧边长", 768, 256, 2048)
        try:
            new_urls, changed, total_frames = await asyncio.to_thread(
                self._convert_urls, urls, max_frames, max_side
            )
        except Exception as e:  # noqa: BLE001 - 拆帧失败必须放行原始请求
            logger.warning("%s | 拆帧失败，按原请求继续: %s", _LOG, e)
            return False
        if not changed:
            return False
        try:
            req.image_urls = new_urls
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 写回 image_urls 失败，按原请求继续: %s", _LOG, e)
            return False
        if bool(_cfg(cfg, "视觉_提示注入", True)):
            try:
                req.prompt = self._inject_preview_hint(
                    getattr(req, "prompt", None), total_frames
                )
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---------------------------------------------------------------- 临时文件

    def _register_temp_file(self, path: Path) -> None:
        now = time.time()
        with self._lock:
            self._temp_files.add(path)
            self._temp_cache[path] = now

    def cleanup_all(self) -> None:
        """插件卸载时调用：删掉本模块生成的全部帧文件。"""
        with self._lock:
            paths = list(self._temp_files)
        for p in paths:
            self._unlink(p)
            with self._lock:
                self._temp_files.discard(p)
                self._temp_cache.pop(p, None)

    def cleanup_expired(self, ttl_sec: float) -> None:
        """按 TTL 清理过期帧文件（在 to_thread 里跑，不阻塞事件循环）。"""
        now = time.time()
        with self._lock:
            items = list(self._temp_cache.items())
        for path, ts in items:
            if now - ts <= ttl_sec:
                continue
            self._unlink(path)
            with self._lock:
                self._temp_cache.pop(path, None)
                self._temp_files.discard(path)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | 清理临时帧失败 %s: %s", _LOG, path, e)

    def _maybe_cleanup_expired(self, cfg: Any) -> None:
        now = time.time()
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        ttl_hours = _cfg_int(cfg, "视觉_临时帧保留小时", 24, 1, 168)
        try:
            # 不阻塞当前请求：清理失败也无所谓，下次再清
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(self.cleanup_expired, ttl_hours * 3600.0)
            )
        except Exception:  # noqa: BLE001 - 没有事件循环时直接跳过
            pass

    # ---------------------------------------------------------------- 拆帧核心

    def _convert_urls(
        self, urls: list[Any], max_frames: int, max_side: int
    ) -> tuple[list[Any], bool, int]:
        """把 urls 里的本地 GIF 拆帧，返回 (新 urls, 是否有改动, 总帧数)。"""
        out: list[str] = []
        changed = False
        total_frames = 0
        gifs = 0
        for u in urls:
            if not isinstance(u, str) or not u:
                out.append(u)
                continue
            if u.lower().startswith(_NON_LOCAL_PREFIXES):
                out.append(u)
                continue
            path = Path(u)
            if gifs >= _MAX_GIFS_PER_REQUEST or not self._is_gif_file(path):
                out.append(u)
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    logger.debug("%s | GIF 体积过大，放行原图: %s", _LOG, path.name)
                    out.append(u)
                    continue
            except OSError:
                out.append(u)
                continue
            frames, count = self._convert_gif(path, max_side, max_frames)
            if not frames or count <= 0:
                out.append(u)
                continue
            out.extend(str(p) for p in frames)
            gifs += 1
            total_frames += count
            changed = True
        return out, changed, total_frames

    @staticmethod
    def _is_gif_file(path: Path) -> bool:
        """按魔数判断：扩展名是 .jpg 但内容是 GIF 也能认出来。"""
        try:
            with path.open("rb") as f:
                return f.read(6) in (b"GIF87a", b"GIF89a")
        except (OSError, ValueError):
            return False

    @staticmethod
    def _read_gif_canvas(path: Path) -> tuple[int, int, int] | None:
        """直接从 GIF 头读「画布尺寸 + 帧数」，不依赖 Pillow。"""
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if len(raw) < 10 or raw[:6] not in (b"GIF87a", b"GIF89a"):
            return None
        try:
            width = struct.unpack_from("<H", raw, 6)[0]
            height = struct.unpack_from("<H", raw, 8)[0]
        except struct.error:
            return None
        frames = raw.count(b"\x2c") or 1
        return width, height, frames

    @staticmethod
    def _bomb_threshold_for(real_pixels: int) -> int:
        """按真实画布像素算一个临时放宽的炸弹阈值（画布 × 3）。"""
        if real_pixels <= 0:
            return 50_000_000
        return max(real_pixels * 3, 50_000_000)

    @staticmethod
    def _decide_frame_count(total_frames: int, file_size: int, cap: int = 6) -> int:
        """按总帧数与体积决定抽几帧。"""
        cap = max(1, int(cap))
        if total_frames <= 1:
            return 1
        if total_frames <= 4:
            frames = min(3, total_frames)
        elif total_frames <= 8:
            frames = min(4, total_frames)
        elif total_frames <= 16:
            frames = min(5, total_frames)
        else:
            frames = min(6, total_frames)
        if file_size > 10 * 1024 * 1024:
            frames = max(3, frames - 2)
        elif file_size > 5 * 1024 * 1024:
            frames = max(3, frames - 1)
        return max(1, min(frames, total_frames, cap))

    @staticmethod
    def _sample_indices(n_frames: int, target: int) -> list[int]:
        """等间距抽样，必含首尾帧。"""
        if n_frames <= 0:
            return []
        if target <= 1 or n_frames <= target:
            return list(range(min(n_frames, target)))
        step = (n_frames - 1) / (target - 1)
        indices = sorted({int(round(i * step)) for i in range(target)})
        if not indices:
            return [0]
        indices[0] = 0
        indices[-1] = n_frames - 1
        return indices

    def _resize_frame(self, img: Any, max_side: int) -> Any:
        w, h = img.size
        longest = max(w, h)
        if longest <= max_side:
            return img
        scale = max_side / float(longest)
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        resample = self._get_resample()
        try:
            return img.resize(new_size, resample=resample) if resample else img.resize(new_size)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s | LANCZOS 缩放失败，用默认算法: %s", _LOG, e)
            return img.resize(new_size)

    def _convert_gif(
        self, path: Path, max_side: int, cap: int
    ) -> tuple[list[Path], int]:
        """把一张 GIF 抽帧成 JPEG；任何异常都返回 ([], 0) 让上层放行原图。"""
        try:
            from PIL import Image, UnidentifiedImageError
            from PIL.Image import DecompressionBombError, DecompressionBombWarning
        except Exception as e:  # noqa: BLE001 - 没装 Pillow 就静默放行
            logger.debug("%s | Pillow 不可用，跳过拆帧: %s", _LOG, e)
            return [], 0

        with self._convert_lock:
            saved_max = Image.MAX_IMAGE_PIXELS
            canvas = self._read_gif_canvas(path)
            if canvas is not None:
                w, h, _frames = canvas
                Image.MAX_IMAGE_PIXELS = self._bomb_threshold_for(max(1, w) * max(1, h))
            bomb_ctx = warnings.catch_warnings()
            bomb_ctx.__enter__()
            warnings.simplefilter("ignore", DecompressionBombWarning)
            try:
                try:
                    size = path.stat().st_size
                except OSError:
                    return [], 0
                try:
                    # 先把所有帧转进内存，**关闭源文件之后**再落盘。
                    # 第一帧要覆盖原路径；边读边写会把还开着的 GIF 写坏，
                    # 后续 seek 直接 EOFError（Windows 上必现）。
                    frames_rgb: list[tuple[int, Any]] = []
                    static_img = None
                    with Image.open(path) as im:
                        total_frames = getattr(im, "n_frames", 1)
                        if total_frames <= 1:
                            static_img = self._resize_frame(im.convert("RGB"), max_side)
                        else:
                            target = self._decide_frame_count(total_frames, size, cap)
                            indices = self._sample_indices(total_frames, target)
                            if not indices:
                                return [], 0
                            for frame_idx in indices:
                                try:
                                    im.seek(frame_idx)
                                    frames_rgb.append(
                                        (frame_idx, self._resize_frame(im.convert("RGB"), max_side))
                                    )
                                except (EOFError, UnidentifiedImageError):
                                    continue  # 标记帧数比实际可读帧多的 GIF

                    # 静态图：只保证是 JPEG（顺手压一下体积），不追加帧
                    if static_img is not None:
                        static_img.save(path, format="JPEG", quality=90)
                        return [path], 1

                    if not frames_rgb:
                        return [], 0
                    out_paths: list[Path] = []
                    for pos, (frame_idx, frame) in enumerate(frames_rgb):
                        out_path = path if pos == 0 else path.parent / f"{path.stem}_f{frame_idx}.jpg"
                        frame.save(out_path, format="JPEG", quality=90)
                        out_paths.append(out_path)
                        if out_path is not path:
                            self._register_temp_file(out_path)
                    return out_paths, len(out_paths)
                except DecompressionBombError as e:
                    logger.warning("%s | 确认为超大图，回退原图: %s", _LOG, e)
                    return [], 0
                except Exception as e:  # noqa: BLE001
                    logger.warning("%s | 拆帧异常，回退原图: %s", _LOG, e)
                    return [], 0
            finally:
                # 无论成败都还原，绝不留后门
                Image.MAX_IMAGE_PIXELS = saved_max
                bomb_ctx.__exit__(None, None, None)

    @staticmethod
    def _inject_preview_hint(original_prompt: Any, frame_count: int) -> str:
        """注入「这是动图抽帧」的提示，且幂等（不重复加）。"""
        if frame_count > 1:
            hint = (
                f"[系统提示] 本次对方发来的是 GIF 动图，已抽取 {frame_count} 帧静态图。"
                "请综合所有帧理解完整的动作与表情变化，不要只看第一张。"
            )
        else:
            hint = (
                "[系统提示] 本次对方发来的是 GIF 动图，只保留了第一帧，"
                "可能丢失动作信息，请结合上下文合理推断。"
            )
        text = str(original_prompt or "")
        if hint in text:
            return text
        return f"{hint}\n\n{text}" if text else hint
