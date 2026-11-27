"""戳一戳 / 纯艾特 / 群聊跟读的正文实现。

为什么逻辑放这里、装饰器留 main.py：
AstrBot 的 ``star_manager`` 按 ``handler_module_path`` **精确匹配**来给 handler
绑定插件实例，装饰器一旦定义在子模块里就再也绑不上（调用时第一个参数会变成
事件对象而不是插件实例）。所以 main.py 里保留 ``@filter`` 薄壳，只做
``async for ... yield`` 转发。
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ..common import canonical_group_key


async def handle_group_reread(plugin, event):
    """群聊跟读：尾 N 条相同短消息时自然复读；闭嘴期/@ 消息绝不触发。"""
    if plugin._stop_event.is_set() or not plugin._module_on("跟读"):
        return
    _sync_cfg = getattr(plugin, "_sync_runtime_state", None)
    if callable(_sync_cfg):
        _sync_cfg()
    try:
        if getattr(event, "is_at_or_wake_command", False):
            return
        gid = str(getattr(event, "get_group_id", lambda: "")() or "")
        if not gid or plugin.group_h._is_muted(gid):
            return
        from .reread import RereadState, make_fingerprint

        state = getattr(plugin, "_reread_state", None)
        if state is None:
            state = RereadState(
                int(plugin.cfg.get("跟读_阈值", 2) or 2),
                float(plugin.cfg.get("跟读_最小间隔秒", 8) or 8),
            )
            plugin._reread_state = state
        # 屏蔽词
        text = str(getattr(event, "message_str", "") or "")
        blocked = [str(x) for x in (plugin.cfg.get("跟读_屏蔽词") or []) if str(x).strip()]
        if any(w in text for w in blocked):
            return
        fp = make_fingerprint(list(getattr(event, "get_messages", lambda: [])() or []))
        if not fp:
            return
        async with state.lock:
            candidate = state.observe(gid, fp)
            if not candidate:
                return
            import random

            if random.random() > float(plugin.cfg.get("跟读_概率", 0.35) or 0.35):
                return
            state.mark_handled(gid, candidate)
        # 复用原消息文本复读，不走拟人管线（避免二次润饰）
        from astrbot.api.message_components import Plain

        yield event.chain_result([Plain(text)])
        # 复读已成事实：截断事件，避免同一条消息又走群聊插话再回一次
        event.stop_event()
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 跟读处理异常，按未触发跳过: %s", e)


async def handle_poke_event(plugin, event):
    """戳一戳 / 纯艾特：共用一套冷却与情绪逻辑，附带该用户的上下文与图片。

    情景 A：用户先说话/发图（忘 @），随后补 @ 或戳 → 带前面的内容一起回。
    情景 B：先 @ / 戳，后续发消息 → 记 pending，由 on_group_message 合并发出。
    """
    if plugin._stop_event.is_set() or not plugin._module_on("戳一戳"):
        return
    _sync_cfg = getattr(plugin, "_sync_runtime_state", None)
    if callable(_sync_cfg):
        _sync_cfg()
    # 戳一戳事件里就带着协议端实例，顺手把出站 tap 挂上
    plugin._ensure_echo_tap(event)
    try:
        from .poke import PokeCooldown, parse_poke
        from .interact_context import PendingTrigger, build_prompt, is_pure_at

        # 先解析 poke：别人之间互相戳时走「跟戳」凑一脚，不进入回应链路
        ok, bot_id, poker, target, poke_group = parse_poke(event)
        if ok and bot_id and target and target != bot_id:
            from .follow_poke import maybe_follow_poke

            await maybe_follow_poke(plugin, event, poke_group, target, poker)
            return

        hit = bool(ok and bot_id and target and target == bot_id)
        sender = poker
        group = poke_group
        kind = ""
        if hit and sender:
            kind = "poke"
        else:
            # 纯艾特：消息组件全是 @Bot/@全体，没有任何文字/图片
            if bool(plugin.cfg.get("戳一戳_纯艾特回应", True)):
                try:
                    bot = str(event.get_self_id() or "")
                    msgs = list(event.get_messages() or [])
                    if is_pure_at(msgs, bot):
                        hit = True
                        kind = "at"
                        sender = str(event.get_sender_id() or "")
                        group = str(event.get_group_id() or "")
                except Exception:  # noqa: BLE001
                    pass
        if not hit or not sender:
            return
        # 黑名单用户直接忽略
        if plugin._module_on("黑名单") and plugin.blacklist.is_blacklisted_event(event):
            return
        cooldown = getattr(plugin, "_poke_cd", None)
        if cooldown is None:
            cooldown = PokeCooldown(
                float(plugin.cfg.get("戳一戳_个人冷却秒", 30) or 30),
                float(plugin.cfg.get("戳一戳_群冷却秒", 60) or 60),
            )
            plugin._poke_cd = cooldown
        try:
            admin = bool(event.is_admin())
        except Exception:  # noqa: BLE001
            admin = False
        if cooldown.is_cooling(sender, group, is_admin=admin):
            return
        cooldown.mark(sender, group)
        if plugin._module_on("上下文"):
            try:
                # 群聊情绪按裸群号记（被动注入 / 面板都按这个 Key 读），
                # 私聊才用 sender；以前一律写 sender，群情绪永远读不到。
                mood_key = canonical_group_key(group) if group else str(sender or "")
                if mood_key:
                    plugin.mood.apply(mood_key, "replied", "被戳/艾特了一下")
            except Exception:  # noqa: BLE001
                pass
        import random

        prob_key = "戳一戳_纯艾特概率" if kind == "at" else "戳一戳_回应概率"
        if random.random() > float(plugin.cfg.get(prob_key, 0.7) or 0.7):
            return

        # 两条分支互斥，避免「既立刻回、又被后续消息再触发一次」的双回复：
        #   情景 A：前面有内容 → 本次直接带上下文回复，不埋 pending
        #   情景 B：前面没内容 → 埋 pending，等后面那条消息一起合并回复，本次不回
        #
        # 纯艾特的额外一层：群里一直有人聊天时 recent 永远非空，只按「窗口有没有
        # 东西」判断会让纯 @ 被别人的闲聊顶成情景 A，抢答一句跟眼前无关的话，
        # 用户随后补的正文反而被当成普通群消息漏掉。所以纯 @ 且自己此前没说话时，
        # 先挂起一小会儿等正文补上（等待秒数可配，0 = 关闭该行为）。
        ctx = getattr(plugin, "interact_ctx", None)
        items = ctx.recent(group, exclude_sender="") if (ctx and group) else []

        event.set_extra("_poke_trigger", True)
        event.set_extra("_poke_sender_id", sender)

        waited = False
        wait_sec = float(plugin.cfg.get("戳一戳_纯艾特等待秒", 3) or 0)
        if (
            kind == "at"
            and ctx is not None
            and group
            and wait_sec > 0
            and bool(plugin.cfg.get("戳一戳_后续合并", True))
            and not ctx.same_sender(group, sender)
        ):
            ctx.set_pending(group, kind, sender)
            await asyncio.sleep(wait_sec)
            waited = True
            if not ctx.has_pending(group):
                # 等待期间用户补了正文，已由 on_group_message 合并回复；
                # 事件本身还带着 is_at_or_wake_command，必须截断，
                # 否则原生链路会再回一次，等于双回复。
                event.stop_event()
                return
            ctx.pop_pending(group)
            items = ctx.same_sender(group, sender)
        elif kind == "at" and ctx is not None and group:
            # 自己刚说过话：只带他自己的那几条，别把别人的闲聊当接话依据
            items = ctx.same_sender(group, sender) or items

        if not items and not waited:
            # 情景 B：没有任何上下文，等用户补正文/图片再一起回。
            # 只对群聊成立：私聊没有「后续合并」这回事，走原来的 return
            # 会让私聊被戳永远哑火（group 为空 → items 恒为空 → 直接退出）。
            if group and ctx is not None and bool(plugin.cfg.get("戳一戳_后续合并", True)):
                ctx.set_pending(group, kind, sender)
                if kind == "at":
                    # 同上：纯 @ 不截断事件会被原生链路再回一次
                    event.stop_event()
                return
            logger.debug("proactive_life | 戳/艾特无上下文（私聊或已关合并），直接回应")

        # 情景 A：带着前面的上下文现在就回
        trigger_ref = PendingTrigger(kind=kind, sender=sender, group=group)
        prompt, images = build_prompt(kind, trigger_ref, items, bot_name=plugin.group_h._bot_name())
        req = event.request_llm(prompt=prompt)
        if images:
            try:
                existing = list(getattr(req, "image_urls", None) or [])
                req.image_urls = list(dict.fromkeys(existing + images))
            except Exception:  # noqa: BLE001
                pass
        yield req
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 戳一戳/纯艾特处理异常，按未触发跳过: %s", e)
