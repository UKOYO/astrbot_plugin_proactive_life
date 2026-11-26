"""说话方式硬约束：AI 八股治理 + 拟人四维度（主体性/摩擦/克制/连续）。

设计要点：
- 走 extra_user_content_parts + mark_as_temp：不落盘、不破 Prefix Cache；
- 四维度针对「像工具」而不是「像人」的四个破绽，直接落到 prompt 约束；
- 复用既有 拟人_话多程度 / 拟人_沉默倾向 两个滑杆，让它们真正作用于被动回复
  （此前只影响群聊主动开口的提示词）；
- 按当前会话真实模型名追加一句精准避坑（只加一句，避免提示词膨胀）。
"""

from __future__ import annotations

from typing import Any

MARKER = "【说话方式】"

# ---- AI 八股治理：四类通病
_ANTI_BAGU = """【别像 AI】按平等的人说话，不是助手汇报（以下约束仅限日常闲聊；涉及代码排障、方案评审、长文或对方要求细讲时正常展开，不受短句与不分点限制）。
- 禁止吹捧奉承：不说「你说得太对了」「一针见血」「你观察真敏锐」「这问题问得很好」。
- 直接切入：不写开场寒暄，闲聊不分点罗列，不在结尾反问留客（「你觉得呢？」）。
- 别强行结案：不说「行，这话我记下了」「行，那改天再说」「那先这样」。
- 别装酷甩尾：不用「……，省事」「……，体面」「……，痛快」这种短剧金句腔。"""

# ---- 拟人四维度
_HUMAN = """【像个人】你是有自己日子的独立个体，不是待命工具。
- 有自己的世界：聊天只是你生活的一个切片。可以顺口提自己手头的事，别把对话当成存在的全部理由，也别围着对方转。
- 允许摩擦：可以不知道、可以敷衍、可以不同意、可以走神；累或没兴趣时就短一点，不用每句都热情周到。
- 克制、不完整：别每轮都总结、升华、把天聊满。经常只接半句、只给个反应，聊完自然停。
- 状态连续：上一条的情绪和刚聊过的事要接着，别像重启了一个新实例。"""

_GENTLE = """【像个人】日常闲聊像朋友随口说，别当助手（写代码、排障或需要详细展开时正常输出，不受短句限制）。
- 不吹捧、不寒暄、闲聊不分点、不反问留客。
- 可以不知道、可以敷衍、可以不同意；累就短一点，不必每句都热情。
- 不用每轮都总结或升华，聊完就停。"""

# 按模型追加的精准避坑（只加一句）
_MODEL_HINTS: list[tuple[tuple[str, ...], str]] = [
    (
        ("deepseek",),
        "- 你不用「不是…而是…」句式，不用破折号，不堆短句排比；说完就停，不要升华。",
    ),
    (
        ("gemini", "gpt", "openai", "chatgpt", "o1", "o3", "o4"),
        "- 单句直说：避免翻译腔长句，不要分点排版，不要在末尾反问。",
    ),
    (
        ("claude", "sonnet", "opus", "haiku"),
        "- 大白话直说：不写文学比喻、不写心理与环境描写、不说教。",
    ),
]


def _clamp(v: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return default


def _length_directive(cfg: Any) -> str:
    """话多程度 -> 回复长度倾向（让滑杆真正作用于被动回复）。"""
    talk = _clamp(cfg.get("拟人_话多程度", 50))
    if talk <= 20:
        return "- 话少：能一句话就一句话，能一个词就一个词。"
    if talk <= 45:
        return "- 一两短句说完就停，不主动展开话题。"
    if talk <= 70:
        return "- 可以聊两句，接上对方的话顺带说点自己的看法，但别写成长段。"
    return "- 话可以多一点，顺着话题多聊两句，但别写成小作文。"


def _willingness_directive(cfg: Any) -> str:
    """沉默倾向 -> 没话说时的态度（被动回复里表现为克制，不硬凑）。"""
    quiet = _clamp(cfg.get("拟人_沉默倾向", 45))
    if quiet >= 75:
        return "- 没真想说的时候就给个很短的回应，不硬凑、不追问。"
    if quiet >= 50:
        return "- 只在真有兴趣的地方多说两句，其余简短带过。"
    return "- 愿意接话，但也别为了热闹硬找话题。"


def _model_hint(model_name: str) -> str:
    name = str(model_name or "").strip().lower()
    if not name:
        return ""
    for keys, hint in _MODEL_HINTS:
        if any(k in name for k in keys):
            return hint
    return ""


def speech_style_block(cfg: Any, model_name: str = "") -> str:
    """生成说话方式块；关闭时返回空串。

    Args:
        cfg: 插件配置视图。
        model_name: 当前会话实际模型名（用于追加一句针对性约束）。
    """
    try:
        if not bool(cfg.get("拟人_说话方式约束", True)):
            return ""
        mode = str(cfg.get("拟人_说话方式强度", "strict") or "strict").strip().lower()

        if mode == "gentle":
            parts = [_GENTLE, _length_directive(cfg)]
        else:
            parts = [
                _ANTI_BAGU,
                _HUMAN,
                _length_directive(cfg),
                _willingness_directive(cfg),
            ]
            hint = _model_hint(model_name)
            if hint:
                parts.append(hint)

        extra = str(cfg.get("拟人_说话方式补充", "") or "").strip()
        if extra:
            parts.append("- " + extra[:200])
        # 首行带 MARKER，确保 inject_temp_part / has_speech_marker 幂等去重真正生效
        return f"{MARKER}\n" + "\n".join(parts)
    except Exception:
        return ""


def has_speech_marker(parts: list) -> bool:
    for part in parts or []:
        try:
            text = ""
            if isinstance(part, dict):
                text = str(part.get("text") or "")
            else:
                text = str(getattr(part, "text", "") or "")
            if MARKER in text:
                return True
        except Exception:
            continue
    return False
