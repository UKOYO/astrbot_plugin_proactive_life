"""角色身分集中管理。

之前「唯笑 / 优可 / 大魔王 / 死宅 / Last Eden」这些角色特征硬编码在
private_handler / group_handler / worker_handler / main 的 prompt 与 sender_name 里，
别人装了这个插件会被强行套上原作者的角色。这里统一收口：

- 所有称呼、生活背景、兴趣词都从插件配置读（有中性默认值）；
- 人设文本自动从 AstrBot 自身的 persona 配置读取（不写死）；
- 提供 identity() 一次性拿到全部字段，避免各处再各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 中性默认值：不带任何特定角色/作品特征
DEFAULTS: dict[str, Any] = {
    "角色_Bot自称": "",
    "角色_对方称呼": "",
    "角色_备用称呼": [],
    "角色_生活背景": "",
    "角色_兴趣关键词": [],
    "角色_额外人设": "",
    "角色_自动读取AstrBot人设": True,
}


@dataclass
class Identity:
    bot_name: str = ""
    user_name: str = ""
    aliases: list[str] = field(default_factory=list)
    life_bg: str = ""
    interests: list[str] = field(default_factory=list)
    extra_persona: str = ""
    persona_text: str = ""
    plugin_prompt: str = ""   # 由模型按所选人设生成、专供本插件使用的提示词

    @property
    def all_names(self) -> list[str]:
        """Bot 的全部称呼（自称 + 备用称呼），用于「是不是在喊我」的判定。"""
        out = []
        for n in [self.bot_name, *self.aliases]:
            n = str(n or "").strip()
            if n and n not in out:
                out.append(n)
        return out

    def persona_block(self) -> str:
        """拼出注入 prompt 的人设文字。

        优先级：插件专用提示词 > 原人设；之后再接用户补充与生活背景。
        """
        parts = []
        if self.plugin_prompt:
            parts.append(self.plugin_prompt.strip())
        elif self.persona_text:
            parts.append(self.persona_text.strip())
        if self.extra_persona:
            parts.append(str(self.extra_persona).strip())
        if self.life_bg:
            parts.append(str(self.life_bg).strip())
        return "\n".join(p for p in parts if p)


def _cfg(cfg: Any, key: str, default=None):
    try:
        return cfg.get(key, default)
    except Exception:
        return default


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
    return []


async def read_astrbot_persona(context: Any) -> str:
    """从 AstrBot 自身的默认 persona 读取 prompt（不写死任何角色）。

    Personality 是 TypedDict，字段为 prompt / name。
    """
    if context is None:
        return ""
    try:
        mgr = getattr(context, "persona_manager", None)
        if mgr is None:
            return ""
        p = await mgr.get_default_persona_v3()
        if isinstance(p, dict):
            return str(p.get("prompt") or "").strip()
        return str(getattr(p, "prompt", "") or "").strip()
    except Exception:
        return ""


def from_config(cfg: Any, persona_text: str = "") -> Identity:
    """从插件配置构造 Identity。"""
    return Identity(
        bot_name=str(_cfg(cfg, "角色_Bot自称", "") or "").strip(),
        user_name=str(_cfg(cfg, "角色_对方称呼", "") or "").strip(),
        aliases=_as_list(_cfg(cfg, "角色_备用称呼", [])),
        life_bg=str(_cfg(cfg, "角色_生活背景", "") or "").strip(),
        interests=_as_list(_cfg(cfg, "角色_兴趣关键词", [])),
        extra_persona=str(_cfg(cfg, "角色_额外人设", "") or "").strip(),
        persona_text=persona_text,
        plugin_prompt=str(_cfg(cfg, "角色_插件专用提示词", "") or "").strip(),
    )


async def list_personas(context: Any) -> list[dict]:
    """列出 AstrBot 里所有可选人设（含「跟随默认」）。

    返回 [{value, label}]，value 为 persona_id（'' 表示跟随默认）。
    """
    out = [{"value": "", "label": "跟随 AstrBot 默认人设"}]
    if context is None:
        return out
    try:
        mgr = getattr(context, "persona_manager", None)
        if mgr is None:
            return out
        # 优先用 personas_v3（get_persona_v3_by_id 比对的就是这里的 name）
        v3 = getattr(mgr, "personas_v3", None) or []
        seen: set[str] = set()
        for p in v3:
            pid = str(
                (p.get("name") if isinstance(p, dict) else getattr(p, "name", "")) or ""
            ).strip()
            if pid and pid not in seen:
                seen.add(pid)
                out.append({"value": pid, "label": pid})
        # 再补上 get_all_personas 里有但 v3 没列出的
        personas = await mgr.get_all_personas()
        for p in personas or []:
            pid = str(
                getattr(p, "persona_id", None)
                or (p.get("persona_id") if isinstance(p, dict) else "")
                or ""
            ).strip()
            if pid and pid not in seen:
                seen.add(pid)
                out.append({"value": pid, "label": pid})
    except Exception:
        pass
    return out


async def read_persona_by_id(context: Any, persona_id: str) -> str:
    """按 id 读取指定人设的 prompt；id 为空则读默认人设。

    多重回退，兼容不同 AstrBot 版本：
    1. get_persona_v3_by_id（personas_v3[].name）
    2. 遍历 get_all_personas 按 persona_id 匹配，直接取 system_prompt
    3. personas_v3 里按 name 匹配
    4. get_default_persona_v3
    """
    if context is None:
        return ""
    mgr = None
    try:
        mgr = getattr(context, "persona_manager", None)
        if mgr is None:
            return ""
        pid = str(persona_id or "").strip()
        picked = None

        if pid:
            # 1) v3 by id
            try:
                fn = getattr(mgr, "get_persona_v3_by_id", None)
                if callable(fn):
                    picked = fn(pid)
                    if picked is not None:
                        # async/sync 兼容
                        if hasattr(picked, "__await__"):
                            picked = await picked
            except Exception:
                picked = None

            # 2) 直接遍历 Persona（system_prompt）
            if picked is None:
                try:
                    fn = getattr(mgr, "get_all_personas", None)
                    rows = fn() if callable(fn) else None
                    if rows is not None and hasattr(rows, "__await__"):
                        rows = await rows
                    for p in rows or []:
                        cur = getattr(p, "persona_id", None)
                        if isinstance(p, dict):
                            cur = p.get("persona_id")
                        if str(cur or "").strip() == pid:
                            picked = {"prompt": getattr(p, "system_prompt", "")
                                      if not isinstance(p, dict) else p.get("system_prompt", "")}
                            break
                except Exception:
                    pass

            # 3) personas_v3 by name
            if picked is None:
                for p in (getattr(mgr, "personas_v3", None) or []):
                    nm = p.get("name") if isinstance(p, dict) else getattr(p, "name", "")
                    if str(nm or "").strip() == pid:
                        picked = p
                        break
        else:
            # 空 id：默认人设
            try:
                fn = getattr(mgr, "get_default_persona_v3", None)
                if callable(fn):
                    picked = fn()
                    if picked is not None and hasattr(picked, "__await__"):
                        picked = await picked
            except Exception:
                picked = None

        def _text_of(obj) -> str:
            if obj is None:
                return ""
            if isinstance(obj, dict):
                return str(obj.get("prompt") or obj.get("system_prompt") or "").strip()
            return str(
                getattr(obj, "prompt", "") or getattr(obj, "system_prompt", "") or ""
            ).strip()

        # 选中人设读不到内容时，回退到默认人设
        if not _text_of(picked) and pid:
            try:
                fn = getattr(mgr, "get_default_persona_v3", None)
                if callable(fn):
                    fallback = fn()
                    if fallback is not None and hasattr(fallback, "__await__"):
                        fallback = await fallback
                    if _text_of(fallback):
                        picked = fallback
            except Exception:
                pass

        return _text_of(picked)
    except Exception as e:
        try:
            from astrbot.api import logger
            logger.warning("proactive_life | 读取人设 %r 失败: %s", persona_id, e)
        except Exception:
            pass
        return ""


PERSONA_SYNTH_PROMPT = """你是「生活中枢提示词设计师」。用户选定了一份角色人设，请你把它改写成一份专供「自主生活与主动中枢」插件使用的设定。

这个插件会做这些事：主动私聊找人、追问没答完的话题、群里破冰与接话、空闲时自主上网摸鱼、生成每日穿搭与日程。它需要知道：这个角色平时怎么说话、什么语气、喜欢什么、生活状态如何、和对方是什么关系。

要求：
1. 只输出一个 JSON 对象本体，不要 Markdown 代码块、不要解释、不要多余文字；
2. 保留原人设里明确的性格、语气、称呼、习惯与关系设定，不要编造原人设没有的事实；
3. prompt 字段：用第二人称「你」描述这个角色，覆盖说话风格、兴趣话题、日常作息、与对方的关系距离，400 字以内，短句为主；
4. 其余字段从人设里抽取；原文没提到的给空字符串或空数组，不要编造：
   - bot_name：这个角色的自称/名字
   - user_name：它称呼对方的方式
   - aliases：别人可能用来叫它的别名/绰号数组
   - life_bg：日常作息、常待的地方、生活习惯（一两句）
   - interests：它感兴趣、愿意聊的话题关键词数组
   - extra_persona：其他需要补充的人设要点

输出格式（严格 JSON）：
{{
  "prompt": "...",
  "bot_name": "...",
  "user_name": "...",
  "aliases": ["..."],
  "life_bg": "...",
  "interests": ["..."],
  "extra_persona": "..."
}}

【用户选定的人设】
{persona}

【用户补充要求（可选）】
{extra}
"""


async def synthesize_plugin_prompt(
    context: Any, persona_text: str, extra: str, model_id: str = ""
) -> tuple[dict | None, str]:
    """用指定模型把人设改写成插件专用设定（提示词 + 结构化字段）。

    返回 (结果字典, 错误信息)；成功时错误信息为空。
    结果字典字段：prompt / bot_name / user_name / aliases / life_bg /
    interests / extra_persona。兼容模型只返回纯文本的情况。
    """
    if not persona_text.strip():
        return None, "没有可改写的人设文本"
    try:
        from .common import get_chat_provider, get_provider_by_id

        prov = get_provider_by_id(context, model_id) if model_id else None
        if prov is None:
            prov = get_chat_provider(context)
        if prov is None:
            return None, "没有可用的聊天模型"

        prompt = PERSONA_SYNTH_PROMPT.format(
            persona=persona_text.strip(), extra=str(extra or "").strip() or "（无）"
        )
        resp = await prov.text_chat(prompt=prompt, system_prompt="")
        text = ""
        for key in ("completion_text", "completion", "text", "content"):
            v = getattr(resp, key, None)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        if not text:
            return None, "模型返回为空"

        # 解析 JSON（复用日程模块的稳健解析器）
        try:
            from .schedule_gen import extract_json_obj

            data = extract_json_obj(text)
        except Exception:
            data = None
        if isinstance(data, dict) and str(data.get("prompt") or "").strip():
            def _arr(v):
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()][:12]
                if isinstance(v, str) and v.strip():
                    return [x.strip() for x in v.replace("，", ",").split(",") if x.strip()][:12]
                return []
            return {
                "prompt": str(data.get("prompt") or "").strip()[:2000],
                "bot_name": str(data.get("bot_name") or "").strip()[:40],
                "user_name": str(data.get("user_name") or "").strip()[:40],
                "aliases": _arr(data.get("aliases")),
                "life_bg": str(data.get("life_bg") or "").strip()[:500],
                "interests": _arr(data.get("interests")),
                "extra_persona": str(data.get("extra_persona") or "").strip()[:500],
            }, ""

        # 模型没按 JSON 输出：退化为纯文本，仍写入 prompt
        return {"prompt": text[:2000], "bot_name": "", "user_name": "",
                "aliases": [], "life_bg": "", "interests": [], "extra_persona": ""}, ""
    except Exception as e:
        return None, str(e)[:160]


async def resolve(context: Any, cfg: Any) -> Identity:
    """一次性解析：配置 + 按所选 persona 读取 AstrBot 人设 + 插件专用提示词。"""
    ident = from_config(cfg, "")
    if bool(_cfg(cfg, "角色_自动读取AstrBot人设", True)):
        ident.persona_text = await read_persona_by_id(
            context, str(_cfg(cfg, "角色_AstrBot人设", "") or "")
        )
    ident.plugin_prompt = str(_cfg(cfg, "角色_插件专用提示词", "") or "").strip()
    return ident
