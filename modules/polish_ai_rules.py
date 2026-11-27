"""AI 八股规则库：按「模型实际写作习惯」归类，供八股清洗与面板透明展示使用。

分类依据（面向常见中文/英文大模型的输出习惯，不针对某一家）：
  opening    开场白套话：首先/其次、值得注意的是、总的来说……
  structure  结构八股：编号列表、加粗滥用、破折号开头、项目符号
  elevate    结尾升华：让我们一起、愿……、而这仅仅是开始、希望对你有帮助
  persona    自我指称与推诿：作为 AI、我无法、我需要澄清
  service    客服腔：以上是、如有其他问题、欢迎随时告诉我
  corporate  职场黑话：赋能、闭环、抓手、颗粒度、对齐
  redundant  冗余修饰：非常非常、极其、十分、真的真的
  parallel   机械排比：既要…又要…还要、不仅…而且…

每条规则：id / name（面板显示） / category / desc（为什么算八股） / pattern / repl
清洗顺序由 prose_polish 统一控制；这里只描述"要改什么"。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 开场白套话
OPENING_RULES: list[dict] = [
    {
        "id": "ai_first_second",
        "name": "首先/其次/最后 三段式",
        "category": "opening",
        "desc": "模型爱用议论文三段式开头，真人聊天不会这么起手",
        "pattern": re.compile(r"(?:首先|其次|再次|再者|最后(?:一点)?)[，,：:]\s*"),
        "repl": "",
    },
    {
        "id": "ai_worth_noting",
        "name": "值得注意的是",
        "category": "opening",
        "desc": "模型的高频转折套话，信息量几乎为零",
        "pattern": re.compile(
            r"(?:值得注意的是|需要指出的是|需要强调的是|需要说明的是|"
            r"值得一提的是|特别值得一提的是|不得不说的是)[，,：:]?\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_in_summary",
        "name": "总的来说/综上所述",
        "category": "opening",
        "desc": "总结腔，聊天里没必要做会议纪要",
        "pattern": re.compile(
            r"(?:总的来说|总而言之|综上所属|综上所述|总结一下|概括来说|"
            r"总体而言|整体来看)[，,：:]?\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_in_fact",
        "name": "事实上/实际上",
        "category": "opening",
        "desc": "填充式转折，删掉意思不变",
        "pattern": re.compile(r"(?:事实上|实际上|其实呢|说白了就是)[，,：:]?\s*|(?<![都讲])说白了[，,：:]\s*"),
        "repl": "",
    },
    {
        "id": "ai_from_certain",
        "name": "从某种意义上说",
        "category": "opening",
        "desc": "模糊限定语，模型的免责式铺垫",
        "pattern": re.compile(
            r"(?:从某种意义上说|某种程度上|在一定意义上|可以说的是)[，,：:]?\s*|(?:^|(?<=[，。！？\n]))可以说[，,：:]\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_obvious",
        "name": "不难看出/众所周知",
        "category": "opening",
        "desc": "「显然」类套话，抬高姿态又没信息",
        "pattern": re.compile(
            r"(?:不难看出|不难发现|众所周知|毋庸置疑|毫无疑问|显而易见)[，,：:]?\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_in_other_words",
        "name": "换句话说/也就是说",
        "category": "opening",
        "desc": "重复解释同一件事，浪费字数",
        "pattern": re.compile(r"(?:换句话说|也就是说|简单来说|简单来讲)[，,：:]?\s*"),
        "repl": "",
    },
]

# ---------------------------------------------------------------- 结构八股
STRUCTURE_RULES: list[dict] = [
    {
        "id": "ai_numbered_list",
        "name": "编号列表",
        "category": "structure",
        "desc": "把闲聊写成操作手册，模型最典型的输出形态",
        "pattern": re.compile(r"(?:^|\n)\s*\d+[.、)]\s*"),
        "repl": "\n",
    },
    {
        "id": "ai_bullet_list",
        "name": "项目符号列表",
        "category": "structure",
        "desc": "破折号/圆点开头的列表，真人聊天不用",
        "pattern": re.compile(r"(?:^|\n)\s*[-*•·]\s+"),
        "repl": "\n",
    },
    {
        "id": "ai_bold_mark",
        "name": "加粗滥用",
        "category": "structure",
        "desc": "**加粗** 在群聊里会变成星号噪音",
        "pattern": re.compile(r"\*\*([^*\n]{1,40})\*\*"),
        "repl": r"\1",
    },
    {
        "id": "ai_heading",
        "name": "标题层级",
        "category": "structure",
        "desc": "# 标题在聊天里没有意义",
        "pattern": re.compile(r"(?:^|\n)#{1,6}\s*"),
        "repl": "\n",
    },
    {
        "id": "ai_colon_block",
        "name": "冒号起块",
        "category": "structure",
        "desc": "「以下是几点：」后面的分块解释",
        "pattern": re.compile(
            r"(?:以下是|主要有|具体包括|具体如下|分为以下几点|有以下几个方面)[：:]\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_hr_line",
        "name": "分隔线",
        "category": "structure",
        "desc": "--- 分隔线是文档习惯，不是聊天",
        "pattern": re.compile(r"(?:^|\n)\s*(?:-{3,}|_{3,}|\*{3,})\s*(?:\n|$)"),
        "repl": "\n",
    },
]

# ---------------------------------------------------------------- 结尾升华
ELEVATE_RULES: list[dict] = [
    {
        "id": "ai_lets_together",
        "name": "让我们一起…",
        "category": "elevate",
        "desc": "集体号召式结尾，模型最爱的升华句式",
        "pattern": re.compile(
            r"(?:让我们(?:一起)?|大家一起)[^。！？\n]{0,12}(?:探索|期待|迎接|开启|携手|共同|见证|努力|创造|迈向|拥抱|前行)[^。！？\n]{0,20}[。！？]?"
        ),
        "repl": "",
    },
    {
        "id": "ai_blessing_ending",
        "name": "愿…/希望…式祝福",
        "category": "elevate",
        "desc": "结尾硬塞祝福，和上文没有逻辑关系",
        "pattern": re.compile(
            r"(?:祝愿|愿你|愿您)[^。！？\n]{0,16}(?:一切顺利|万事如意|前程似锦|生活愉快|工作顺利|越来越好|平安喜乐)[。！？]?"
            r"|希望你(?:的)?(?:未来|生活|每一天)[^。！？\n]{0,12}(?:美好|愉快|越来越好)[。！？]?"
        ),
        "repl": "",
    },
    {
        "id": "ai_just_beginning",
        "name": "而这仅仅是开始",
        "category": "elevate",
        "desc": "模型最爱用的宏大开篇/收尾",
        "pattern": re.compile(
            r"(?:而)?这一切?(?:才)?(?:仅仅)?(?:只是)?(?:刚刚)?(?:开始|起步)[。！？]?"
        ),
        "repl": "",
    },
    {
        "id": "ai_future_bright",
        "name": "未来可期",
        "category": "elevate",
        "desc": "空口号式收尾",
        "pattern": re.compile(r"(?:未来可期|未来值得期待|前景广阔|拭目以待)[。！？]?"),
        "repl": "",
    },
    {
        "id": "ai_help_ending",
        "name": "希望对你有帮助",
        "category": "elevate",
        "desc": "客服式收尾，不是朋友聊天的语气",
        "pattern": re.compile(
            r"(?:希望(?:这)?(?:些)?(?:内容)?(?:对你|能)(?:有所)?(?:帮助|帮到你)|"
            r"希望能帮到你|希望上述内容对你有帮助)[。！？]?"
        ),
        "repl": "",
    },
]

# ---------------------------------------------------------------- 自我指称
PERSONA_RULES: list[dict] = [
    {
        "id": "ai_as_model",
        "name": "作为 AI/语言模型",
        "category": "persona",
        "desc": "跳出角色自报家门，直接破坏拟人",
        "pattern": re.compile(
            r"(?:作为|身为)(?:一个)?\s*(?:AI|人工智能|语言模型|大模型|智能助手|助手)[，,]?\s*"
        ),
        "repl": "",
    },
    {
        "id": "ai_i_cannot",
        "name": "我无法/我不能",
        "category": "persona",
        "desc": "过度推诿；不应答可以直接跳过而不是声明限制",
        "pattern": re.compile(r"我(?:无法|不能|没办法)(?:为你|帮)?(?:提供|回答|完成|满足)"),
        "repl": "这个我说不好",
    },
    {
        "id": "ai_need_clarify",
        "name": "我需要澄清",
        "category": "persona",
        "desc": "公文式澄清，聊天里显得生硬",
        "pattern": re.compile(r"我需要(?:先)?(?:澄清|说明|指出|强调)(?:一下)?[，,：:]?\s*"),
        "repl": "",
    },
]

# ---------------------------------------------------------------- 客服腔
SERVICE_RULES: list[dict] = [
    {
        "id": "ai_above_is",
        "name": "以上是",
        "category": "service",
        "desc": "交付式表述，像在发工单",
        "pattern": re.compile(r"(?:以上就是|以上是|以上内容)[^。\n]{0,12}[。]?"),
        "repl": "",
    },
    {
        "id": "ai_any_question",
        "name": "如有其他问题",
        "category": "service",
        "desc": "标准客服用语",
        "pattern": re.compile(
            r"(?:如果|若|如)[^。！？\n]{0,10}"
            r"(?:还有|有其他|有其它|有问题|有疑问|需要帮助|遇到问题)"
            r"[^。！？\n]{0,12}"
            r"(?:欢迎|随时|尽管|都可以|可以)[^。！？\n]{0,10}"
            r"(?:告诉我|告诉我一声|问我|提出|找我|联系我|问)"
            r"[^。！？\n]{0,4}[。！？]?"
        ),
        "repl": "",
    },
    {
        "id": "ai_welcome_ask",
        "name": "欢迎随时提问",
        "category": "service",
        "desc": "客服式邀请，没人这么聊天",
        "pattern": re.compile(
            r"(?:欢迎|随时|尽管)(?:随时)?(?:向我|找我|来)?"
            r"(?:提问|咨询|联系我|问我|告诉我|找我)[^。！？\n]{0,4}[。！？]?"
        ),
        "repl": "",
    },
    {
        "id": "ai_how_can_i",
        "name": "还有什么可以帮您",
        "category": "service",
        "desc": "敬语+帮您，明显客服口吻",
        "pattern": re.compile(r"(?:请问)?(?:还有)?(?:什么|其它|其他)(?:可以)?(?:帮|需要)[^。！？\n]{0,8}[您你吗？?]?[。！？]?"),
        "repl": "",
    },
]

# ---------------------------------------------------------------- 职场黑话
CORPORATE_WORDS: list[str] = [
    "赋能", "闭环", "抓手", "颗粒度", "拉通", "对齐一下", "沉淀一下",
    "组合拳", "打法", "势能", "心智", "壁垒", "生态位", "链路",
    "发力", "助力", "打造", "落地", "复盘", "对齐",
]

# 职场黑话替换池（保持原意但说人话）
CORPORATE_REPL: dict[str, list[str]] = {
    "赋能": ["帮着", "让…能干"],
    "闭环": ["做完一整套", "收尾"],
    "抓手": ["着手点", "入口"],
    "颗粒度": ["细节程度", "粗细"],
    "拉通": ["串起来", "打通"],
    "组合拳": ["几个办法一起上", "配套做"],
    "打法": ["做法", "路子"],
    "势能": ["势头", "劲头"],
    "心智": ["认知", "印象"],
    "壁垒": ["门槛", "优势"],
    "生态位": ["位置", "定位"],
    "链路": ["流程", "环节"],
    "发力": ["使劲", "重点做"],
    "助力": ["帮着", "帮上"],
    "打造": ["做", "弄"],
    "落地": ["真做起来", "实行"],
    "复盘": ["回头看看", "总结"],
    "对齐": ["对一下", "说好"],
}

# ---------------------------------------------------------------- 冗余修饰
REDUNDANT_RULES: list[dict] = [
    {
        "id": "ai_double_really",
        "name": "真的真的",
        "category": "redundant",
        "desc": "叠词强调，真人极少这么写",
        "pattern": re.compile(r"真的真的|非常非常|特别特别|十分十分|相当相当"),
        "repl": "挺",
    },
    {
        "id": "ai_extremely",
        "name": "极其/十分",
        "category": "redundant",
        "desc": "书面程度副词，口语里显得用力",
        "pattern": re.compile(
            r"(?:极其|极为)(?=[^\s，。！？、])"
            r"|(?<![零一二三四五六七八九两半满扣加减差第点分打给得考拿评计0-9])"
            r"十分(?![钟之的制满左右多来半前后内外了呢吧啊吗，。！？、；：\s]|$)"
        ),
        "repl": "很",
    },
]

# ---------------------------------------------------------------- 机械排比
PARALLEL_RULES: list[dict] = [
    {
        "id": "ai_both_and_also",
        "name": "既要…又要…还要",
        "category": "parallel",
        "desc": "三连排比是模型最典型的注水方式",
        "pattern": re.compile(r"既(?:要|能)([^，。\n]{1,20})[，,]\s*(?:又|也)(?:要|能)([^，。\n]{1,20})[，,]\s*(?:还|更)(?:要|能)([^。！？\n]{1,20})"),
        "repl": r"\1，\2，还要\3",
    },
    {
        "id": "ai_not_only_but",
        "name": "不仅…而且…",
        "category": "parallel",
        "desc": "递进连词堆叠，删掉后半句意思不变",
        "pattern": re.compile(r"不仅(?:仅)?(?:是)?([^，。\n]{1,20})[，,]\s*(?:而且|更)(?:是|要)?([^。！？\n]{1,20})"),
        "repl": r"\1，也\2",
    },
]

# ---------------------------------------------------------------- 汇总
CATEGORY_LABELS: dict[str, str] = {
    "opening": "开场白套话",
    "structure": "结构八股",
    "elevate": "结尾升华",
    "persona": "自我指称",
    "service": "客服腔",
    "corporate": "职场黑话",
    "redundant": "冗余修饰",
    "parallel": "机械排比",
}

AI_RULES: list[dict] = (
    OPENING_RULES + STRUCTURE_RULES + ELEVATE_RULES
    + PERSONA_RULES + SERVICE_RULES + REDUNDANT_RULES + PARALLEL_RULES
)


def build_corporate_rules() -> list[dict]:
    """把职场黑话词表转成规则（每个词一条，便于面板单条禁用）。"""
    out: list[dict] = []
    for idx, word in enumerate(CORPORATE_WORDS):
        pool = CORPORATE_REPL.get(word) or []
        if not pool:
            continue
        # 保留原词作为候选之一，避免被改成更怪的表达
        out.append({
            "id": f"corp_{idx:02d}",
            "name": f"职场黑话：{word}",
            "category": "corporate",
            "desc": f"「{word}」是职场/PPT 用语，日常聊天不会这么说",
            "pattern": re.compile(re.escape(word)),
            "pool": pool,
            "word": word,
        })
    return out


ALL_AI_RULES: list[dict] = AI_RULES + build_corporate_rules()


def rule_catalog() -> list[dict]:
    """给面板用的规则清单（不含正则对象，可安全 JSON 序列化）。"""
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "category": r["category"],
            "category_label": CATEGORY_LABELS.get(r["category"], r["category"]),
            "desc": r["desc"],
            "has_pool": bool(r.get("pool")),
        }
        for r in ALL_AI_RULES
    ]


def category_catalog() -> list[dict]:
    """分类清单 + 各类规则数，供面板分组展示。"""
    counts: dict[str, int] = {}
    for r in ALL_AI_RULES:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    return [
        {"key": k, "label": v, "count": counts.get(k, 0)}
        for k, v in CATEGORY_LABELS.items()
    ]
