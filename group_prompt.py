"""群聊：场景感知、说话风格与回复 prompt 组装。

从 group_handler.py 拆出，不改变任何行为。Mixin 方法保持原样，
self 上仍需有 _scene/_last_scene/_scene_analyzer/cfg 等状态。
"""

from __future__ import annotations

import time

from astrbot.api import logger



class GroupPromptMixin:
    """场景与 prompt 组装；由 GroupProactiveHandler 继承。"""

    def _context_aware_active(self) -> bool:
        """检查原 context_aware 外挂是否启用；启用时避免重复注入 conversation_scene。"""
        if not bool(self.cfg.get("场景感知_让位contextaware", True)):
            return False
        try:
            meta = self.context.get_registered_star("astrbot_plugin_context_aware")
            return bool(meta and getattr(meta, "activated", False))
        except Exception:
            return False

    def _build_scene_block(self, origin: str) -> str:
        """用场景感知生成 <conversation_scene>；失败时回空串（降级不影响接话）。"""
        if not self._module_on("上下文"):
            return ""
        if not bool(self.cfg.get("场景感知_启用", True)):
            return ""
        if self._context_aware_active():
            # 原 context_aware 正在注入同类场景，避免 LLM 收到两份重叠提示
            return ""
        try:
            win_size = max(2, int(self._scene_cfg("场景感知_对话窗口", 8) or 8))
            show_flow = bool(self._scene_cfg("场景感知_显示对话流", True))
            strict = bool(self._scene_cfg("场景感知_严格模式", False))
            window = self._scene.recent(origin, win_size + 1)
            if not window:
                return ""
            from copy import copy as _copy
            current = _copy(window[-1])  # 避免严格模式改写污染共享历史
            # 被动接话都是主动插入，直接按主动场景告知模型（不依赖 event extra）
            trigger, desc = "active", "你是主动加入这个对话的，没有人在叫你"
            # 严格模式：不把当前消息推断成「在跟 Bot 说」
            if strict and current.talking_to == "bot":
                current.talking_to, current.talking_to_name = "group", "群聊"
            # 短期总结：注入跨轮笔记，让接话像「记得聊过什么」
            summary_text = ""
            if bool(self._scene_cfg("场景感知_总结启用", True)):
                note = self._summary.get(origin)
                if note and not note.is_empty():
                    summary_text = note.render(
                        max_items=max(2, int(self._scene_cfg("场景感知_总结条数", 6) or 6)),
                    )
            # 成员印象：当前发言人的说话习惯，让回应因人而异
            member_note = ""
            if bool(self._scene_cfg("拟人_成员印象", True)):
                member_note = self._members.render(origin, current.sender_id)
            scene = self._scene_gen.generate(
                trigger, desc, current, window[:-1],
                show_flow=show_flow, summary=summary_text, member_note=member_note,
            )
            # 保存快照：面板用它展示「最近一次注入的上下文」
            self._last_scene[origin] = {
                "at": time.time(),
                "text": scene,
                "trigger": trigger,
                "chars": len(scene),
                "flow_count": (max(0, len(window) - 1) if show_flow else 0),
                "talking_to": current.talking_to_name or current.talking_to,
                "has_summary": bool(summary_text),
                "member_note": member_note,
            }
            return scene
        except Exception as e:
            logger.debug("proactive_life | 场景提示生成失败: %s", e)
            return ""

    def _persona_hint(self) -> str:
        """把拟人参数转成自然语言指令，注入接话 prompt。

        三个维度：话多程度 / 沉默倾向 / 吐槽强度，各自分档给不同指令。
        """
        def clamp(v, default=50):
            try:
                return max(0, min(100, int(v)))
            except (TypeError, ValueError):
                return default

        talk = clamp(self._scene_cfg("拟人_话多程度", 50))
        quiet = clamp(self._scene_cfg("拟人_沉默倾向", 45))
        snark = clamp(self._scene_cfg("拟人_吐槽强度", 50))

        # 话多程度：影响句数与展开意愿
        if talk <= 25:
            length = "只说最短的一句，能一个词就别一句话，绝不展开。"
        elif talk <= 50:
            length = "一两短句说完就停，不主动展开话题。"
        elif talk <= 75:
            length = "可以聊两句，接梗后顺带补一句自己的看法，但仍别写长段落。"
        else:
            length = "话可以多一点，接梗后多聊两句、顺着话题延伸，但也别写成小作文。"

        # 沉默倾向：影响「没话说时」的态度
        if quiet >= 75:
            attitude = (
                "没有真正想说的就别开口；群聊是大家的，你不挨个回答。"
                "宁可跳过也不硬凑，只挑真有意思或真想说的那句接。"
            )
        elif quiet >= 50:
            attitude = "只在真有话想说、或话题你真有兴趣时接；没想法就安静。"
        elif quiet >= 25:
            attitude = "多数时候自然搭一句，只有气氛明显不适合时才沉默。"
        else:
            attitude = "乐于搭话，看到消息就自然接一句，别让人觉得你冷淡。"

        # 吐槽强度：影响语气
        if snark >= 75:
            tone = "嘴毒一点，爱吐槽抖机灵，但也别真伤人——玩笑式吐槽，不是攻击。"
        elif snark >= 50:
            tone = "可以随口吐槽、带点调侃，语气轻松不认真说教。"
        elif snark >= 25:
            tone = "温和吐槽就好，以附和、认同为主，偶尔调侃一句。"
        else:
            tone = "温和友善，以附和、鼓励为主，避免任何可能让人不舒服的调侃。"

        return (
            "【说话风格（按这组参数来，优先级高于一般客套）】\n"
            f"- 篇幅：{length}\n"
            f"- 开口意愿：{attitude}\n"
            f"- 语气：{tone}"
        )

    def _build_passive_prompt(
        self, origin: str, msg_str: str, is_responding_to_bot: bool, current_sender: str
    ) -> str:
        """依据上下文组出接话 prompt，不碰事件与状态。

        融入场景感知：<conversation_scene> 告诉模型谁在跟谁说话、它是不是被叫，
        避免把别人之间的对话当成问自己（抢答）；再叠加反应多样性与护栏。
        """
        scene = self._build_scene_block(origin)
        mood_part = self._mood_hint(origin)

        # 反应方式多样性 + 拟人参数（话多/沉默/吐槽）共同决定怎么说
        reaction_hint = (
            "怎么回应自己挑，别每次都一个样：\n"
            "- 好笑的：接梗、抖一句、跟着哈哈；\n"
            "- 有同感的：附和一句，站队或吐槽，不用长篇；\n"
            "- 有看法的：说句真心话或主观吐槽，说完就停；\n"
            "- 没啥好说的：一句语气词（啊 / 草 / 啊这 / 嗯），或按下面的沉默倾向处理。\n\n"
            + self._persona_hint()
        )

        scene_part = ((scene + "\n\n") if scene else "") + mood_part
        if is_responding_to_bot:
            bot_last_words = self._recent_bot_spoke.get(origin, {}).get("text", "")
            return (
                scene_part +
                f"你刚才在群里说的是：「{bot_last_words}」\n"
                f"{current_sender} 接着说：{msg_str}\n\n"
                f"{reaction_hint}\n\n"
                f"{self._GUARDRAIL}\n\n"
                f"他在接你的话。像真人一样自然回一句就行，不用解释、不用报幕。"
            )
        return (
            scene_part +
            f"{current_sender} 刚说：{msg_str}\n\n"
            f"{reaction_hint}\n\n"
            f"{self._GUARDRAIL}\n\n"
            f"你在旁边看聊天，挑一个此刻最自然的反应说出来，哪怕只是一个语气词。"
            f"先看清场景提示：如果这条明显是在跟别人说话，别抢答。"
            f"只输出要说的话本身。"
        )
