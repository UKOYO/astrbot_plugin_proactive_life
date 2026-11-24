// 自主生活 · 面板前端
// 所有数据都经 AstrBot 插件页 Bridge 走后端接口，页面本身不直连。
(function () {
  "use strict";

  const API = "panel";
  const REFRESH_MS = 4000;

  // 后端值到中文显示的对照；保持界面不出现英文
  const MODE_NAMES = { split: "拆条", punctuation: "仅去标点", trim: "截断" };
  const SCOPE_NAMES = { public: "公开", private: "私密" };

  const LABELS = {
    "角色_AstrBot人设": "选用人设",
    "角色_插件提示词模型": "提示词生成模型",
    "角色_插件专用提示词": "插件专用提示词",
    "角色_自动读取AstrBot人设": "自动读取 AstrBot 人设",
    "角色_Bot自称": "Bot 自称",
    "角色_对方称呼": "对方称呼",
    "角色_备用称呼": "备用称呼",
    "角色_生活背景": "生活背景",
    "角色_兴趣关键词": "兴趣关键词",
    "角色_额外人设": "额外人设",
    "模块_私聊": "私聊模块总开关",
    "模块_群聊": "群聊模块总开关",
    "模块_摸鱼": "摸鱼模块总开关",
    "模块_节奏": "节奏模块总开关",
    "模块_日程": "日程模块总开关",
    "模块_上下文": "上下文模块总开关",
    "模块_防抖": "防抖模块总开关",
    "防抖_群聊启用": "群聊防抖",
    "防抖_私聊启用": "私聊防抖",
    "防抖_群聊等待秒": "群聊静默等待 · 秒",
    "防抖_群聊最长等待秒": "群聊最长等待 · 秒",
    "防抖_私聊等待秒": "私聊静默等待 · 秒",
    "防抖_私聊最长等待秒": "私聊最长等待 · 秒",
    "防抖_单次图片上限": "单次图片上限 · 张",
    "防抖_情绪拆分": "情绪倾诉单独成批",
    "防抖_过滤表情包": "过滤无意义表情包",
    "防抖_锁超时秒": "会话锁超时 · 秒",
    "全局调度间隔": "调度间隔 · 分",
    "免打扰时段": "免打扰 · 小时",
    "私聊主动_启用": "私聊主动",
    "私聊追问_启用": "私聊追问",
    "对谁主动": "对谁主动 · UID",
    "最短间隔": "私聊最短间隔 · 分",
    "每天最多几次": "私聊每日上限",
    "触发概率": "私聊触发概率",
    "安静多久才找": "安静多久才找 · 分",
    "追问等待秒数": "追问等待 · 秒",
    "追问过期秒数": "追问过期 · 秒",
    "追问最多次数": "追问最多几次",
    "参考几条": "参考几条上下文",
    "参考日程": "参考日程",
    "日程生成时间": "生成时刻 · HH:MM",
    "日程_自动生成": "每日自动生成",
    "日程_参考历史天数": "参考历史 · 天",
    "日程_参考最近条数": "参考聊天 · 条",
    "日程_生成模型": "生成模型",
    "日程_穿搭风格池": "服装库 · 穿搭风格",
    "日程_提示词模板": "提示词模板",
    "参考最近聊天": "参考最近聊天",
    "群聊自发破冰_启用": "群聊自发破冰",
    "群聊冷场判定时间": "冷场判定 · 分",
    "群聊自发破冰概率": "破冰概率",
    "群聊插话_启用": "群聊插话",
    "群聊插话_模式": "插话模式",
    "群聊插话_概率": "插话概率",
    "群聊插话_冷却时间": "插话冷却 · 秒",
    "群聊接话_最短间隔": "接话最短间隔 · 秒",
    "群聊插话_饥饿保底条数": "饥饿保底 · 条",
    "群聊插话_保底最短间隔": "保底最短间隔 · 分",
    "群聊插话_判定窗口": "判定窗口 · 秒",
    "群聊插话_闭嘴静默分钟": "闭嘴静默 · 分",
    "群聊插话_嫌烦静默分钟": "嫌烦静默 · 分",
    "群聊插话_诊断日志": "插话诊断日志",
    "群聊白名单": "群聊白名单",
    "群聊白名单_概率倍数": "白名单概率倍数",
    "群聊判定模型": "群聊判定模型",
    "群聊命中词_启用": "命中词唤醒",
    "群聊命中词_关键词": "命中词",
    "群聊命中词_接话概率": "命中后接话概率",
    "群聊命中词_最短间隔秒": "命中最短间隔 · 秒",
    "群聊命中词_需包含称呼": "命中需带称呼",
    "群聊独立分区配置": "群专属分区",
    "场景感知_启用": "场景感知 · 防抢答",
    "场景感知_让位contextaware": "让位原 context_aware",
    "场景感知_对话窗口": "对话流窗口 · 条",
    "场景感知_显示对话流": "显示对话流",
    "场景感知_严格模式": "严格模式",
    "场景感知_回复特征词": "回复特征词",
    "场景感知_调试推断": "调试推断",
    "场景感知_总结启用": "短期总结 · 记住聊过什么",
    "场景感知_总结每N条": "总结触发 · 每N条",
    "场景感知_总结间隔秒": "总结最小间隔 · 秒",
    "场景感知_总结条数": "注入笔记 · 每类条数",
    "场景感知_总结模型": "总结模型",
    "拟人_话多程度": "话多程度 · 0-100",
    "拟人_沉默倾向": "沉默倾向 · 0-100",
    "拟人_吐槽强度": "吐槽强度 · 0-100",
    "拟人_成员印象": "成员印象 · 因人而异",
    "拟人_情绪启用": "情绪状态 · 跨轮延续",
    "拟人_情绪衰减小时": "情绪衰减 · 小时",
    "拟人_动机多样化": "开口动机多样化",
    "后台摸鱼_启用": "后台摸鱼",
    "摸鱼干活最短间隔": "摸鱼最短间隔 · 分",
    "摸鱼干活概率": "摸鱼概率",
    "摸鱼_搜索方向": "自定义搜索方向",
    "摸鱼_搜索方向_模式": "方向叠加方式",
    "优先消费心事动机": "优先消费心事",
    "节奏_启用": "节奏修剪",
    "节奏_处理方式": "修剪方式",
    "节奏_每段字数": "每段字数",
    "节奏_最多几条": "最多几条",
    "节奏_间隔秒数": "补发间隔 · 秒",
    "节奏_去句末标点": "去句末标点",
    "节奏_去颜文字": "去颜文字",
    "节奏_表格代码不拆": "表格代码不拆",
    "节奏_花括号不拆": "花括号不拆",
    "节奏_打印处理详情": "打印处理详情",
    "节奏_只对群聊": "只对群聊",
    "节奏_演练模式": "演练模式",
    "模拟分段打字": "模拟分段打字",
    "分段发送延迟": "分段延迟 · 秒",
  };

  // 配置栏位归到独立分頁（对应 HTML 的 data-view / data-conf）
  const PRIVATE_KEYS = ["私聊主动_启用", "私聊追问_启用", "对谁主动", "最短间隔", "每天最多几次", "触发概率", "安静多久才找", "追问等待秒数", "追问过期秒数", "追问最多次数", "参考几条", "参考日程", "参考最近聊天"];
  const CORE_KEYS = ["全局调度间隔", "免打扰时段"];
  const WORKER_KEYS = ["后台摸鱼_启用", "摸鱼干活最短间隔", "摸鱼干活概率", "优先消费心事动机"];
  const VOICE_KEYS = ["模拟分段打字", "分段发送延迟"];
  // 这些键用多行文本框编辑
  const TEXTAREA_KEYS = ["日程_提示词模板", "角色_生活背景", "角色_额外人设", "角色_插件专用提示词"];
  const MODULE_GROUP = {
    "模块_私聊": "private", "模块_群聊": "group", "模块_摸鱼": "worker",
    "模块_节奏": "rhythm", "模块_日程": "schedule", "模块_上下文": "context",
    "模块_防抖": "debounce", "模块_身份": "identity",
  };
  const GROUP_OF = (key) => {
    if (MODULE_GROUP[key]) return MODULE_GROUP[key];
    if (key.startsWith("角色_")) return "identity";
    if (key === "日程生成时间" || key.startsWith("日程_")) return "schedule";
    if (key.startsWith("防抖_")) return "debounce";
    if (key.startsWith("场景感知_") || key.startsWith("拟人_")) return "context";
    if (CORE_KEYS.includes(key)) return "core";
    if (PRIVATE_KEYS.includes(key)) return "private";
    if (key.startsWith("群聊")) return "group";
    if (WORKER_KEYS.includes(key) || key.startsWith("摸鱼_")) return "worker";
    if (key.startsWith("节奏_") || VOICE_KEYS.includes(key)) return "rhythm";
    return "core";
  };

  const el = {
    loopDot: document.getElementById("loopDot"),
    loopText: document.getElementById("loopText"),
    mNext: document.getElementById("mNext"),
    mInterval: document.getElementById("mInterval"),
    mQuiet: document.getElementById("mQuiet"),
    mNow: document.getElementById("mNow"),
    privateList: document.getElementById("privateList"),
    privateHint: document.getElementById("privateHint"),
    groupBody: document.getElementById("groupBody"),
    groupHint: document.getElementById("groupHint"),
    groupSec: document.getElementById("groupSec"),
    groupFold: document.getElementById("groupFold"),
    rhythm: document.getElementById("rhythm"),
    worker: document.getElementById("worker"),
    confForms: Array.from(document.querySelectorAll("form.conf")),
    toast: document.getElementById("toast"),
    footNote: document.getElementById("footNote"),
    footTime: document.getElementById("footTime"),
    activityList: document.getElementById("activityList"),
    conflictBox: document.getElementById("conflictBox"),
    debounceStats: document.getElementById("debounceStats"),
    identityView: document.getElementById("identityView"),
    personaExtra: document.getElementById("personaExtra"),
    scheduleView: document.getElementById("scheduleView"),
    scheduleHint: document.getElementById("scheduleHint"),
    scheduleExtra: document.getElementById("scheduleExtra"),
    scheduleOutfit: document.getElementById("scheduleOutfit"),
    scheduleText: document.getElementById("scheduleText"),
    scheduleSave: document.getElementById("scheduleSave"),
    zoomIn: document.getElementById("zoomIn"),
    zoomOut: document.getElementById("zoomOut"),
    zoomVal: document.getElementById("zoomVal"),
  };

  let bridge = null;
  let timer = null;
  let confBuilt = false;
  let refreshing = false;      // 防止上次请求没回来又发一次，避免渲染错乱
  let lastSchedule = null;     // 最近一次拿到的日程，保存时带日期/风格
  let lastErrorAt = 0;         // 错误 toast 节流：后端短暂挂掉时不要每 4 秒弹一次

  // ------------------------------------------------------------ 粒子背景
  // 原生 Canvas 2D 画的旋转粒子云：几条螺旋臂 + 缓慢自转 + 轻微闪烁。
  // 不依赖任何外部库，离线也能跑；尊重系统减少动态效果偏好。
  function initParticleBackground() {
    const canvas = document.getElementById("bgCanvas");
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext("2d");
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, dpr = 1;
    let particles = [];
    let raf = null;
    let angle = 0;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
    }

    function build() {
      const count = Math.min(1500, Math.max(500, Math.floor((w * h) / 1400)));
      particles = [];
      const arms = 3;
      for (let i = 0; i < count; i++) {
        // 螺旋臂 + 随机散布
        const arm = i % arms;
        const t = Math.random();
        const radius = Math.pow(t, 0.6) * Math.max(w, h) * 0.62;
        const spread = (Math.random() - 0.5) * (0.35 + radius / (w + h));
        const base = arm * ((Math.PI * 2) / arms);
        const a = base + radius * 0.004 + spread;
        particles.push({
          x: Math.cos(a) * radius,
          y: Math.sin(a) * radius * 0.72, // 稍微压扁，像星云
          r: Math.random() * 1.1 + 0.3,
          alpha: Math.random() * 0.5 + 0.15,
          tw: Math.random() * Math.PI * 2, // 闪烁相位
        });
      }
    }

    function color() {
      // 跟随主题：深色用亮点，浅色用暗点，与参考站的反色一致
      const dark = document.documentElement.dataset.theme === "dark" ||
        (!document.documentElement.dataset.theme &&
         window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
      return dark ? "255,255,255" : "0,0,0";
    }

    function frame(now) {
      ctx.clearRect(0, 0, w, h);
      const c = color();
      if (!reduceMotion) angle += 0.00035;
      const cos = Math.cos(angle), sin = Math.sin(angle);
      const cx = w * 0.5, cy = h * 0.52;
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        // 绕中心旋转
        const x = p.x * cos - p.y * sin + cx;
        const y = p.x * sin + p.y * cos + cy;
        if (x < -4 || x > w + 4 || y < -4 || y > h + 4) continue;
        const flicker = reduceMotion ? 1 : 0.75 + 0.25 * Math.sin(now * 0.001 + p.tw);
        ctx.beginPath();
        ctx.fillStyle = `rgba(${c},${(p.alpha * flicker).toFixed(3)})`;
        ctx.arc(x, y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = window.requestAnimationFrame(frame);
    }

    resize();
    window.addEventListener("resize", resize);
    if (reduceMotion) {
      frame(0); // 静态渲染一帧
      if (raf) window.cancelAnimationFrame(raf);
    } else {
      raf = window.requestAnimationFrame(frame);
    }
    // 页面隐藏时停掉，省电
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (raf) window.cancelAnimationFrame(raf);
        raf = null;
      } else if (!raf && !reduceMotion) {
        raf = window.requestAnimationFrame(frame);
      }
    });
  }

  // ------------------------------------------------------------ 主题开关
  function initThemeSwitch() {
    const root = document.documentElement;
    const buttons = document.querySelectorAll(".theme-btn");
    function apply(mode) {
      if (mode === "mono") {
        document.body.classList.add("is-monospaced");
        // 等宽只切换字体，主题保持现状
        return;
      }
      document.body.classList.remove("is-monospaced");
      root.dataset.theme = mode;
      buttons.forEach((b) => b.classList.toggle("is-selected", b.dataset.themeSet === mode));
    }
    buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.themeSet)));
  }

  // ------------------------------------------------------------ 滚动进度
  function initScrollWatch() {
    const bar = document.getElementById("scrollBar");
    if (!bar) return;
    function update() {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const p = max > 0 ? window.scrollY / max : 0;
      bar.style.height = `${Math.min(100, Math.max(0, p * 100))}%`;
    }
    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  // ------------------------------------------------------------ 工具

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch];
    });
  }

  function yn(flag) {
    return flag ? "是" : "否";
  }

  // 秒数换成 mm:ss，超过一小时退回 xx 小时
  function dur(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total === 0) return "—";
    if (total >= 3600) {
      const h = Math.floor(total / 3600);
      const m = Math.round((total % 3600) / 60);
      return m ? `${h} 时 ${m} 分` : `${h} 时`;
    }
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  // 把免打扰小时列表合并成可读时段：[2,3,4,5,6] -> "02:00–07:00"
  // 每个小时是「这一小时静默」，结束点取最后一个小时 +1。
  function quietText(hours) {
    const hs = (hours || []).map((h) => Number(h)).filter((h) => Number.isFinite(h) && h >= 0 && h <= 23);
    if (!hs.length) return "无（全天可活动）";
    const sorted = Array.from(new Set(hs)).sort((a, b) => a - b);
    // 切成连续段
    const ranges = [];
    let start = sorted[0], prev = sorted[0];
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] === prev + 1) { prev = sorted[i]; continue; }
      ranges.push([start, prev]);
      start = sorted[i]; prev = sorted[i];
    }
    ranges.push([start, prev]);
    return ranges.map(([a, b]) => {
      const endH = (b + 1) % 24;
      return `${pad2(a)}:00–${pad2(endH)}:00`;
    }).join("、");
  }

  // 相对时间：群里最后一条消息是多久以前
  function ago(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    if (s < 60) return "刚刚";
    if (s < 3600) return `${Math.floor(s / 60)} 分前`;
    if (s < 86400) return `${Math.floor(s / 3600)} 时前`;
    return `${Math.floor(s / 86400)} 天前`;
  }

  // 分钟级的门槛用「分」说，秒级倒计时才用 mm:ss
  function mins(seconds) {
    const m = Math.round((Number(seconds) || 0) / 60);
    if (m >= 60) {
      const h = Math.floor(m / 60);
      const rest = m % 60;
      return rest ? `${h} 时 ${rest} 分` : `${h} 时`;
    }
    return `${m} 分`;
  }

  // 概率（0~1）换成百分比，保留一位小数；0.35 -> 35%
  function pct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const p = n * 100;
    return `${Number.isInteger(p) ? p : p.toFixed(1)}%`;
  }

  function toast(text, isError) {
    el.toast.textContent = text || "";
    el.toast.style.color = isError ? "var(--warn)" : "var(--accent)";
    if (text) {
      window.clearTimeout(toast._t);
      toast._t = window.setTimeout(() => {
        el.toast.textContent = "";
      }, 4200);
    }
  }

  // 定时轮询的错误提示节流：同一个错在 15 秒内只弹一次
  function toastThrottled(text) {
    const now = Date.now();
    if (now - lastErrorAt < 15000) return;
    lastErrorAt = now;
    toast(text, true);
  }

  async function getBridge() {
    const deadline = Date.now() + 5000;
    while (!window.AstrBotPluginView && Date.now() < deadline) {
      await new Promise((r) => window.setTimeout(r, 100));
    }
    const api = window.AstrBotPluginView || window.AstrBotPluginPage;
    if (!api) {
      throw new Error("Bridge 未就绪，请从 AstrBot 后台的插件页面打开");
    }
    await api.ready();
    return api;
  }

  // ------------------------------------------------------------ 渲染

  function renderStatus(s) {
    const loop = s.loop || {};
    el.loopDot.dataset.state = loop.running ? "on" : "off";
    el.loopText.textContent = loop.running
      ? loop.in_quiet
        ? "中枢运行中 · 静默时段"
        : "中枢运行中"
      : "中枢未运行";

    el.mNext.innerHTML = loop.running
      ? loop.next_tick_in > 0
        ? `${mins(loop.next_tick_in)}<small>后</small>`
        : `即将<small>轮询</small>`
      : "—";
    el.mInterval.innerHTML = `${loop.interval_min || 0}<small>分</small>`;

    el.mQuiet.innerHTML = `<span class="metric__quiet">${esc(quietText(loop.quiet_hours))}</span>`;
    if (loop.in_quiet) {
      el.mQuiet.innerHTML += `<small>剩 ${dur((loop.quiet_left_hours || 0) * 60)}</small>`;
    }
    el.mNow.textContent = s.now || "—";

    renderPrivate(s.private || {});
    renderGroup(s.group || {});
    renderRhythm(s.rhythm || {});
    renderWorker(s.worker || {}, s.motive || {});
    renderContext(s.context || {});
    renderIdentity(s.identity || {});
    renderDebounce(s.debounce || {});
    renderConflicts(s.conflicts || []);
    renderActivity(s.activity || []);
    renderSchedule(s.schedule || {});

    el.footTime.textContent = s.now || "—";
  }

  function renderPrivate(p) {
    const users = p.users || [];
    el.privateHint.textContent = p.enabled
      ? `概率 ${pct(p.probability)} · ${users.length} 位`
      : "已关闭";

    if (!users.length) {
      el.privateList.innerHTML =
        '<article class="card"><span class="card__uid">—</span>' +
        '<span class="card__state">未配置对谁主动</span></article>';
      return;
    }

    el.privateList.innerHTML = users
      .map((u) => {
        const first = (u.blockers || [])[0];
        const state = u.ready
          ? "可开口"
          : first
            ? `${first.label} ${first.text || mins(first.left)}`
            : "等待";
        const pending = u.pending
          ? `<div class="card__pending">待追问 <q>${esc(u.pending.question)}</q><br>` +
            `已催 ${u.pending.nudged_count}/${u.pending.max_nudge} · ` +
            `${
              u.pending.expire_in > 0
                ? `还有 ${mins(u.pending.expire_in)} 过期`
                : "已过期"
            }` +
            (u.pending.due_in > 0 ? ` · ${mins(u.pending.due_in)}后该催` : "") +
            `</div>`
          : "";
        return (
          `<article class="card">` +
          `<span class="card__uid">${esc(u.name || u.uid)}${
            u.name ? `<small>${esc(u.uid)}</small>` : ""
          }</span>` +
          `<span class="card__state" data-ready="${u.ready ? "yes" : "no"}">${esc(state)}</span>` +
          `<div class="card__meta">` +
          `<span><b>来信</b>${esc(u.last_user_msg || "—")}</span>` +
          `<span><b>开口</b>${esc(u.last_active || "—")}</span>` +
          `<span><b>今日</b>${u.daily_count}/${u.daily_cap}${
            u.daily_count > u.daily_cap ? " 已超" : ""
          }</span>` +
          (u.mood && u.mood.name && u.mood.name !== "平静" && u.mood.intensity > 0.15
            ? `<span class="card__mood"><b>情绪</b>${esc(u.mood.name)} ${Math.round(u.mood.intensity * 100)}%</span>`
            : "") +
          `</div>${pending}</article>`
        );
      })
      .join("");
  }

  function renderGroup(g) {
    const groups = g.groups || [];
    const bits = [];
    if (g.icebreak_enabled) bits.push(`破冰 ${pct(g.icebreak_probability)}`);
    if (g.interject_enabled) bits.push(`插话 ${pct(g.interject_probability)}`);
    bits.push(`冷却 ${g.cooldown} 秒`);
    el.groupHint.textContent = `${bits.join(" · ")} · ${groups.length} 群`;

    if (!groups.length) {
      el.groupBody.innerHTML =
        '<tr><td colspan="7" class="empty">还没有群聊记录</td></tr>';
      return;
    }

    el.groupBody.innerHTML = groups
      .map(
        (item) =>
          `<tr>` +
          `<td class="gname">${esc(item.name || item.session)}${
            item.name ? `<small>${esc(item.session)}</small>` : ""
          }</td>` +
          `<td>${esc(item.last_spoke || "未开口")}</td>` +
          `<td>${
            item.cooldown_left == null
              ? "待破冰"
              : item.cooldown_left > 0
                ? dur(item.cooldown_left)
                : "就绪"
          }</td>` +
          `<td class="${item.muted_left > 0 ? "muted" : ""}">${
            item.muted_left > 0 ? dur(item.muted_left) : "正常"
          }</td>` +
          `<td>${item.miss_streak}</td>` +
          `<td>${item.cache_size}</td>` +
          `<td>${item.active_ago == null ? "—" : ago(item.active_ago)}</td>` +
          `</tr>`,
      )
      .join("");
  }

  function renderRhythm(r) {
    const last = r.last;
    let lastBlock = '<div class="rhythm__last">还没跑过</div>';
    if (last) {
      const lens = (last.lens || []).length
        ? ` [${last.lens.join(" · ")}]`
        : "";
      const hits = (last.hits || []).length
        ? `修饰 ${last.hits.join("、")}`
        : "无改动";
      lastBlock =
        `<div class="rhythm__last">最近一条 ${last.before} 字 → ${last.segments} 条${lens}\n${hits}</div>`;
    }

    el.rhythm.innerHTML =
      row("开关", `${r.enabled ? "开" : "关"} · ${MODE_NAMES[r.mode] || r.mode}`) +
      row("分段", `${r.target} 字 / 最多 ${r.max_segments} 条`) +
      row("补发间隔", `${r.gap} 秒`) +
      row("去标点 / 去颜文字", `${yn(r.strip_punct)} / ${yn(r.strip_faces)}`) +
      row("只对群聊 / 演练", `${yn(r.group_only)} / ${yn(r.dry_run)}`) +
      lastBlock +
      '<div class="rhythm__note">处理详情为最近一条消息的结果</div>';
  }

  function renderWorker(w, m) {
    const items = (m.items || [])
      .slice(-3)
      .reverse()
      .map(
        (it) =>
          `<div class="worker__memo"><b>${esc(SCOPE_NAMES[it.scope] || it.scope || "—")}</b><span>${esc(
            it.text,
          )}</span></div>`,
      )
      .join("");

    el.worker.innerHTML =
      row("开关", w.enabled ? "开" : "关") +
      row("最短间隔", `${w.min_interval} 分`) +
      row("概率", pct(w.probability)) +
      row("心事池", `${m.count || 0} 条`) +
      items;
  }

  // 上下文/场景感知：展示实际注入的上下文统计与最近一次场景预览
  function renderContext(c) {
    const box = document.getElementById("contextStats");
    if (!box) return;
    const stats =
      row("追踪会话", `${c.sessions || 0} 个群`) +
      row("缓存消息", `${c.tracked_messages || 0} 条（每群上限 ${c.cache_cap || 40}）`) +
      row("本次注入", `${c.inject_window || 8} 条对话流`);
    let latestBlock = '<div class="context__empty">还没有群聊接话，暂无注入快照</div>';
    const l = c.latest;
    if (l) {
      const TRIGGER_NAMES = {
        active: "主动加入", at: "被 @", at_all: "@全体", reply: "被回复",
        mention: "被提及", private: "私聊", unknown: "未知",
      };
      latestBlock =
        `<div class="context__latest">` +
          `<div class="context__meta">` +
            `最近注入 · ${esc(l.at || "—")} · 群 ${esc(l.group || "—")}` +
          `</div>` +
          `<div class="context__kv">` +
            `<span>触发：${esc(TRIGGER_NAMES[l.trigger] || l.trigger || "—")}</span>` +
            `<span>对话对象：${esc(l.talking_to || "—")}</span>` +
            `<span>对话流：${l.flow_count || 0} 条</span>` +
            `<span>注入长度：${l.chars || 0} 字</span>` +
          `</div>` +
          `<pre class="context__preview">${esc(l.preview || "")}</pre>` +
        `</div>`;
    }
    // 短期总结：最近沉淀的笔记
    let summaryBlock = "";
    const sum = c.summary;
    if (sum) {
      const sl = sum.latest || {};
      const cats = [
        ["constraints", "偏好/约束"], ["corrections", "纠正"],
        ["decisions", "已定"], ["todos", "待办"],
      ];
      const parts = cats
        .filter(([k]) => (sl[k] || []).length)
        .map(([k, label]) =>
          `<div class="summary__cat"><b>${label}</b>` +
          (sl[k] || []).map((x) => `<span>· ${esc(x)}</span>`).join("") +
          `</div>`)
        .join("");
      summaryBlock =
        `<div class="summary__block">` +
          `<div class="summary__head">短期总结 · ${sum.enabled ? "已开启" : "已关闭"} · ${sum.sessions || 0} 个会话有笔记` +
            `${sum.latest ? ` · 最近 ${esc(sum.latest.at || "—")} 群 ${esc(sum.latest.group || "—")}` : ""}</div>` +
          (parts || '<div class="context__empty">暂无总结笔记（攒够消息后自动生成）</div>') +
        `</div>`;
    }
    // 成员印象：最近活跃群里的成员画像
    let memberBlock = "";
    const mem = c.members;
    if (mem) {
      const rows = (mem.latest || []).map((p) =>
        `<div class="member__row"><b>${esc(p.name || "—")}</b>` +
        `<span>发言 ${p.messages || 0} · 找你 ${p.to_bot || 0}</span>` +
        `<span>${esc(p.impression || "暂无显著印象")}</span></div>`).join("");
      memberBlock =
        `<div class="summary__block">` +
          `<div class="summary__head">成员印象 · ${mem.enabled ? "已开启" : "已关闭"} · ${mem.sessions || 0} 个群有记录</div>` +
          (rows || '<div class="context__empty">暂无成员印象（有群聊后自动累积）</div>') +
        `</div>`;
    }
    box.innerHTML = stats + latestBlock + summaryBlock + memberBlock;
  }

  // 让 textarea 随内容自动撑高，避免出现内部滚动条
  function autoGrow(node) {
    if (!node) return;
    node.style.height = "auto";
    node.style.height = node.scrollHeight + "px";
  }

  // 角色身分：确认人设是否真的从 AstrBot 读到了
  function renderIdentity(d) {
    const box = el.identityView;
    if (!box) return;
    if (!d || !d.available) {
      box.innerHTML = '<div class="ident__empty">身分未初始化</div>';
      return;
    }
    const rows = [
      ["Bot 自称", d.bot_name || "（留空，跟随人设）"],
      ["对方称呼", d.user_name || "（留空，用「对方」）"],
      ["备用称呼", (d.aliases || []).join("、") || "—"],
      ["兴趣词", (d.interests || []).join("、") || "—"],
    ];
    rows.push(["选用人设", d.persona_id || "AstrBot 默认"]);
    rows.push(["专用提示词", d.using_synth ? `已生成 ${d.plugin_prompt_len} 字` : "尚未生成"]);
    const persona = d.using_synth
      ? `插件专用提示词（${d.plugin_prompt_len} 字）正在生效：「${(d.plugin_prompt_preview || "").replace(/\s+/g, " ").slice(0, 60)}…」`
      : d.auto
        ? (d.persona_len
            ? `已读取 ${d.persona_len} 字人设：「${(d.persona_preview || "").replace(/\s+/g, " ").slice(0, 60)}…」`
            : "已开启自动读取，但当前未取到 AstrBot 人设")
        : "自动读取已关闭，只用手动填写的内容";
    box.innerHTML =
      rows.map(([k, v]) => `<div class="ident__row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("") +
      `<div class="ident__persona">${esc(persona)}</div>`;
  }

  // 防抖实时状态
  function renderDebounce(d) {
    const box = el.debounceStats;
    if (!box) return;
    const rows = [
      ["合并中会话", d.active || 0],
      ["待发车消息", d.pending || 0],
      ["已发车批次", d.batches || 0],
      ["已吸收消息", d.absorbed || 0],
      ["占用中的锁", d.locked || 0],
      ["跟踪会话数", d.sessions || 0],
    ];
    box.innerHTML = rows
      .map(([k, v]) => `<div class="dstats__row"><span>${esc(k)}</span><b>${v}</b></div>`)
      .join("");
  }

  // 与其它插件的功能重叠提示（没有就不占位）
  function renderConflicts(items) {
    const box = el.conflictBox;
    if (!box) return;
    if (!items || !items.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML = items
      .map((c) => `<div class="conflict__row"><b>冲突</b><span>${esc(c.text || "")}</span></div>`)
      .join("");
  }

  // 今日日程：展示 + 手动编辑（保存后各链路直接用）
  function renderSchedule(sc) {
    const box = el.scheduleView;
    if (!box) return;
    lastSchedule = sc || null;
    if (el.scheduleHint) {
      el.scheduleHint.textContent = sc && sc.available
        ? `${sc.date || "—"} · 风格 ${sc.outfit_style || "—"}${sc.auto ? "" : " · 自动生成已关"}`
        : "还没生成";
    }
    if (!sc || !sc.available) {
      box.innerHTML = '<div class="schedule__empty">今天还没有日程。点「重新生成」，或直接在下面手写一份再保存。</div>';
      if (el.scheduleOutfit) { el.scheduleOutfit.value = ""; autoGrow(el.scheduleOutfit); }
      if (el.scheduleText) { el.scheduleText.value = ""; autoGrow(el.scheduleText); }
      return;
    }
    const head =
      `<div class="schedule__head">` +
        `<span>${esc(sc.date || "—")}</span>` +
        (sc.time_desc ? `<span>${esc(sc.time_desc)}</span>` : "") +
        (sc.current_activity ? `<span class="schedule__now">此刻：${esc(sc.current_activity)}</span>` : "") +
      `</div>`;
    const outfit = sc.outfit ? `<div class="schedule__outfit">今日穿搭：${esc(sc.outfit)}</div>` : "";
    const body = `<pre class="schedule__body">${esc(sc.schedule || "")}</pre>`;
    box.innerHTML = head + outfit + body;
    // 同步到编辑框（用户可直接改）
    if (el.scheduleOutfit) { el.scheduleOutfit.value = sc.outfit || ""; autoGrow(el.scheduleOutfit); }
    if (el.scheduleText) { el.scheduleText.value = sc.schedule || ""; autoGrow(el.scheduleText); }
  }

  // 保存手写日程
  async function saveSchedule() {
    if (!bridge) return;
    const schedule = (el.scheduleText && el.scheduleText.value) || "";
    if (!schedule.trim()) {
      toast("日程不能为空", true);
      return;
    }
    const body = {
      action: "schedule_save",
      outfit: (el.scheduleOutfit && el.scheduleOutfit.value) || "",
      schedule,
    };
    if (lastSchedule && lastSchedule.date) body.date = lastSchedule.date;
    if (lastSchedule && lastSchedule.outfit_style) body.outfit_style = lastSchedule.outfit_style;
    try {
      const resp = await bridge.apiPost(`${API}/action`, body);
      toast((resp && resp.message) || (resp && resp.ok ? "已保存" : "保存失败"), !resp || !resp.ok);
      if (resp && resp.ok) await refresh();
    } catch (err) {
      toast(err.message || String(err), true);
    }
  }

  function renderActivity(items) {
    const box = el.activityList;
    if (!box) return;
    const KIND = {
      private: "私聊主动", nudge: "追问", group: "群聊接话",
      icebreak: "冷场破冰", worker: "摸鱼沉淀", schedule: "日程",
      debounce: "消息合并", other: "其他",
    };
    if (!items.length) {
      box.innerHTML = '<div class="activity__empty">还没有活动记录，等中枢跑起来</div>';
      return;
    }
    box.innerHTML = items.map((it) =>
      `<div class="activity__row">` +
        `<span class="activity__time">${esc((it.at || "").slice(11) || it.at || "—")}</span>` +
        `<span class="activity__kind">${esc(KIND[it.kind] || it.kind || "—")}</span>` +
        `<span class="activity__text" title="${esc(it.text || "")}">${esc(it.text || "")}</span>` +
      `</div>`
    ).join("");
  }

  function row(label, value) {
    return `<div class="rhythm__row"><b>${esc(label)}</b><span>${esc(
      value,
    )}</span></div>`;
  }

  // ------------------------------------------------------------ 配置表单

  // 群专属分区：单个分组行的 HTML
  function partitionRow(idx, row) {
    row = row || {};
    return (
      `<div class="partition__row" data-partition-row="${idx}">` +
        `<div class="partition__cell"><label>群号${row.group_name ? ` · ${esc(row.group_name)}` : ""}</label>` +
          `<input data-pf="group_id" value="${esc(row.group_id || "")}" placeholder="群号" /></div>` +
        `<div class="partition__cell"><label>破冰概率</label>` +
          `<input data-pf="icebreak_prob" value="${esc(row.icebreak_prob ?? 0.35)}" inputmode="decimal" /></div>` +
        `<div class="partition__cell"><label>插话概率</label>` +
          `<input data-pf="chitchat_prob" value="${esc(row.chitchat_prob ?? 0.05)}" inputmode="decimal" /></div>` +
        `<div class="partition__cell"><label>冷场(分)</label>` +
          `<input data-pf="idle_minutes" value="${esc(row.idle_minutes ?? 120)}" inputmode="numeric" /></div>` +
        `<button type="button" class="partition__del" title="删除这一行">×</button>` +
      `</div>`
    );
  }

  // 读取群分区子表单，回传 list[dict]
  function readPartitions(root) {
    const rows = [];
    root.querySelectorAll("[data-partition-row]").forEach((r) => {
      const get = (f) => (r.querySelector(`[data-pf="${f}"]`) || {}).value;
      const gid = String(get("group_id") || "").trim();
      if (!gid) return;
      rows.push({
        group_id: gid,
        icebreak_prob: Number(get("icebreak_prob")),
        chitchat_prob: Number(get("chitchat_prob")),
        idle_minutes: Number.parseInt(get("idle_minutes"), 10),
      });
    });
    return rows;
  }

  // 保存群分区：把结构化数据提交到后端
  async function savePartitions(root) {
    const key = root.dataset.partitionRoot;
    if (!key) return;
    const rows = readPartitions(root);
    try {
      const resp = await bridge.apiPost(`${API}/config`, { [key]: rows });
      if (!resp || resp.ok === false) {
        toast((resp && resp.message) || "分区保存失败", true);
        return;
      }
      if (resp.saved === false) {
        toast("群专属分区已生效，但没落盘（重启后可能丢失）", true);
      } else {
        toast("群专属分区已保存");
      }
    } catch (err) {
      toast(err.message || String(err), true);
    }
  }

  // 自定义下拉：展开/选择/点外关闭，选完即保存
  function bindCombos() {
    document.querySelectorAll("[data-combo]").forEach((combo) => {
      const valueEl = combo.querySelector(".combo__value");
      const hidden = combo.querySelector("input[type=hidden]");
      const menu = combo.querySelector(".combo__menu");
      combo.addEventListener("click", (event) => {
        const item = event.target.closest(".combo__item");
        if (item) {
          const val = item.dataset.value || "";
          hidden.value = val;
          valueEl.textContent = item.textContent;
          valueEl.title = item.title || item.textContent;
          combo.classList.remove("is-open");
          menu.querySelectorAll(".combo__item").forEach((n) => n.classList.toggle("is-active", n === item));
          submitField(hidden);
          return;
        }
        combo.classList.toggle("is-open");
      });
    });
    document.addEventListener("click", (event) => {
      document.querySelectorAll(".combo.is-open").forEach((combo) => {
        if (!combo.contains(event.target)) combo.classList.remove("is-open");
      });
    });
  }

  // 绑定群分区的增删与自动保存（失焦即存）
  function bindPartitions() {
    document.querySelectorAll("[data-partition-root]").forEach((root) => {
      root.addEventListener("click", (event) => {
        const add = event.target.closest("[data-partition-add]");
        const del = event.target.closest(".partition__del");
        const list = root.querySelector(".partition__list");
        if (!list) return;
        if (add) {
          const empty = list.querySelector(".partition__empty");
          if (empty) empty.remove();
          const tmp = document.createElement("div");
          tmp.innerHTML = partitionRow(Date.now(), {});
          list.appendChild(tmp.firstElementChild);
          return;
        }
        if (del) {
          const row = del.closest("[data-partition-row]");
          if (row) row.remove();
          savePartitions(root);
        }
      });
      // 输入失焦后自动保存，避免每次敲键都写盘
      root.addEventListener("focusout", (event) => {
        if (event.target && event.target.dataset && event.target.dataset.pf) {
          savePartitions(root);
        }
      });
    });
  }

  const chipHtml = (text) =>
    `<span class="chip" data-chip="${esc(text)}">${esc(text)}` +
    `<button type="button" class="chip__del" aria-label="删除">×</button></span>`;

  function bindChips(root) {
    if (!root) return;
    root.querySelectorAll(".chip__del").forEach((btn) => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        const chip = btn.closest(".chip");
        if (chip) chip.remove();
        submitField(root);
      });
    });
    const input = root.querySelector(".chip__input");
    if (input && !input.dataset.bound) {
      input.dataset.bound = "1";
      input.addEventListener("keydown", (ev) => {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        const val = input.value.trim();
        if (!val) return;
        input.insertAdjacentHTML("beforebegin", chipHtml(val));
        input.value = "";
        bindChips(root);
        submitField(root);
      });
    }
  }

  function renderChips(root, items) {
    const box = root && root.querySelector(".chips");
    if (!box) return;
    box.querySelectorAll(".chip").forEach((c) => c.remove());
    const input = box.querySelector(".chip__input");
    const html = (items || []).map(chipHtml).join("");
    if (input) input.insertAdjacentHTML("beforebegin", html);
    else box.insertAdjacentHTML("afterbegin", html);
    bindChips(root);
  }

  function buildConf(schema, values, options) {
    if (confBuilt) return;
    options = options || {};
    // 每个分頁各自一个 form，不共享滚动；先收集再分配
    const buckets = { core: [], private: [], group: [], worker: [], rhythm: [], schedule: [], context: [], debounce: [], identity: [] };
    schema.forEach(({ key, type }) => {
        const group = GROUP_OF(key);
        const label = LABELS[key] || key;
        const value = values[key];
        const field = (() => {
        // 自定义搜索方向用多行文本框
        if (key === "摸鱼_搜索方向") {
          return (
            `<div class="field field--wide"><label>${esc(label)}（每行一个方向，留空用默认）</label>` +
            `<textarea data-key="${esc(key)}" data-type="str" rows="4" ` +
            `placeholder="例如：今天的 AI 新模型发布&#10;某个游戏的版本更新">${esc(value || "")}</textarea></div>`
          );
        }
        // 群专属分区是 list[dict]，用可增删的结构化子表单，而非普通输入框
        if (key === "群聊独立分区配置") {
          const rows = Array.isArray(value) ? value : [];
          const rowHtml = rows.map((r, i) => partitionRow(i, r)).join("");
          return (
            `<div class="field field--wide" data-partition-root="${esc(key)}">` +
            `<div class="partition__title">${esc(label)}<em>未定制的群走全局默认</em></div>` +
            `<div class="partition__list">${rowHtml || '<div class="partition__empty">暂无分区，点下方添加</div>'}</div>` +
            `<button type="button" class="partition__add" data-partition-add="${esc(key)}">＋ 添加群分区</button>` +
            `</div>`
          );
        }
        if (type === "bool") {
          return (
            `<div class="field"><label for="f-${esc(key)}">${esc(label)}</label>` +
            `<input type="checkbox" id="f-${esc(key)}" data-key="${esc(key)}" data-type="bool" ${
              value ? "checked" : ""
            } /></div>`
          );
        }
        // 有选项的字段渲染成自定义下拉，贴合整体单色细线风格
        const opts = options[key];
        if (Array.isArray(opts) && opts.length) {
          const current = value == null ? "" : String(value);
          const list = opts.slice();
          if (current && !list.some((o) => String(o.value) === current)) {
            list.unshift({ value: current, label: `${current}（当前值，已失效）` });
          }
          const currentLabel = (list.find((o) => String(o.value) === current) || {}).label || "请选择";
          const items = list
            .map(
              (o) =>
                `<div class="combo__item${String(o.value) === current ? " is-active" : ""}" ` +
                `data-value="${esc(o.value)}" title="${esc(o.label)}">${esc(o.label)}</div>`,
            )
            .join("");
          return (
            `<div class="field"><label>${esc(label)}</label>` +
            `<div class="combo" data-combo="${esc(key)}">` +
              `<div class="combo__value" title="${esc(currentLabel)}">${esc(currentLabel)}</div>` +
              `<span class="combo__arrow">▾</span>` +
              `<div class="combo__menu">${items}</div>` +
              `<input type="hidden" data-key="${esc(key)}" data-type="str" value="${esc(current)}" />` +
            `</div></div>`
          );
        }
        // 长文本（如提示词模板）用多行
        if (TEXTAREA_KEYS.includes(key)) {
          return (
            `<div class="field field--wide"><label for="f-${esc(key)}">${esc(label)}</label>` +
            `<textarea id="f-${esc(key)}" class="field__text" data-key="${esc(key)}" data-type="str" ` +
            `rows="10" placeholder="留空使用内置模板">${esc(value ?? "")}</textarea></div>`
          );
        }
        // 清单类（服装库、免打扰时段等）用标签式编辑：紧凑、全部可见、可增删
        if (type === "list") {
          const items = Array.isArray(value) ? value : [];
          return (
            `<div class="field field--wide" data-list-root="${esc(key)}" data-key="${esc(key)}" data-type="list">` +
            `<label>${esc(label)}</label>` +
            `<div class="chips">${items.map(chipHtml).join("")}` +
            `<input class="chip__input" placeholder="+ 添加" /></div>` +
            `</div>`
          );
        }
        const text = value ?? "";
        return (
          `<div class="field"><label for="f-${esc(key)}">${esc(label)}</label>` +
          `<input id="f-${esc(key)}" data-key="${esc(key)}" data-type="${esc(type)}" ` +
          `value="${esc(text)}" inputmode="decimal" /></div>`
        );
        })();
        (buckets[group] || buckets.core).push(field);
    });
    el.confForms.forEach((form) => {
      const g = form.dataset.conf;
      form.innerHTML = (buckets[g] || []).join("") || '<div class="conf__empty">此分栏暂无配置项</div>';
      // 多行框按内容撑高，避免清单/模板出现内部滚动条
      form.querySelectorAll("textarea").forEach((node) => {
        autoGrow(node);
        node.addEventListener("input", () => autoGrow(node));
      });
      form.querySelectorAll("[data-list-root]").forEach(bindChips);
    });
    // 上下文分页的状态说明
    const info = document.getElementById("contextInfo");
    if (info) {
      const on = values["场景感知_启用"] !== false;
      info.innerHTML =
        `<div class="context__state">当前状态：<b>${on ? "已开启" : "已关闭"}</b></div>` +
        `<div class="context__tips">` +
        `· 群聊消息会分析「谁在跟谁说话」与触发类型<br>` +
        `· 主动接话时会标注「不是在问你」，避免抢答<br>` +
        `· 纯规则分析，无额外模型调用；关闭后接话照常，只是不带场景提示` +
        `</div>`;
    }
    confBuilt = true;
    bindPartitions();
    bindCombos();
  }

  // 从后端重新拉配置并回填表单（不动用户当前正在编辑的输入框）
  async function syncConfigFields() {
    if (!bridge) return;
    try {
      const c = await bridge.apiGet(`${API}/config`);
      const values = c.values || {};
      document.querySelectorAll("[data-key]").forEach((node) => {
        const key = node.dataset.key;
        if (!Object.prototype.hasOwnProperty.call(values, key)) return;
        // 正在聚焦的输入框不覆盖，避免打断用户输入
        if (document.activeElement === node) return;
        const actual = values[key];
        if (node.dataset.type === "bool") node.checked = Boolean(actual);
        else if (node.dataset.type === "list") {
          if (node.hasAttribute("data-list-root")) {
            renderChips(node, Array.isArray(actual) ? actual : []);
          } else {
            node.value = Array.isArray(actual)
              ? actual.join(node.value.includes("\n") ? "\n" : ", ")
              : String(actual ?? "");
          }
        } else {
          node.value = String(actual ?? "");
          if (node.tagName === "TEXTAREA" && typeof autoGrow === "function") autoGrow(node);
        }
      });
      // 下拉选项也刷新（人设列表/模型列表可能变化）
      if (c.options) {
        document.querySelectorAll(".combo[data-key]").forEach((combo) => {
          const opts = c.options[combo.dataset.key];
          if (!Array.isArray(opts)) return;
          const current = combo.querySelector("input[type=hidden]")?.value ?? "";
          const menu = combo.querySelector(".combo__menu");
          if (!menu) return;
          menu.innerHTML = opts
            .map((o) =>
              `<div class="combo__item${String(o.value) === String(current) ? " is-active" : ""}" data-value="${esc(String(o.value))}">${esc(o.label || "")}</div>`
            )
            .join("");
          if (typeof bindCombos === "function") bindCombos();
        });
      }
    } catch (err) {
      toastThrottled(`同步配置失败：${err.message || err}`);
    }
  }

  function readField(node) {
    const type = node.dataset.type;
    if (type === "bool") return node.checked;
    if (type === "list") {
      // 标签式：直接取当前所有标签，后端 _coerce 再按具体项决定转数字还是保留
      return Array.from(node.querySelectorAll(".chip"))
        .map((c) => c.dataset.chip || "")
        .filter(Boolean);
    }
    if (type === "int") return Number.parseInt(node.value, 10);
    if (type === "float") return Number.parseFloat(node.value);
    return node.value;
  }

  async function submitField(node) {
    const key = node.dataset.key;
    if (!key) return;
    const value = readField(node);
    if (typeof value === "number" && !Number.isFinite(value)) {
      toast(`${LABELS[key] || key} 不是有效数字`, true);
      return;
    }
    try {
      const resp = await bridge.apiPost(`${API}/config`, { [key]: value });
      if (!resp || resp.ok === false) {
        toast((resp && resp.message) || "写入失败", true);
        return;
      }
      if (resp.saved === false) {
        toast(`${LABELS[key] || key} 已生效，但没落盘`, true);
      } else {
        toast(`${LABELS[key] || key} 已更新`);
      }
      // 后端可能对值做正规化（bool 解析、list 清洗、int/float 转换），
      // 把栏位同步成实际生效值，避免显示的是你输入的而非真正保存的。
      if (resp.changed && Object.prototype.hasOwnProperty.call(resp.changed, key)) {
        const actual = resp.changed[key];
        if (node.dataset.type === "bool") node.checked = Boolean(actual);
        else if (node.dataset.type === "list") {
          renderChips(node, Array.isArray(actual) ? actual : []);
        }
        else node.value = String(actual ?? "");
        // 自定义下拉：同步显示文字与选中项
        const combo = node.closest("[data-combo]");
        if (combo) {
          const valEl = combo.querySelector(".combo__value");
          const match = Array.from(combo.querySelectorAll(".combo__item"))
            .find((it) => it.dataset.value === String(actual));
          if (match) {
            if (valEl) { valEl.textContent = match.textContent; valEl.title = match.title || match.textContent; }
            combo.querySelectorAll(".combo__item").forEach((it) => it.classList.toggle("is-active", it === match));
          }
        }
      }
      await refresh();
    } catch (err) {
      toast(err.message || String(err), true);
    }
  }

  // ------------------------------------------------------------ 数据

  async function refresh() {
    if (!bridge || refreshing) return;
    refreshing = true;
    try {
      const s = await bridge.apiGet(`${API}/status`);
      renderStatus(s);
      lastErrorAt = 0; // 成功一次就重置节流，下次故障能立刻提示
    } catch (err) {
      el.loopDot.dataset.state = "off";
      el.loopText.textContent = "读取失败";
      toastThrottled(err.message || String(err));
      return;
    } finally {
      refreshing = false;
    }

    if (!confBuilt) {
      try {
        const c = await bridge.apiGet(`${API}/config`);
        buildConf(c.schema || [], c.values || {}, c.options || {});
      } catch (err) {
        toastThrottled(`配置读取失败：${err.message || err}`);
      }
    }
  }

  async function runAction(action) {
    const buttons = document.querySelectorAll(".act");
    const target = document.querySelector(`.act[data-act="${action}"]`);
    const oldText = target ? target.textContent : "";
    buttons.forEach((b) => (b.disabled = true));
    // 生成日程要等模型，给出明确进行中提示，避免看起来"点了没反应"
    if (target && action === "schedule_generate") target.textContent = "生成中…";
    try {
      const body = { action };
      // 日程生成可带补充要求
      if (action === "schedule_generate" && el.scheduleExtra && el.scheduleExtra.value.trim()) {
        body.extra = el.scheduleExtra.value.trim();
      }
      if (action === "persona_synthesize" && el.personaExtra && el.personaExtra.value.trim()) {
        body.extra = el.personaExtra.value.trim();
      }
      if (action.startsWith("persona_") && target) target.textContent = "处理中…";
      const resp = await bridge.apiPost(`${API}/action`, body);
      toast((resp && resp.message) || (resp && resp.ok ? "已完成" : "失败"), !resp || !resp.ok);
      await refresh();
      await syncConfigFields();
    } catch (err) {
      toast(err.message || String(err), true);
    } finally {
      if (target) target.textContent = oldText;
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  /* ------------------------------------------------------------ 字号调节 */

  const ZOOM_KEY = "pl_panel_zoom";
  const ZOOM_STEPS = [1, 1.1, 1.2, 1.35, 1.5, 1.75, 2];

  function applyZoom(scale) {
    // localStorage 读回来是字符串，必须先转数字再比对，否则 includes 永远 false，
    // 每次打开都被重置成 100%（这就是之前缩放记不住的根因）。
    const num = Number(scale);
    const v = Number.isFinite(num) && ZOOM_STEPS.includes(num) ? num : 1;
    document.documentElement.style.setProperty("--fs", String(v));
    if (el.zoomVal) el.zoomVal.textContent = `${Math.round(v * 100)}%`;
    if (el.zoomIn) el.zoomIn.disabled = v >= ZOOM_STEPS[ZOOM_STEPS.length - 1];
    if (el.zoomOut) el.zoomOut.disabled = v <= ZOOM_STEPS[0];
    try {
      window.localStorage.setItem(ZOOM_KEY, String(v));
    } catch (err) {
      /* 无痕模式下存不了就算了 */
    }
  }

  function stepZoom(direction) {
    const current = Number(
      document.documentElement.style.getPropertyValue("--fs") || 1,
    );
    // 找最接近的档位，避免浮点误差导致 indexOf 找不到
    let idx = ZOOM_STEPS.findIndex((s) => Math.abs(s - current) < 0.001);
    if (idx < 0) idx = 0;
    const next = ZOOM_STEPS[Math.min(
      ZOOM_STEPS.length - 1,
      Math.max(0, idx + direction),
    )];
    applyZoom(next);
  }

  function initZoom() {
    let saved = 1;
    try {
      saved = Number(window.localStorage.getItem(ZOOM_KEY)) || 1;
    } catch (err) {
      saved = 1;
    }
    applyZoom(saved);
    if (el.zoomIn) el.zoomIn.addEventListener("click", () => stepZoom(1));
    if (el.zoomOut) el.zoomOut.addEventListener("click", () => stepZoom(-1));
  }

  // 分区 tab：每个分组是独立分頁，互不共享滚动
  const TAB_KEY = "pl_panel_tab";
  const TABS = ["overview", "private", "group", "worker", "rhythm", "schedule", "debounce", "identity", "context"];

  function setTab(name) {
    const tab = TABS.includes(name) ? name : "overview";
    document.querySelectorAll("#tabs li").forEach((li) => {
      const active = li.dataset.tab === tab;
      li.classList.toggle("is-selected", active);
      li.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".panel-view").forEach((view) => {
      view.classList.toggle("is-active", view.dataset.view === tab);
    });
    // 切换分页后把内容滚动到顶，避免上一页的滚动位置串到新页
    const content = document.querySelector(".content");
    if (content) content.scrollTop = 0;
    window.scrollTo({ top: 0 });
    try {
      window.localStorage.setItem(TAB_KEY, tab);
    } catch (err) {}
  }

  function initTabs() {
    let saved = "overview";
    try {
      saved = window.localStorage.getItem(TAB_KEY) || "overview";
      if (!TABS.includes(saved)) saved = "overview";
    } catch (err) {}
    setTab(saved);
    document.querySelectorAll("#tabs li").forEach((li) => {
      li.addEventListener("click", () => setTab(li.dataset.tab));
    });
  }

  // 群聊列表的折叠，状态记在本地，下次打开还是上次那样
  const FOLD_KEY = "pl_group_folded";

  function setGroupFold(folded) {
    if (!el.groupSec || !el.groupFold) return;
    el.groupSec.classList.toggle("folded", folded);
    el.groupFold.textContent = folded ? "展开" : "收起";
    el.groupFold.setAttribute("aria-expanded", folded ? "false" : "true");
    try {
      window.localStorage.setItem(FOLD_KEY, folded ? "1" : "0");
    } catch (err) {}
  }

  function initGroupFold() {
    let folded = false;
    try {
      folded = window.localStorage.getItem(FOLD_KEY) === "1";
    } catch (err) {
      folded = false;
    }
    setGroupFold(folded);
    if (el.groupFold) {
      el.groupFold.addEventListener("click", () => {
        setGroupFold(!el.groupSec.classList.contains("folded"));
      });
    }
  }

  function bind() {
    el.confForms.forEach((form) => {
      form.addEventListener("change", (event) => {
        const node = event.target;
        if (node && node.dataset && node.dataset.key) {
          submitField(node);
        }
      });
    });

    document.querySelectorAll(".act").forEach((btn) => {
      if (btn.dataset.act) btn.addEventListener("click", () => runAction(btn.dataset.act));
    });
    if (el.scheduleSave) el.scheduleSave.addEventListener("click", saveSchedule);
    [el.scheduleOutfit, el.scheduleText].forEach((node) => {
      if (node) node.addEventListener("input", () => autoGrow(node));
    });
  }

  async function main() {
    initZoom();
    initTabs();
    initGroupFold();
    initParticleBackground();
    initThemeSwitch();
    initScrollWatch();
    bind();
    try {
      bridge = await getBridge();
    } catch (err) {
      el.loopText.textContent = "Bridge 未就绪";
      toast(err.message || String(err), true);
      return;
    }
    await refresh();
    // 页面隐藏时停掉轮询，回来立刻刷新一次，省掉后台无谓请求
    function startTimer() {
      if (timer) return;
      timer = window.setInterval(refresh, REFRESH_MS);
    }
    function stopTimer() {
      if (!timer) return;
      window.clearInterval(timer);
      timer = null;
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopTimer();
      else {
        refresh();
        startTimer();
      }
    });
    if (!document.hidden) startTimer();
    window.addEventListener("beforeunload", stopTimer);
  }

  main();
})();
