"""八股清洗规则表。

分两层：
  WORD_RULES     换词。一条规则挂一个候选池，抽的时候洗牌不放回，
                 一轮抽空才重洗，并且记住上次抽到哪个，防止连着撞同一个。
  SENTENCE_RULES 改骨架。不是换词，是把整句绕弯的说法直接裁直，
                 这类改完最见效，因为八股味大半长在句式上。

自己攒的，没有照搬别人的表。想加规则照着下面两条格式往下写就行，
id 要唯一，/八股 列表 会把所有 id 打出来。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 换词层

WORD_RULES: list[dict] = [
    {
        "id": "gaze_flicker",
        "name": "眼底闪过一抹…",
        "pattern": r"(?:眼底|眼中|眸中|眸光里)(?:飞快|快速)?地?闪过(?:一抹|一丝|一缕|几分)?"
        r"(?:复杂|异样|了然|心疼|失落|笑意|情绪|狠色|暗色|危险)?(?:的)?(?:神色|情绪|光|意味)?",
        "pool": ["眼里动了一下", "眼神变了变", "表情有点不对"],
    },
    {
        "id": "breath_hitch",
        "name": "呼吸一滞",
        "pattern": r"呼吸(?:猛地|陡然|倏地)?一(?:滞|窒|顿)",
        "pool": ["顿了一下", "停了半拍", "气没接上"],
    },
    {
        "id": "gasp_cold",
        "name": "倒吸一口凉气",
        "pattern": r"(?:猛地|狠狠)?倒吸(?:了)?一(?:口|大口)(?:凉气|冷气)",
        "pool": ["吸了口气", "愣了一下", "后背一紧"],
    },
    {
        "id": "heart_skip",
        "name": "心脏漏跳一拍",
        "pattern": r"(?:心脏|心跳)(?:猛地|骤然|倏地)?(?:漏跳|漏了)一?拍?",
        "pool": ["心里一紧", "心跳乱了", "胸口发闷"],
    },
    {
        "id": "throat_roll",
        "name": "喉结滚动",
        "pattern": r"喉结(?:上下)?(?:滚了滚|滚动|滑动|动了动)(?:了一下|一下|了)?",
        "pool": ["咽了口口水", "喉咙动了动", "吞了下口水"],
    },
    {
        "id": "knuckle_white",
        "name": "指节泛白",
        "pattern": r"(?:指尖|指节|手指)(?:因为用力)?(?:捏得|攥得|握得)?(?:微微)?泛白",
        "pool": ["手攥紧了", "捏得手发白", "指头抠紧了"],
    },
    {
        "id": "smile_arc",
        "name": "嘴角勾起一抹弧度",
        "pattern": r"(?:嘴角|唇边)(?:微微)?(?:勾起|扬起|弯起|牵起)(?:了)?(?:一抹|一丝)?(?:弧度|笑意|笑|弧)",
        "pool": ["笑了", "扯了下嘴角", "嘴角动了动"],
    },
    {
        "id": "half_smile",
        "name": "似笑非笑",
        "pattern": r"似笑非笑(?:的)?(?:表情|样子|弧度)?",
        "pool": ["表情有点怪", "笑意不明", "笑得不太对"],
    },
    {
        "id": "gaze_sink",
        "name": "眸光微沉",
        "pattern": r"(?:眸光|眼神|眸色|目光)(?:微微|倏地|骤然)?(?:一沉|沉了沉|暗了暗|暗下来|沉下来)",
        "pool": ["眼神冷下来", "脸色沉了", "眼睛眯起来"],
    },
    {
        "id": "ear_blood",
        "name": "耳根红得滴血",
        "pattern": r"耳(?:根|尖|垂)(?:红得)?(?:快要)?滴血",
        "pool": ["耳朵红透了", "耳根发烫", "耳朵烧起来"],
    },
    {
        "id": "lash_butterfly",
        "name": "睫毛如蝶翼",
        "pattern": r"(?:睫毛|眼睫)(?:如同|像|如)(?:蝶翼|蝴蝶翅膀|蝶翅)(?:般)?(?:轻颤|颤动|扑闪)?",
        # 替换词要能直接接后面的「着」：'睫毛轻颤' + '着落下' 读得顺，
        # 换成「睫毛动了动」会变成「睫毛动了动着落下」
        "pool": ["睫毛轻颤"],
    },
    {
        "id": "deer_heart",
        "name": "小鹿乱撞",
        "pattern": r"(?:心里|心头)(?:像)?(?:有)?(?:一头)?小鹿(?:乱撞|乱跳)",
        "pool": ["心跳很快", "心里发慌", "心跳得厉害"],
    },
    {
        "id": "air_freeze",
        "name": "空气凝固/粘稠",
        "pattern": r"空气(?:仿佛|好像|似乎)?(?:一瞬间)?(?:凝固|凝滞|粘稠|变得粘稠)(?:[^，。！？\n]{0,8})?",
        "pool": ["没人说话", "一下子静了", "气氛僵住"],
    },
    {
        "id": "dead_silent",
        "name": "落针可闻",
        "pattern": r"(?:安静得|静得)?(?:落针可闻|能听见针掉)|(?:安静得|静得)让人(?:发慌|窒息)",
        "pool": ["安静得吓人", "一点声音都没有", "静得难受"],
    },
    {
        "id": "ripple",
        "name": "石子投入湖心/涟漪",
        # 「涟漪」必须带出「泛起/荡起」才算八股：裸词会误伤人名与正常词
        # （群里有人就叫涟漪，或者聊「涟漪效应」）
        "pattern": (
            r"(?:像|如同|仿佛)?(?:一颗)?石子(?:投入|投进|落入)(?:了)?(?:平静的)?湖(?:心|面)"
            r"(?:[^，。！？\n]{0,10})?"
            r"|(?:泛起|荡起|漾起|荡出)(?:了)?(?:一圈圈?的?)?涟漪"
        ),
        "pool": ["波纹", "水响"],
    },
    {
        "id": "field",
        "name": "无形的磁场",
        "pattern": r"无形的(?:磁场|气场|压力)(?:在空气中)?(?:蔓延|扩散|弥漫)?",
        "pool": ["气氛", "那股劲儿", "气压"],
    },
    {
        "id": "claim_territory",
        "name": "宣示主权",
        "pattern": r"(?:像是在|仿佛在|如同在)?宣示主权",
        "pool": ["把人圈住", "占着不放", "划地盘"],
    },
    {
        "id": "superior_pressure",
        "name": "上位者的威压",
        "pattern": r"上位者的(?:威压|压迫感|气势)|不容置疑的(?:霸道|强势|口吻|语气)",
        "pool": ["压人的气势", "那股厉害劲儿", "没得商量的口气"],
    },
    {
        "id": "predator",
        "name": "捕食者与猎物",
        "pattern": r"捕食者(?:与|和|看向)?猎物|猎物(?:般|似的)(?:的)?(?:眼神|目光)?",
        "pool": ["盯着看", "眼神很凶", "像盯上了什么"],
    },
    {
        "id": "long_fingers",
        "name": "修长分明的手指",
        "pattern": r"修长(?:而)?(?:分明|白皙|有力)?的(?:手指|指节|指尖)",
        # 只留能当定语的替换：'手指很长' 是主谓短语，塞进定语位置会变病句
        "pool": ["手指"],
    },
    {
        "id": "mask_crack",
        "name": "完美面具碎裂",
        "pattern": r"(?:完美的?)?面具(?:终于)?(?:碎裂|剥落|崩裂|出现裂痕)",
        "pool": ["装不下去了", "脸色垮了", "绷不住了"],
    },
    {
        "id": "bio_jargon",
        "name": "多巴胺/肾上腺素",
        "pattern": r"多巴胺|肾上腺素|防卫机制|防御机制",
        "pool": ["脑子一热", "本能", "心里那点劲"],
    },
    {
        "id": "reflex_jargon",
        "name": "条件反射/应激反应",
        "pattern": r"条件反射|应激反应|防御姿态",
        "pool": ["下意识", "本能", "手比脑子快"],
    },
    {
        "id": "blood_reverse",
        "name": "全身血液倒流",
        "pattern": r"(?:全身的?)?血液(?:仿佛|好像)?(?:瞬间)?(?:倒流|凝固)|僵在原地",
        "pool": ["整个人僵住", "血往头上冲", "动不了了"],
    },
    {
        "id": "stomach_drop",
        "name": "胃部失重",
        "pattern": r"胃部?(?:一阵)?失重(?:感)?",
        "pool": ["心里一空", "肚子一沉"],
    },
    {
        "id": "spine_chill",
        "name": "后背发凉",
        "pattern": r"(?:后背|脊背|后颈)(?:一阵)?发凉",
        "pool": ["背后一凉", "脊背发紧", "后颈发麻"],
    },
    {
        "id": "electric",
        "name": "电流窜过",
        "pattern": r"(?:一股|一阵)?(?:电流|酥麻)(?:般)?(?:窜过|掠过|流遍)(?:全身)?",
        "pool": ["麻了一下", "抖了一下", "整个人软了半截"],
    },
    {
        "id": "playful_arc",
        "name": "玩味的弧度",
        # 名词部分不能可选：否则裸「玩味」也会被替换，把正常句子改坏
        "pattern": r"玩味(?:的)?(?:弧度|笑意|笑容|眼神)",
        "pool": ["笑得很欠", "挑了下眉", "一副看好戏的样子"],
    },
    {
        "id": "voice_rasp",
        "name": "砂纸打磨/大提琴般的嗓音",
        # 必须带出「的嗓音/声音/声线」：否则单一个「大提琴」就把乐器名改掉。
        # 顺带把前面的「一副/一把」量词吃掉，否则替换后会剩「他有一副声音发涩」。
        "pattern": (
            r"(?:一副|一把)?(?:像|如同|仿佛)?(?:被)?(?:砂纸打磨过|大提琴)"
            r"(?:般|一样)?的(?:嗓音|声音|声线)"
        ),
        "pool": ["嗓子哑", "声音低", "声音发涩"],
    },
    {
        "id": "cold_pool_eyes",
        "name": "不见底的寒潭",
        # 必须真的在写眼睛：'深不见底的古井' 是景物描写，不能换成「眼里没光」。
        # 顺带吃掉「一双/一对」，避免剩「他有一双眼睛黑得吓人」。
        "pattern": (
            r"(?:一双|一对)?(?:深)?不见底的(?:眼眸|眼瞳|双眼|眼睛|眼底|眼神)"
            r"|(?:深潭|古井)(?:般|似的)的?(?:眼眸|眼瞳|双眼|眼睛|眼底|眼神)"
        ),
        "pool": ["眼神很沉", "眼睛黑得吓人", "眼里没光"],
    },
    {
        "id": "tremble_lip",
        "name": "嘴唇微微颤抖",
        "pattern": r"(?:嘴唇|唇瓣)(?:微微)?(?:颤抖|哆嗦|发抖)",
        "pool": ["嘴抖了一下", "嘴唇发颤", "说不出话"],
    },
]

# ---------------------------------------------------------------- 改句式
#
# 这一层的正则和替换逻辑来自 Ny-bagu-remover 的语法规则，作者已同意改私人版、
# 不公开。相比我自己重写的粗糙版本，它多做了两件事：
#   1. 捕获前缀（这/那/他/她/眼神/目光…），改完不会把主语丢了
#   2. 判断后半段是不是动词短语，是的话不再补「是」，
#      免得出现「他是想哭」这种别扭话
# 底下 ratio_split / reason_string / 英文两条是我补的。

_SUBJECT_PREFIX_RE = re.compile(r"^(?:在|因为|出于|为了|想|要|敢|能|会)")
_PRONOUNS = ("他", "她", "它")


def _repl_not_but(m: re.Match) -> str:
    """不是A，而是B → 前缀 + 是 + B"""
    prefix = (m.group(1) or "").strip()
    tail = re.sub(r"^是+", "", m.group(3).strip()).strip()
    if prefix:
        if _SUBJECT_PREFIX_RE.match(tail) and prefix in _PRONOUNS:
            return prefix + tail
        return prefix + "是" + tail
    return "是" + tail


def _repl_not_but_cross(m: re.Match) -> str:
    """不是A。是B → 前缀 + 是 + B"""
    prefix = (m.group(1) or "").strip()
    tail = re.sub(r"^是+", "", m.group(3).strip()).strip()
    return f"{prefix}是{tail}" if prefix else f"是{tail}"


def _repl_not_but_2(m: re.Match) -> str:
    """并非A，而是B → 前缀 + 是 + B"""
    prefix = (m.group(1) or "").strip()
    tail = re.sub(r"^是+", "", m.group(3).strip()).strip()
    return f"{prefix}是{tail}" if prefix else f"是{tail}"


def _repl_rather(m: re.Match) -> str:
    return "更像是" + m.group(2).strip()


def _repl_less_more(m: re.Match) -> str:
    return "透出几分" + m.group(2).strip()


def _repl_ratio(m: re.Match) -> str:
    return f"有点{m.group(1).strip()}，更多是{m.group(2).strip()}"


def _repl_en_was(m: re.Match) -> str:
    tense = "was" if re.search(r"was", m.group(0), re.I) else "is"
    return f"It {tense} {m.group(2).strip()}"


def _repl_en_not_but(m: re.Match) -> str:
    tail = m.group(2).strip()
    return tail[:1].upper() + tail[1:]


SENTENCE_RULES: list[dict] = [
    {
        "id": "negative_parallelism",
        "name": "不是…而是/是…",
        "pattern": re.compile(
            r"((?:这|那|它|他|她|其|眼神|目光|声音|笑容|动作|语气)?)?(?:并|绝)?不是"
            r"([^，。；！？\n]{1,36})[，,]\s*"
            r"(?:(?:甚至|也|更)?不是[^，。；！？\n]{1,30}[，,]\s*)*"
            r"(?:而是|是)([^。；！？\n]+)"
        ),
        "repl": _repl_not_but,
    },
    {
        "id": "negative_parallelism_cross",
        "name": "跨句 不是A。是/而是B",
        "pattern": re.compile(
            r"((?:这|那|它|他|她)?)?(?:并|绝)?不是([^。！？\n]{1,28})[。.]\s*"
            r"(?:(?:甚至|也)?不是[^。！？\n]{1,25}[，,。.]\s*)*"
            r"(?:而是|只是|是)([^。！？\n]+)"
        ),
        "repl": _repl_not_but_cross,
    },
    {
        "id": "negative_parallelism_2",
        "name": "并非…而是…",
        "pattern": re.compile(
            r"((?:这|那|它|他|她)?)并非(?:出于|因为)?([^，。；！？\n]{1,36})[，,]\s*"
            r"(?:而是|是)((?:出于|因为)?[^。；！？\n]+)"
        ),
        "repl": _repl_not_but_2,
    },
    {
        "id": "rather_than",
        "name": "与其说…不如说…",
        "pattern": re.compile(
            r"与其说是([^，。；！？\n]{1,30})[，,]\s*(?:倒)?不如说是([^。；！？\n]+)"
        ),
        "repl": _repl_rather,
    },
    {
        "id": "less_more",
        "name": "少了几分…多了几分…",
        "pattern": re.compile(
            r"少了几分([^，。；！？\n]{1,20})[，,]\s*多了几分([^。；！？\n]+)"
        ),
        "repl": _repl_less_more,
    },
    {
        "id": "ratio_split",
        "name": "三分A，七分B",
        "pattern": re.compile(
            r"三分([^，。；！？\n]{1,12})[，,]\s*七分([^。；！？\n]{1,12})"
        ),
        "repl": _repl_ratio,
    },
    {
        "id": "reason_string",
        "name": "名为理智的弦崩断",
        "pattern": re.compile(
            r"(?:那根)?名为(?:理智|克制|冷静)的弦(?:终于|彻底|猛地)?(?:崩断|断了|断裂)"
        ),
        "repl": lambda m: "撑不住了",
    },
    {
        "id": "en_it_was",
        "name": "[EN] It wasn't A, it was B",
        "pattern": re.compile(
            r"\bIt\s+(?:wasn't|was\s+not|isn't|is\s+not)\s+(?:just\s+|merely\s+)?"
            r"([^,.;!?\n]{1,45})[,;—–-]\s*it\s+(?:was|is)\s+([^.;!?\n]+)",
            re.I,
        ),
        "repl": _repl_en_was,
    },
    {
        "id": "en_not_but",
        "name": "[EN] Not A, but B",
        "pattern": re.compile(
            r"\bNot\s+(?:a\s+|an\s+|just\s+|merely\s+)?([^,.;!?\n—–-]{2,35})"
            r"(?:,\s*but\s+|\s+[—–-]\s+)(?:rather\s+)?([a-zA-Z][^.;!?\n]+)"
        ),
        "repl": _repl_en_not_but,
    },
]

# 软化副词：单独走配额，不走替换池
SOFTENERS = ["微微", "缓缓", "轻轻", "淡淡", "默默", "静静", "悄悄"]

# 固定词里的「水词」不能删：悄悄话 / 微微一笑 / 默默无闻。
# 值是该词后面要排除的后缀（负向前瞻）。
SOFTENER_EXCLUDE: dict[str, str] = {
    "悄悄": r"(?!话)",
    "微微": r"(?!一笑)",
    "默默": r"(?!无闻)",
}

# 结尾老套升华。命中就整句删掉。
ENDING_PATTERNS = [
    re.compile(r"^(?:而)?这一切[，,]?(?:才)?(?:刚刚|只是)?(?:开始|拉开序幕)[。！…]*$"),
    re.compile(r"^(?:这个|那一?)?夜[，,]?(?:还|很)?(?:很长|才刚开始|深了)[。！…]*$"),
    re.compile(r"^而?答案[，,]?(?:早已|已经)?(?:写在|藏在)[^。！？\n]{0,20}[。！…]*$"),
    re.compile(r"^(?:至于|剩下的)[，,]?(?:就)?(?:交给|留给)[^。！？\n]{0,20}[。！…]*$"),
    re.compile(r"^但(?:至少|好在)[，,]?[^。！？\n]{0,30}[。！…]*$"),
    re.compile(r"^(?:至于)?(?:接下来|往后)[，,]?(?:会怎样|如何)[^。！？\n]{0,20}[。！…]*$"),
    re.compile(r"^决定权[，,]?(?:终究)?(?:在|交给)[^。！？\n]{0,12}[。！…]*$"),
    re.compile(r"^命运[，,]?(?:早已|已经)?[^。！？\n]{0,20}[。！…]*$"),
]

# 不参与清洗的整段特征
PROTECT_LINE_HINTS = ("```", "|---", "| :", "|:", "```")


def rule_ids() -> list[tuple[str, str, str]]:
    """返回 [(层, id, 名称)]，给 /八股 列表 用。"""
    out: list[tuple[str, str, str]] = []
    for r in WORD_RULES:
        out.append(("词", r["id"], r["name"]))
    for r in SENTENCE_RULES:
        out.append(("句", r["id"], r["name"]))
    return out
