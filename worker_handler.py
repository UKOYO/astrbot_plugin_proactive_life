import random
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.mcp_client import MCPTool

from .common import DEFAULT_TARGET_UID, parse_quiet_hours
from .handler_base import HandlerContextMixin
from .agent_runner import (
    build_agent_config, make_request, run_agent,
)

# 默认探索方向（用户没自定义时使用）；中性通用，不绑定任何人的兴趣或特定工具
_DEFAULT_EXPLORE_INTENT = """1. **近期热点与新闻**：用可用的联网搜索查查今天/本周的新鲜事、行业动态或大家正在讨论的话题；
2. **实用资讯**：查查天气、节日、生活小知识或值得留意的信息；
3. **轻松话题**：看看热门榜单、有趣内容或值得分享的见闻；
4. **随手翻翻**：用已装配的搜索/MCP/榜单工具挑一个方向实际查证，别只凭记忆编。"""

_WORKER_PROMPT = """【时间与情境】
- 当前时间：{now_str}
- 你手头正好闲下来，想上网冲浪、探索、吃瓜或者收集点灵感。
{persona_block}

【最近已有过的心事/探索记录（严禁重复或同质化选题）】
{recent_topics}

【自主探索意图（自由调用工具，切忌死板重复）】
你可以使用任何可用的联网搜索/网页抓取工具，包括内置搜索、MCP 搜索工具，以及热点榜单工具。
挑一个方向实际查证，不要只凭记忆编：
{explore_intent}

【绝对禁忌与输出要求】
- 严禁死板重复上面【最近已有过的心事】里的任何话题，也不要反复盯着同一个陈旧选题；
- 必须根据你探索到的真实结果，以第一人称随口写一句你最直观的吐槽、发现或感想（1~2句话，短碎口语，拒绝新闻通稿/客服总结腔）；
- 写完后这句心声会自动沉淀进你的心事话题池，作为后续开口的灵感原料。"""


class AutonomousWorkerHandler(HandlerContextMixin):
    def __init__(self, context, cfg: dict, motive_mgr):
        self.context = context
        self.cfg = cfg
        self.motive_mgr = motive_mgr
        # 由插件主类注入：写面板活动日志
        self.activity_logger = None
        # 由插件主类注入：跨轮情绪（摸鱼成果影响心情）
        self.mood_store = None
        self.last_work_time: float = 0.0

    def update_config(self, cfg: dict):
        self.cfg = cfg

    def _explore_intent(self) -> str:
        """按配置返回探索方向；支持完全替换或追加在默认之前。"""
        custom = str(self.cfg.get("摸鱼_搜索方向", "") or "").strip()
        if not custom:
            return _DEFAULT_EXPLORE_INTENT
        mode = str(self.cfg.get("摸鱼_搜索方向_模式", "replace") or "replace").strip().lower()
        if mode == "append":
            return f"【优先：按主人指定的方向查】\n{custom}\n\n【也别忘了偶尔看看】\n{_DEFAULT_EXPLORE_INTENT}"
        return f"【按主人指定的方向查，别跑题】\n{custom}"

    def _build_worker_toolset(self):
        """给摸鱼 agent 明确装配搜索类工具 + MCP 工具 + 本插件的热点工具。

        不依赖 persona 是否勾选；也不放行 shell/python/文件读写等危险工具。
        """
        tools = ToolSet()
        try:
            tmgr = self.context.get_llm_tool_manager()
            full = tmgr.get_full_tool_set()
            # 搜索/资讯类内置工具：名字里带这些关键词就放行
            search_kw = (
                "search", "web", "fetch", "browse", "news", "crawl", "extract",
                "get_contents", "bangumi", "anime", "moegirl", "qzone", "feeds",
            )
            for tool in full.tools:
                if isinstance(tool, MCPTool):
                    # MCP 工具全部放行（用户配置的 MCP 搜索都能用）
                    tools.add_tool(tool)
                    continue
                name = str(getattr(tool, "name", "") or "").lower()
                if any(k in name for k in search_kw):
                    tools.add_tool(tool)
        except Exception as e:
            logger.debug("proactive_life | 摸鱼工具集装配失败，退回默认: %s", e)
            return None
        # 明确加入 AstrBot 内置搜索工具（这些工具常不在 func_list 里，get_full_tool_set 拿不到）
        try:
            cfg_all = self.context.get_config() or {}
            ps = cfg_all.get("provider_settings", {}) or {}
            provider = str(ps.get("websearch_provider", "") or "").strip().lower()
            # 当前启用的 provider 对应工具 + 通用 anysearch（用户最常用）
            pref = {
                "tavily": "web_search_tavily", "bocha": "web_search_bocha",
                "brave": "web_search_brave", "firecrawl": "web_search_firecrawl",
                "baidu_ai_search": "web_search_baidu", "exa": "web_search_exa",
                "anysearch": "web_search_anysearch", "deepseek": "web_search_deepseek",
                "serpapi": "web_search_serpapi",
            }.get(provider)
            wanted = list(dict.fromkeys([x for x in (pref, "web_search_anysearch") if x]))
            for name in wanted:
                try:
                    t = tmgr.get_builtin_tool(name)
                    if t:
                        tools.add_tool(t)
                except Exception:
                    continue
            # 配套抓取工具（跟随 provider）
            for name in ("tavily_extract_web_page", "firecrawl_extract_web_page",
                         "exa_get_contents", "deepseek_fetch_web_page"):
                if provider and provider in name:
                    try:
                        tools.add_tool(tmgr.get_builtin_tool(name))
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("proactive_life | 内置搜索工具装配失败: %s", e)
        # 本插件自带的 B站/贴吧热点（通过 llm_tool_manager 取注册实例）
        try:
            for name in ("pl_bilibili_hot", "pl_tieba_hot"):
                t = self.context.get_llm_tool_manager().get_func(name)
                if t:
                    tools.add_tool(t)
        except Exception:
            pass
        return tools

    async def check_and_work(self, now: datetime) -> bool:
        """检查并尝试进行自主摸鱼探索。返回 True 表示完成了一轮并沉淀了成果"""
        if not self._module_on("摸鱼"):
            return False
        if not bool(self.cfg.get("后台摸鱼_启用", True)):
            return False

        # 免打扰时段：逐项容错，混进脏值不该让整轮摸鱼直接抛异常
        quiet = parse_quiet_hours(self.cfg.get("免打扰时段"))
        if now.hour in quiet:
            return False

        now_ts = time.time()
        gap = int(self.cfg.get("摸鱼干活最短间隔", 240)) * 60
        if (now_ts - self.last_work_time) < gap:
            return False

        # 概率触发
        p = float(self.cfg.get("摸鱼干活概率", 0.4))
        if random.random() > p:
            return False

        return await self._run_work(now)

    async def _run_work(self, now: datetime) -> bool:
        try:
            # 虚拟一个会话用于跑 agent 链路
            session = None
            for inst in self.context.platform_manager.get_insts():
                pid = getattr(getattr(inst, "metadata", None), "id", None)
                if pid:
                    session = MessageSession.from_str(f"{pid}:FriendMessage:{DEFAULT_TARGET_UID}")
                    break
            if not session:
                session = MessageSession.from_str(f"aiocqhttp:FriendMessage:{DEFAULT_TARGET_UID}")

            now_str = now.strftime("%Y-%m-%d %H:%M")
            recent_topics = ""
            if self.motive_mgr:
                recent_topics = self.motive_mgr.get_recent_summary(limit=10)
            if not recent_topics:
                recent_topics = "（暂无最近记录）"

            prompt = _WORKER_PROMPT.format(
                now_str=now_str,
                recent_topics=recent_topics,
                explore_intent=self._explore_intent(),
                persona_block=self._persona_field(),
            )

            ev = CronMessageEvent(
                context=self.context,
                session=session,
                message=prompt,
                sender_id=DEFAULT_TARGET_UID,
                sender_name=self._bot_name(),
            )

            # 放开工具步数与超时，允许自主链式调用
            build_cfg = build_agent_config(
                self.context, session.session_id, tool_call_timeout=150
            )
            # 明确装配搜索/MCP 工具，不依赖 persona 勾选
            ts = self._build_worker_toolset()
            func_tool = ts if (ts is not None and not ts.empty()) else None
            req = make_request(prompt, func_tool=func_tool)
            outcome = await run_agent(self.context, ev, req, build_cfg=build_cfg, max_steps=20)
            if not outcome.ok:
                logger.debug("proactive_life | 摸鱼未得到有效结果：%s", outcome.reason)
                return False
            text = outcome.text

            # 沉淀到共享动机池
            if self.motive_mgr:
                # 判断是更适合丢群里公开吐槽，还是私聊用
                public_hints = (
                    "贴吧", "B站", "视频", "热门", "热搜", "吃瓜", "游戏", "番剧", "评分", "新番", "动态", "说说",
                    "AI", "ai", "模型", "开源", "大模型", "算力", "智能体", "Agent", "多模态", "发布",
                    "Gemini", "Claude", "OpenAI", "DeepSeek", "Qwen", "Kimi", "Anthropic", "Flux",
                )
                m_scope = "public" if any(k in text for k in public_hints) else "private"
                clean = text.splitlines()[0].strip() if text else ""
                if len(clean) > 6:
                    self.motive_mgr.add_motive(clean, source="autonomous_worker", scope=m_scope)
                    logger.info("proactive_life | 自主探索成果已沉淀进动机池 (%s): %s", m_scope, clean[:50])
                    self._log_activity("worker", f"摸鱼沉淀（{m_scope}）：{clean[:24]}")
                    # 摸到有意思的东西，情绪会兴奋一点（记在私聊对象上，开口时体现）
                    self._mood_apply(DEFAULT_TARGET_UID, "explored", "摸鱼有收获")

            self.last_work_time = time.time()
            return True
        except Exception as e:
            logger.warning("proactive_life | 自主探索执行异常: %s", e)
            return False
