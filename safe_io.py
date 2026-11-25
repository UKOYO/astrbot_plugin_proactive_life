"""统一的文件 IO 收口。

目的：
- 同步 API 保持给 __init__ / 同步 handler 用；
- async 路径统一用 ``async_read_json`` / ``async_write_json`` 放线程池，
  避免小文件 IO 阻塞事件循环；
- 读写失败可选择性记录日志，避免 "静默吞掉"；
- 原子写（临时文件 + replace），避免写到一半被读到脏数据。

不引入新依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from astrbot.api import logger


def sync_read_json(path: Path | str, default=None, *, log_prefix: str = "") -> Any:
    """同步读取 JSON；失败返回 default 并记一条 warning。"""
    p = Path(path)
    if not p.exists():
        return default
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError) as e:
        if log_prefix:
            logger.warning("%s | 读取 %s 失败: %s", log_prefix, p.name, e)
        return default


def sync_write_json(path: Path | str, data: Any, *, log_prefix: str = "") -> bool:
    """同步原子写 JSON；成功 True，失败 False 并记日志。"""
    p = Path(path)
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)
        return True
    except (OSError, TypeError, ValueError) as e:
        if log_prefix:
            logger.warning("%s | 写入 %s 失败: %s", log_prefix, p.name, e)
        return False
    finally:
        if tmp is not None and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


async def async_read_json(path: Path | str, default=None, *, log_prefix: str = "") -> Any:
    """线程池版同步读取，供 async 路径使用。"""
    return await asyncio.to_thread(sync_read_json, path, default, log_prefix=log_prefix)


async def async_write_json(path: Path | str, data: Any, *, log_prefix: str = "") -> bool:
    """线程池版原子写，供 async 路径使用。"""
    return await asyncio.to_thread(sync_write_json, path, data, log_prefix=log_prefix)
