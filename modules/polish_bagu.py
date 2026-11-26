import re

"""从「物理杀八股」词表转过来的删词层。

原词表 132 条，策略是无脑删。直接搬过来实测会把句子删废：

    语气不容置疑。        → 语气。
    排他性的温柔。        → 的温柔。
    身体很诚实地出卖了她  → 地出卖了她
    背德感扑面而来        → 感扑面而来

原因是那张表配的是特定角色卡的特定句式，脱离上下文就是一把没准星的刀。

所以拆成两层，并且都过安全阀（见 main.py 的 _delete_phrases）：

  SAFE_DELETE       定语、状语、程度副词。删掉之后中心词和动词还在，句子仍站得住
  AGGRESSIVE_DELETE 名词、动词、半截话。删了会缺成分，默认关

标点和残句片段（、（）！！连他 什、 的、）一律不收——删了会让句子粘连。
"""

SAFE_DELETE: list[str] = [
    # 察觉类（定语）
    "自己都没有察觉到的",
    "自己都没察觉到的",
    "连他自己都没察觉到的",
    "连她自己都没察觉到的",
    "连他自己都未曾察觉",
    "连她自己都未曾察觉",
    "自己都没察觉的",
    "极难察觉的",
    "察觉不到的",
    "难以察觉的",
    "微不可察的",
    "几不可察的",
    "几不可查的",
    "不易察觉的",
    # 察觉类（状语）
    "几不可察地",
    "不可察觉地",
    "不易察觉地",
    "微不可察地",
    # 语气类
    "不容置疑地",
    "不容置疑的",
    "不容置喙的",
    "不容拒绝的",
    # 程度
    "极度的",
    "极端的",
    "极其",
    "绝对的",
    "毁灭性",
    "过载的",
    # 神圣
    "神圣的",
    "虔诚的",
    "虔诚",
    # 机械
    "机器般",
    # 状态
    "失而复得的",
    "生理性",
    "那双赤红的",
    "一闪而过的",
    "沙哑的",
    "黏糊糊的",
    "咆哮的",
    "易碎的",
    "稀世",
    "卑微",
    "算计的",
    "很诚实地",
    "背德的",
    "泛白",
    "发白",
    "浑浊",
    "发红的",
    "赤红的",
    # 量词
    "一丝",
    "一抹",
    # 虚词
    "仿佛",
    "几乎",
    "几乎是",
    "一种",
]

AGGRESSIVE_DELETE: list[str] = [
    # 比喻整块
    "那是一种怎样",
    "那是怎样",
    "投下了一颗石子",
    "像是一颗石子",
    "投入了一颗",
    "一颗投入",
    "深水炸弹",
    "溺水的人",
    "的浮木",
    # 名词
    "排他性",
    "私有物",
    "所有权",
    "精密仪器",
    "手术刀",
    "小兽",
    "幼兽",
    "野兽",
    "雄性",
    "雌性",
    "石楠花",
    "肠液",
    "心湖",
    "共犯",
    "“共犯”",
    "神仙设定",
    "单片",
    "指节",
    "七分",
    "仪式",
    "眼镜",
    # 动词
    "献祭",
    "膜拜",
    "吞吃入腹",
    "拆吃入腹",
    "低吼",
    "升华",
    "支配",
    "推了推",
    "崇拜",
    # 半截话
    "最后一根",
    "总是带着点",
    "原本就有些",
    "而沙哑",
    "身体很诚实",
    "背德感和",
    "背德和",
    "背德",
    "不存在的",
    "并不存在的",
    "其实根本",
    "连他",
    "信徒",
    "的信徒",
    "啊啊啊",
    "豁……",
    "什……",
    # 常见词
    "扭曲",
    "设定",
]


def all_phrases(aggressive: bool) -> list[str]:
    """按长度降序返回待删短语，长短语先走，免得「共犯」先把「“共犯”」拆了。"""
    pool = list(SAFE_DELETE)
    if aggressive:
        pool += AGGRESSIVE_DELETE
    return sorted(set(pool), key=len, reverse=True)

# ============================================================ 分级治理（新增）
# 原则：
#   高确定性 -> 自动裁剪，但只在「正文还有剩余」时生效，绝不把单句洗空
#   高风险   -> 只统计命中，绝不正则硬改（硬改会砍出语病，交给上游 prompt 约束）

# ---- 高确定性：掐马屁/捧哏头（句首独立短句）
FLATTERY_HEAD = re.compile(
    r"^[\s，,]*(?:"
    r"你(?:说|讲|问|抓|总结)得?(?:太[对准好棒]了?|很?[对准])"
    r"|(?:这个问题|这问题)问得(?:很?好|不错|到位)"
    r"|你?(?:观察|看)得?(?:真|很|太)?(?:敏锐|仔细|准)"
    r"|(?:确实|的确)(?:很)?有(?:见地|道理|洞察)"
    r"|一针见血"
    r"|说得(?:太)?(?:好|对)了"
    r"|你这个?(?:角度|思路|观点)(?:很|真|太)?(?:新颖|独特|好|妙|到位)"
    r"|完全(?:同意|赞同|正确)"
    r"|(?:太|很)棒了"
    r")[。！!，,\s]*[，,。！!\s]*"
)

# ---- 高确定性：掐公文头（句首连接词）
OFFICIAL_HEAD = re.compile(
    r"^[\s]*(?:首先|其次|再次|再者|最后|总之|总而言之|综上(?:所述|所属)?|"
    r"值得注意的是|需要(?:指出|强调|说明)的是|值得一提的是|"
    r"总而言之|总的来说|总体而言|简而言之)[，,：:\s]*"
)

# ---- 高确定性：去结案/留客/升华尾（仅多句回复时裁最后一句）
NPC_CLOSING = re.compile(
    r"^行[，,]?\s*(?:这话我记下了|我记下了|那改天再说|改天再说|就这么定了|"
    r"那就这样|先这样|这样吧|知道了)[。！!～~…]*$"
)
ELEVATE_CLOSING = re.compile(
    r"^(?:这就够了|这样就(?:好|行)了|这就很(?:好|圆满)了|如此(?:便好|就好)|"
    r"但愿如此|希望如此)[。！!～~…]*$"
)
ASK_BACK_CLOSING = re.compile(
    r"^[^。！？\n]{0,20}(?:你觉得呢|你怎么看|你说呢|你觉得如何|"
    r"有什么想法|要不要试试看?)[？?～~…]*$"
)

# ---- 高确定性：破折号降级（正文内连续破折号 -> 逗号）
DASH_RUN = re.compile(r"\s*[—–]{2,}\s*|\s*――+\s*")

# ---- 高风险：只检测，不改写
RISK_PATTERNS: dict[str, re.Pattern] = {
    # DeepSeek 最爱：不是…而是…
    "not_but": re.compile(r"不是[^。！？\n]{1,24}[，,](?:而是|是)[^。！？\n]{1,24}"),
    # 短剧二字甩尾：……，省事 / 体面 / 痛快 / 稳了 / 够用 / 不亏
    "drama_tail": re.compile(
        r"[，,]\s*(?:省事|体面|痛快|稳了|够用|不亏|值了|血赚|真香)[。！!～~…]*$"
    ),
    # 三连短句排比：X。Y。Z。（每句都很短）
    "short_parallel": re.compile(
        r"(?:[^。！？\n]{1,8}[。！？]){3}"
    ),
    # 破折号泛滥
    "dash_heavy": re.compile(r"[—–]{2,}"),
}


def trim_flattery_head(text: str) -> tuple[str, int]:
    """掐掉句首马屁短句；洗完不能为空，否则保持原样。"""
    if not text:
        return text, 0
    m = FLATTERY_HEAD.match(text)
    if not m:
        return text, 0
    rest = text[m.end():]
    if not rest.strip():
        return text, 0  # 整句就是马屁：不洗空
    return rest.lstrip(), 1


def trim_official_head(text: str) -> tuple[str, int]:
    """掐掉句首公文连接词；洗完不能为空。"""
    if not text:
        return text, 0
    m = OFFICIAL_HEAD.match(text)
    if not m:
        return text, 0
    rest = text[m.end():]
    if not rest.strip():
        return text, 0
    return rest.lstrip(), 1


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点切句，保留标点。"""
    parts = re.findall(r"[^。！？!?\n]+[。！？!?…～~]*|[。！？!?…～~]+", text)
    return [p for p in parts if p]


def trim_closing_tail(text: str) -> tuple[str, int]:
    """去掉最后一句的 NPC 结案 / 升华 / 反问留客。

    只在「多句回复」时动手；剩下一句就保留，避免把单句洗空。
    """
    if not text or not text.strip():
        return text, 0
    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return text, 0
    last = sentences[-1].strip()
    if not (NPC_CLOSING.match(last) or ELEVATE_CLOSING.match(last)
            or ASK_BACK_CLOSING.match(last)):
        return text, 0
    body = "".join(sentences[:-1]).rstrip()
    if not body.strip():
        return text, 0
    return body, 1


def downgrade_dash(text: str) -> tuple[str, int]:
    """正文里的连续破折号降级成逗号（代码/引用已被 vault 保护，不会进来）。"""
    if not text or "—" not in text and "–" not in text:
        return text, 0
    new, n = DASH_RUN.subn("，", text)
    if n:
        new = re.sub(r"，{2,}", "，", new)
        new = re.sub(r"^，", "", new)
    return new, n


def detect_high_risk(text: str) -> dict[str, int]:
    """高风险句式统计：只用于面板展示与日志，不改写文本。"""
    if not text:
        return {}
    out: dict[str, int] = {}
    for key, pattern in RISK_PATTERNS.items():
        n = len(pattern.findall(text))
        if n:
            out[key] = n
    if out.get("short_parallel"):
        # 三连短句的单条命中算一次即可，避免数字虚高
        out["short_parallel"] = 1
    return out


RISK_LABELS = {
    "not_but": "不是…而是…",
    "drama_tail": "短剧金句甩尾",
    "short_parallel": "短句排比",
    "dash_heavy": "破折号泛滥",
}


TRIM_RULE_CATALOG = [
    {"id": "trim_flattery_head", "name": "掐马屁/捧哏头", "category": "trim",
     "desc": "句首独立的奉承短句（你说得太对了、一针见血），裁掉但保留正文"},
    {"id": "trim_official_head", "name": "掐公文连接词", "category": "trim",
     "desc": "句首的首先/其次/值得注意的是/总而言之等公文头"},
    {"id": "trim_closing_tail", "name": "去结案/留客尾句", "category": "trim",
     "desc": "多句回复里最后一句的「行，那就这样」「你觉得呢？」这类收尾"},
    {"id": "downgrade_dash", "name": "破折号降级", "category": "trim",
     "desc": "正文里连续破折号换成逗号（代码/引用不动）"},
]

RISK_ONLY_CATALOG = [
    {"id": "risk_not_but", "name": "不是…而是…", "category": "risk_only",
     "desc": "DeepSeek 高频句式；硬改会出语病，只统计不改写"},
    {"id": "risk_drama_tail", "name": "短剧金句甩尾", "category": "risk_only",
     "desc": "「……，省事/体面/痛快」；只统计，交给上游 prompt 约束"},
    {"id": "risk_short_parallel", "name": "短句排比", "category": "risk_only",
     "desc": "三连极短句；只统计"},
    {"id": "risk_dash_heavy", "name": "破折号泛滥", "category": "risk_only",
     "desc": "多处破折号；只统计"},
]


def trim_rule_catalog() -> list[dict]:
    return list(TRIM_RULE_CATALOG)


def risk_rule_catalog() -> list[dict]:
    return list(RISK_ONLY_CATALOG)
