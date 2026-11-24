# -*- coding: utf-8 -*-
"""把 unified_msg_origin 翻成人能认的名字。

面板上光一串 QQ 号没法认，这里去 AstrBot 自己的 umo_aliases 表里翻
auto_name / user_alias。查不到就返回空，前端回落成 ID。
"""
import glob
import os
import re
import sqlite3
import time
from typing import Dict, Iterable

_CACHE: Dict[str, str] = {}
_CACHE_AT = 0.0
_TTL = 300.0


def _db_path() -> str:
    roots = []
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        roots.append(str(get_astrbot_data_path()))
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(os.path.abspath(os.path.join(here, "..", "..")))
    roots.append(os.path.abspath(os.path.join(here, "..", "..", "..")))

    def ver(p: str) -> int:
        m = re.search(r"data_v(\d+)\.db$", p)
        return int(m.group(1)) if m else 0

    for base in roots:
        cands = glob.glob(os.path.join(base, "data_v*.db"))
        if cands:
            cands.sort(key=ver)
            return cands[-1]
    return ""


def _key(umo: str) -> str:
    """平台前缀会变，只认最后两段：类型:ID。"""
    parts = str(umo).split(":")
    return ":".join(parts[-2:]) if len(parts) >= 3 else str(umo)


def _load_all() -> Dict[str, str]:
    path = _db_path()
    if not path:
        return {}
    try:
        conn = sqlite3.connect(
            "file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=2.0
        )
    except Exception:
        return {}
    try:
        rows = conn.execute("select umo, auto_name, user_alias from umo_aliases").fetchall()
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out: Dict[str, str] = {}
    for umo, auto, alias in rows:
        name = str(alias or auto or "").strip()
        if name:
            out[_key(umo)] = name
    return out


def resolve(umos: Iterable[str]) -> Dict[str, str]:
    """返回 {传入的 umo: 显示名}，查不到的键不会出现在结果里。"""
    global _CACHE, _CACHE_AT
    wanted = [str(u) for u in umos if u]
    if not wanted:
        return {}
    now = time.time()
    if now - _CACHE_AT > _TTL:
        _CACHE = _load_all()
        _CACHE_AT = now
    out: Dict[str, str] = {}
    for u in wanted:
        name = _CACHE.get(_key(u))
        if name:
            out[u] = name
    return out
