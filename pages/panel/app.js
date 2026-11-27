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
    "拟人_跨会话记忆": "跨会话记住同一个人",
    "拟人_说话方式约束": "注入说话方式硬约束，防止低智模型退化成解说员/百科",
    "拟人_说话方式强度": "strict=严格（防解说员）/gentle=温和（只提醒语气）",
    "拟人_说话方式补充": "自己补充的说话要求（会加到约束末尾，200字内）",
    "拟人_情绪落盘": "情绪状态落盘",
    "拟人_情绪注入被动": "被动回复附带情绪",
    "心事_被动注入": "被动回复附带心事",
    "生活状态_总字数上限": "状态块字数上限",
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
    "戳一戳_上下文条数": "戳/艾特时附带前面几条消息当上下文",
    "戳一戳_上下文秒数": "只附带这么多秒内发的消息（防串到很久以前的话题）",
    "戳一戳_后续合并": "先戳/艾特、后发消息时，把后续内容合并进同一次回复",
    "戳一戳_纯艾特回应": "纯艾特也算戳一戳",
    "戳一戳_个人冷却秒": "个人戳完冷却 · 秒",
    "戳一戳_群冷却秒": "群内被戳冷却 · 秒",
    "戳一戳_反戳": "戳回去",
    "戳一戳_回应概率": "被戳回应概率",
    "戳一戳_纯艾特概率": "纯艾特回应概率",
    "跟读_阈值": "几句相同才复读",
    "跟读_最小间隔秒": "复读间隔",
    "跟读_概率": "复读概率",
    "跟读_屏蔽词": "不复读的词",
    "出站_思考标签": "思考标签名",
    "出站_裸思考过滤": "过滤裸思考",
    "出站_孤闭合处理": "落单结束标签",
    "出站_未闭合处理": "没关上的标签",
    "出站_思考标签大小写敏感": "区分大小写",
    "出站_空回复兜底": "空回复时说啥",
    "出站_修剪空白": "去掉首尾空格",
    "出站_压缩空行": "合并空行",
    "出站_八股_清AI八股": "清理 AI 八股",
    "出站_八股_改句式": "改掉绕弯句式",
    "出站_八股_换词": "换掉老套词",
    "出站_八股_删八股短语": "删掉套话",
    "出站_八股_激进删词": "删得更狠",
    "出站_八股_水词配额": "保留几个水词",
    "出站_八股_合并碎句": "合并短句",
    "出站_八股_碎句阈值": "多短算短句",
    "出站_八股_删结尾升华": "删掉结尾升华",
    "出站_八股_保护细节块": "保护细节内容",
    "出站_八股_保护代码表格": "保护代码表格",
    "出站_八股_保护引号台词": "保护引号台词",
    "出站_八股_禁用规则": "停用规则",
    "出站_八股_演练模式": "只预览不修改",
    "模块_天气": "天气功能",
    "天气_城市": "你在哪个城市",
    "黑话_自动解析": "自动解析新黑话",
    "黑话_解析模型": "解析专用模型",
    "模块_黑话": "群黑话",
    "黑话_最小命中": "出现几次算黑话",
    "黑话_最多条数": "最多带几条黑话",
    "黑话_只注入已解析": "只注入确认过的黑话",
    "模块_黑名单": "黑名单准入模块总开关",
    "模块_黑名单_ID": "黑名单 QQ 号",
    "模块_黑名单_批量文本": "批量黑名单文本",
    "模块_黑名单_拦主动": "黑名单禁止主动私聊",
    "模块_黑名单_记日志": "拦截时记日志",

    "模块_撤回取消": "撤回取消模块总开关",
    "模块_戳一戳": "戳一戳互动模块总开关",
    "模块_跟读": "群聊复读模块总开关",
    "模块_思考过滤": "出站清洗模块总开关",
    "模块_八股清洗": "出站清洗模块总开关",

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
    "防抖_推理续租秒": "模型推理续租 · 秒",
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
    "日程_被动注入": "被动回复也带生活状态",
    "日程_被动注入字数": "注入日程字数上限",
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
    "场景感知_让位contextaware": "让位给旧插件",
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
    "节奏_间隔秒数": "正常回复补发间隔 · 秒",
    "节奏_去句末标点": "去句末标点",
    "节奏_去颜文字": "去颜文字",
    "节奏_表格代码不拆": "表格代码不拆",
    "节奏_花括号不拆": "花括号不拆",
    "节奏_打印处理详情": "打印处理详情",
    "节奏_只对群聊": "只对群聊",
    "节奏_演练模式": "演练模式",
    "模拟分段打字": "模拟分段打字",
    "分段发送延迟": "主动私聊打字停顿 · 秒",
  };

  // 配置栏位归到独立分頁（对应 HTML 的 data-view / data-conf）
  const PRIVATE_KEYS = ["私聊主动_启用", "私聊追问_启用", "对谁主动", "最短间隔", "每天最多几次", "触发概率", "安静多久才找", "追问等待秒数", "追问过期秒数", "追问最多次数", "参考几条", "参考日程", "参考最近聊天"];
  const CORE_KEYS = ["全局调度间隔", "免打扰时段"];
  const WORKER_KEYS = ["后台摸鱼_启用", "摸鱼干活最短间隔", "摸鱼干活概率", "优先消费心事动机"];
  const TYPING_KEYS = ["模拟分段打字", "分段发送延迟"];
  // 这些键用多行文本框编辑
  const TEXTAREA_KEYS = ["日程_提示词模板", "角色_生活背景", "角色_额外人设", "角色_插件专用提示词"];
  const MODULE_GROUP = {
    "模块_私聊": "private", "模块_群聊": "group", "模块_摸鱼": "worker",
    "模块_节奏": "rhythm", "模块_日程": "schedule", "模块_上下文": "context",
    "模块_防抖": "debounce", "模块_身份": "identity",
    // 融合进来的模块开关，按语义归到相关分页，不再全堆总览
    "模块_黑名单": "blacklist", "模块_撤回取消": "interact", "模块_戳一戳": "interact",
    "模块_跟读": "group", "模块_黑话": "slang",
    "模块_思考过滤": "outbound", "模块_八股清洗": "outbound", "模块_天气": "schedule",
  };
  const GROUP_OF = (key) => {
    if (MODULE_GROUP[key]) return MODULE_GROUP[key];
    if (key.startsWith("角色_")) return "identity";
    if (key === "日程生成时间" || key.startsWith("日程_")) return "schedule";
    if (key.startsWith("防抖_")) return "debounce";
    if (key.startsWith("场景感知_") || key.startsWith("拟人_")) return "context";
    if (CORE_KEYS.includes(key)) return "core";
    if (PRIVATE_KEYS.includes(key)) return "private";
    if (key.startsWith("模块_黑名单")) return "blacklist";
    if (key.startsWith("黑话_") || key === "模块_黑话") return "slang";
    if (key.startsWith("群聊") || key.startsWith("跟读_")
        ) return "group";
    if (key.startsWith("戳一戳_")) return "interact";
    if (key.startsWith("出站_")) return "outbound";
    if (key.startsWith("场景感知_")) return "context";
    if (key.startsWith("天气_")) return "schedule";
    if (WORKER_KEYS.includes(key) || key.startsWith("摸鱼_")) return "worker";
    if (key.startsWith("节奏_") || TYPING_KEYS.includes(key)) return "rhythm";
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
    slangList: document.getElementById("slangList"),
    slangMeta: document.getElementById("slangMeta"),
    slangClearAll: document.getElementById("slangClearAll"),
    slangAnalyze: document.getElementById("slangAnalyze"),
    slangAnalysis: document.getElementById("slangAnalysis"),
    blacklistView: document.getElementById("blacklistView"),
    outboundHistory: document.getElementById("outboundHistory"),
    ruleCatalog: document.getElementById("ruleCatalog"),
    ruleTop: document.getElementById("ruleTop"),
    ruleMeta: document.getElementById("ruleMeta"),
    confForms: Array.from(document.querySelectorAll("form.conf")),
    toast: document.getElementById("toast"),
    footNote: document.getElementById("footNote"),
    footTime: document.getElementById("footTime"),
    activityList: document.getElementById("activityList"),
    conflictBox: document.getElementById("conflictBox"),
    debounceStats: document.getElementById("debounceStats"),
    identityView: document.getElementById("identityView"),
    personaExtra: document.getElementById("personaExtra"),
    motiveInput: document.getElementById("motiveInput"),
    motiveAddBtn: document.getElementById("motiveAddBtn"),
    motiveList: document.getElementById("motiveList"),
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

  // ------------------------------------------------------------ 偏好本地持久化（后端文件 + localStorage 双兜底）
  const THEME_KEY = "pl_panel_theme";
  const MONO_KEY = "pl_panel_mono";
  const userTouchedPrefs = new Set();
  let pendingPrefPatch = {};
  let prefSaveTimer = null;

  function queueRemotePrefs(patch) {
    if (!patch || typeof patch !== "object") return;
    Object.assign(pendingPrefPatch, patch);
    if (!bridge) return;
    if (prefSaveTimer) window.clearTimeout(prefSaveTimer);
    prefSaveTimer = window.setTimeout(async () => {
      const body = pendingPrefPatch;
      pendingPrefPatch = {};
      prefSaveTimer = null;
      if (!bridge || !Object.keys(body).length) return;
      try {
        await bridge.apiPost(`${API}/prefs`, body);
      } catch (err) {
        /* 兼容尚未重载的新接口 404，静默忽略 */
      }
    }, 120);
  }

  function applyThemeState(theme, mono, persist = true) {
    const root = document.documentElement;
    const buttons = document.querySelectorAll(".theme-btn");
    if (theme === "light" || theme === "dark") {
      root.dataset.theme = theme;
      buttons.forEach((b) => {
        if (b.dataset.themeSet === "light" || b.dataset.themeSet === "dark") {
          b.classList.toggle("is-selected", b.dataset.themeSet === theme);
        }
      });
      try { window.localStorage.setItem(THEME_KEY, theme); } catch (err) {}
    }
    if (typeof mono === "boolean") {
      document.body.classList.toggle("is-monospaced", mono);
      buttons.forEach((b) => {
        if (b.dataset.themeSet === "mono") b.classList.toggle("is-selected", mono);
      });
      try { window.localStorage.setItem(MONO_KEY, mono ? "1" : "0"); } catch (err) {}
    }
    if (persist) {
      const patch = {};
      if (theme === "light" || theme === "dark") {
        userTouchedPrefs.add("theme");
        patch.theme = theme;
      }
      if (typeof mono === "boolean") {
        userTouchedPrefs.add("mono");
        patch.mono = mono;
      }
      queueRemotePrefs(patch);
    }
  }

  // ------------------------------------------------------------ 主题开关
  function initThemeSwitch() {
    const buttons = document.querySelectorAll(".theme-btn");
    try {
      const savedTheme = window.localStorage.getItem(THEME_KEY);
      const savedMono = window.localStorage.getItem(MONO_KEY);
      applyThemeState(
        savedTheme === "light" || savedTheme === "dark" ? savedTheme : null,
        savedMono === "1" ? true : savedMono === "0" ? false : undefined,
        false,
      );
    } catch (err) {}
    buttons.forEach((b) => b.addEventListener("click", () => {
      const mode = b.dataset.themeSet;
      if (mode === "mono") {
        applyThemeState(null, !document.body.classList.contains("is-monospaced"), true);
        return;
      }
      applyThemeState(mode, false, true);
    }));
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

  let updateNavFrameMetrics = () => {};

  function initNavFrame() {
    const nav = document.getElementById("tabs");
    const scroller = document.getElementById("navScroll");
    const thumb = document.getElementById("navThumb");
    if (!nav || !scroller || !thumb) return;

    function update() {
      const max = scroller.scrollHeight - scroller.clientHeight;
      const scrollable = max > 2;
      nav.classList.toggle("is-scrollable", scrollable);
      scroller.classList.toggle("is-fade-top", scrollable && scroller.scrollTop > 4);
      scroller.classList.toggle("is-fade-bottom", scrollable && scroller.scrollTop < max - 4);
      if (!scrollable) return;
      const ratio = scroller.clientHeight / scroller.scrollHeight;
      const thumbPct = Math.max(14, Math.min(100, ratio * 100));
      const progress = max > 0 ? scroller.scrollTop / max : 0;
      const topPct = progress * (100 - thumbPct);
      thumb.style.height = `${thumbPct.toFixed(1)}%`;
      thumb.style.top = `${topPct.toFixed(1)}%`;
    }

    updateNavFrameMetrics = update;
    scroller.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update, { passive: true });
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(update);
      ro.observe(scroller);
      if (scroller.firstElementChild) ro.observe(scroller.firstElementChild);
    }
    requestAnimationFrame(update);
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
  renderSlang(s.slang || {});
  renderBlacklist(s.blacklist || {});
  renderOutbound(s.outbound || {});
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

  // 高风险句式（只统计不改写）的中文标签
  const RISK_LABELS = {
    not_but: "不是…而是…",
    drama_tail: "短剧金句甩尾",
    short_parallel: "短句排比",
    dash_heavy: "破折号泛滥",
  };
  function riskLine(risk) {
    if (!risk) return "";
    const items = Object.keys(risk).map((k) => RISK_LABELS[k] || k);
    if (!items.length) return "";
    return '<div class="polish__risk">仅记录（不改写）：' + esc(items.join(" · ")) + '</div>';
  }

  function renderOutbound(d) {
    // 规则清单：按分类分组，公开每条规则在干什么
    if (el.ruleCatalog) {
      const cats = d.categories || [];
      const rules = d.rules || [];
      if (el.ruleMeta) {
        el.ruleMeta.textContent = d.rule_total
          ? `共 ${d.rule_total} 条规则 · ${cats.length} 类${d.ai_rules_enabled ? "" : "（AI 八股已关闭）"}`
          : "—";
      }
      el.ruleCatalog.innerHTML = cats.map((c) => {
        const items = rules.filter((r) => r.category === c.key);
        if (!items.length) return "";
        return '<div class="rule-cat">' +
          '<div class="rule-cat__head"><b>' + esc(c.label) + '</b>' +
            '<span>' + items.length + ' 条</span></div>' +
          items.map((r) =>
            '<div class="rule-item" data-rule="' + esc(r.id) + '">' +
              '<span class="rule-item__name">' + esc(r.name) + '</span>' +
              '<span class="rule-item__desc">' + esc(r.desc || "") + '</span>' +
            '</div>'
          ).join("") +
        '</div>';
      }).join("") || '<div class="motive__empty">规则清单加载中</div>';
    }
    // 最近命中排行
    if (el.ruleTop) {
      const top = d.top_rules || [];
      el.ruleTop.innerHTML = top.length
        ? top.map((t) =>
            '<div class="rule-top__row"><span>' + esc(t.name) + '</span>' +
              '<b>' + esc(String(t.count)) + ' 次</b></div>'
          ).join("")
        : '<div class="motive__empty">还没有命中记录，模型输出八股时会出现在这里</div>';
    }

    const box = el.outboundHistory;
    if (!box) return;
    const items = d.history || [];
    if (!items.length) {
      box.innerHTML = '<div class="motive__empty">还没有清洗记录。开启八股清洗后，模型发出八股味的句子会被记在这里。</div>';
      return;
    }
    box.innerHTML = items.map((it) =>
      '<div class="polish__row">' +
        '<div class="polish__head"><span class="polish__time">' + esc(it.time || '') + '</span>' +
          '<span class="polish__hits">改了 ' + esc(String(it.hits || 0)) + ' 处</span>' +
          '<span class="polish__rules">' + esc((it.rule_names || it.rules || []).join(' · ')) + '</span></div>' +
        '<div class="polish__before"><b>原文</b>' + esc(it.before || '') + '</div>' +
        '<div class="polish__after"><b>改后</b>' + esc(it.after || '') + '</div>' +
        riskLine(it.risk) +
      '</div>'
    ).join('');
  }

  function renderBlacklist(d) {
    const box = el.blacklistView;
    if (!box) return;
    const ids = d.ids || [];
    if (!d.enabled) {
      box.innerHTML = '<div class="motive__empty">黑名单已关闭</div>';
      return;
    }
    if (!ids.length) {
      box.innerHTML = '<div class="motive__empty">还没有黑名单，在下方添加 QQ 号</div>';
      return;
    }
    box.innerHTML = ids.map((id) =>
      '<div class="card"><div class="card__meta"><b>QQ</b> ' + esc(id) + '</div></div>'
    ).join('');
  }

  function renderSlang(d) {
    const list = el.slangList, meta = el.slangMeta;
    if (!list) return;
    const recs = d.records || [];
    if (meta) {
      meta.textContent = d.enabled
        ? `已挖掘 ${recs.length} 条 · 出现 ${d.min_hits || 3} 次以上才算黑话`
        : "已关闭";
    }
    // 解析结果区
    const box = el.slangAnalysis;
    if (box) {
      const an = (d.analysis || []).filter((a) => a.is_slang !== false);
      box.innerHTML = an.length
        ? an.map((a) =>
            '<div class="slang__an" data-word="' + esc(a.word || '') + '">' +
              '<b>' + esc(a.word || '') + '</b>' +
              '<span class="slang__meaning" title="点击修改释义">' + esc(a.meaning || '（点此填写释义）') + '</span>' +
            '</div>'
          ).join('')
        : '<div class="motive__empty">模型会每 30 分钟自动解析新词（可点上方「立即解析」手动触发）</div>';
      // 点击释义 -> 就地编辑，失焦/回车保存
      box.querySelectorAll('.slang__meaning').forEach((span) => {
        span.addEventListener('click', () => {
          if (span.querySelector('input')) return;
          const row = span.closest('.slang__an');
          const word = row && row.dataset.word;
          const oldText = span.textContent === '（点此填写释义）' ? '' : span.textContent;
          span.innerHTML = '<input type="text" class="slang__meaning-input" value="' + esc(oldText) + '" />';
          const input = span.querySelector('input');
          input.focus(); input.select();
          const save = async () => {
            const val = (input.value || '').trim();
            span.textContent = val || '（点此填写释义）';
            try {
              const resp = await bridge.apiPost(`${API}/action`, { action: "slang_update_meaning", word, meaning: val });
              toast((resp && resp.message) || '已保存', !resp || !resp.ok);
            } catch (err) { toast(err.message || String(err), true); }
          };
          input.addEventListener('blur', save, { once: true });
          input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
            if (e.key === 'Escape') { span.textContent = oldText || '（点此填写释义）'; }
          });
        });
      });
    }
    if (!recs.length) {
      list.innerHTML = '<div class="motive__empty">还没有黑话记录，群里多聊几句就能挖到</div>';
      return;
    }
    list.innerHTML = recs.map((r) =>
      '<div class="slang__row" data-group="' + esc(r.group || '') + '">' +
        '<span class="slang__word">' + esc(r.word || '') + '</span>' +
        '<span class="slang__hits">' + esc(String(r.hits || 0)) + ' 次</span>' +
        '<span class="slang__group">' + esc(r.group || '') + '</span>' +
        '<button type="button" class="slang__del" title="清空此群">×</button>' +
      '</div>'
    ).join('');
    list.querySelectorAll('.slang__del').forEach((btn) => {
      btn.onclick = async () => {
        const row = btn.closest('.slang__row');
        if (!row) return;
        try {
          const resp = await bridge.apiPost(`${API}/action`, { action: "slang_clear", group: row.dataset.group });
          toast((resp && resp.message) || "已清空", !resp || !resp.ok);
          await Promise.all([loadRemotePrefs(), refresh()]);
        } catch (err) { toast(err.message || String(err), true); }
      };
    });
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
    if (el.motiveList) {
      const list = (m.items || []).map((it) =>
        `<div class="motive__row" data-mid="${esc(it.id || "")}">` +
          `<span class="motive__text">${esc(it.text || "")}</span>` +
          `<span class="motive__meta">${esc(SCOPE_NAMES[it.scope] || it.scope || "—")}` +
            `${it.private_used ? " · 已用于私聊" : ""}` +
            `${it.age_hours != null ? ` · ${it.age_hours}h前` : ""}</span>` +
          `<button type="button" class="motive__del" title="删除">×</button>` +
        `</div>`
      ).join("");
      el.motiveList.innerHTML = list || '<div class="motive__empty">还没有心事，摸鱼沉淀或手动添加</div>';
      el.motiveList.querySelectorAll(".motive__del").forEach((btn) => {
        btn.onclick = async () => {
          const rowEl = btn.closest(".motive__row");
          const mid = rowEl && rowEl.dataset.mid;
          if (!mid) return;
          try {
            const resp = await bridge.apiPost(`${API}/action`, { action: "motive_delete", id: mid });
            toast((resp && resp.message) || "已删除", !resp || !resp.ok);
            await refresh();
          } catch (err) { toast(err.message || String(err), true); }
        };
      });
    }
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
    // 用隐藏 mirror 精确量内容高度：textarea 的 scrollHeight 在某些 WebView
    // 会被自身 rows/box 限制，量不准；mirror 用完全相同的样式但 height:auto。
    let mirror = node.__plMirror;
    if (!mirror) {
      mirror = document.createElement("div");
      mirror.setAttribute("aria-hidden", "true");
      mirror.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;" +
        "left:-9999px;top:0;white-space:pre-wrap;word-wrap:break-word;box-sizing:border-box;";
      document.body.appendChild(mirror);
      node.__plMirror = mirror;
    }
    const cs = getComputedStyle(node);
    for (const prop of ["fontFamily", "fontSize", "fontWeight", "lineHeight",
                        "letterSpacing", "paddingTop", "paddingRight", "paddingBottom",
                        "paddingLeft", "borderTopWidth", "borderBottomWidth"]) {
      mirror.style[prop] = cs[prop];
    }
    const grow = () => {
      // 复制 textarea 实际内容宽度给 mirror，让换行位置完全一致
      mirror.style.width = node.clientWidth + "px";
      mirror.textContent = (node.value || "") + "\n";
      const h = mirror.offsetHeight;
      if (h > 0) node.style.height = h + "px";
    };
    grow();
    requestAnimationFrame(() => requestAnimationFrame(grow));
    setTimeout(grow, 50);
    setTimeout(grow, 300);
    if (!node.__plGrowInput) {
      node.__plGrowInput = true;
      node.addEventListener("input", grow);
    }
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

  // 高级项：默认折叠，避免 160+ 项把面板堆满；仍在各分页可展开细调
  const ADVANCED_PREFIXES = [
    // 只把真正的微调项收进折叠；模块开关和常用参数直接显示
    "出站_孤闭合", "出站_未闭合", "出站_思考标签大小写", "出站_修剪空白", "出站_压缩空行",
    "出站_八股_保护", "出站_八股_禁用规则", "出站_八股_演练",
    "场景感知_严格", "场景感知_总结每", "场景感知_总结间隔",
    "节奏_打印", "节奏_演练", "节奏_表格", "节奏_花括号",
    "拟人_情绪衰减", "日程_参考历史", "日程_参考最近",
    "防抖_锁超时", "防抖_群聊最长", "防抖_私聊最长",
  ];
  const isAdvanced = (key) => ADVANCED_PREFIXES.some((p) => key.startsWith(p));

  // 功能小节：把同一分页里不同用途的设置用标题隔开，避免混在一起看花眼
  const SECTION_OF = (key) => {
    if (key === "模块_群聊" || key.startsWith("群聊接话") || key.startsWith("群聊插话")
        || key.startsWith("群聊命中词") || key.startsWith("群聊自发") || key === "群聊冷场判定时间"
        || key === "群聊独立分区配置") return "接话与插话";
    if (key.startsWith("跟读_") || key === "模块_跟读") return "复读";
    if (key.startsWith("黑话_") || key === "模块_黑话") return "群黑话";
    if (key.startsWith("模块_黑名单")) return "黑名单";
    if (key === "模块_撤回取消") return "撤回取消";
    if (key.startsWith("戳一戳_") || key === "模块_戳一戳") return "戳一戳";
    if (key.startsWith("出站_八股") || key === "模块_八股清洗") return "八股清洗";
    if (key.startsWith("出站_") || key === "模块_思考过滤") return "思考过滤";
    if (key.startsWith("场景感知_") || key === "模块_上下文") return "场景感知";
    if (key.startsWith("拟人_")) return "拟人性格";
    if (key.startsWith("节奏_") || key === "模块_节奏" || TYPING_KEYS.includes(key)) return "发送节奏";
    if (key.startsWith("防抖_") || key === "模块_防抖") return "消息防抖";
    if (key.startsWith("日程") || key === "模块_日程" || key.startsWith("天气_") || key === "模块_天气") return "日程与天气";
    if (key.startsWith("角色_") || key === "模块_身份") return "身份";
    if (PRIVATE_KEYS.includes(key) || key === "模块_私聊" || key === "模块_黑名单_拦主动") return "私聊";
    if (WORKER_KEYS.includes(key) || key.startsWith("摸鱼_") || key === "模块_摸鱼") return "摸鱼";
    return "通用";
  };

  function buildConf(schema, values, options) {
    if (confBuilt) return;
    options = options || {};
    // 每个分頁各自一个 form，不共享滚动；先收集再分配
    const buckets = { core: [], private: [], group: [], slang: [], interact: [], blacklist: [], worker: [], rhythm: [], outbound: [], schedule: [], context: [], debounce: [], identity: [] };
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
          // 直接按内容行数设 rows：不依赖 JS 时机，WebView 里也能正确显示全文
          const raw = String(value ?? "");
          // 宁可框大也不裁切：按每个中文约 20 字/行的保守值估算，再留 3 行余量。
          // 上限放宽到 120 行，足以覆盖任何正常长度的人设文本。
          const estLines = raw.split("\n").reduce((n, line) => {
            return n + Math.max(1, Math.ceil(line.length / 20));
          }, 0);
          const rows = Math.max(4, Math.min(120, estLines + 3));
          // 展示态用 pre（无框、按内容完整展开），点击切换成 textarea 编辑
          return (
            `<div class="field field--wide longtext" data-key="${esc(key)}" data-type="str">` +
            `<label>${esc(label)} <em class="longtext__edit">点击编辑</em></label>` +
            `<pre class="longtext__view" title="点击编辑">${esc(raw) || "（留空使用内置模板）"}</pre>` +
            `<textarea id="f-${esc(key)}" class="field__text longtext__input" data-key="${esc(key)}" data-type="str" ` +
            `rows="${rows}" placeholder="留空使用内置模板" hidden>${esc(raw)}</textarea></div>`
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
        (buckets[group] || buckets.core).push({ html: field, section: SECTION_OF(key) });
    });
    el.confForms.forEach((form) => {
      const g = form.dataset.conf;
      const renderTagged = (arr) => {
        let out = "", last = "";
        arr.forEach((t) => {
          if (t.section !== last) {
            out += '<h3 class="conf__sub">' + esc(t.section) + '</h3>';
            last = t.section;
          }
          out += t.html;
        });
        return out;
      };
      let html = (buckets[g] || []).length
        ? renderTagged(buckets[g])
        : '<div class="conf__empty">此分栏暂无配置项</div>';
      form.innerHTML = html;
      // 多行框按内容撑高，避免清单/模板出现内部滚动条
      form.querySelectorAll("textarea").forEach(autoGrow);
    form.querySelectorAll(".longtext").forEach((wrap) => {
      const view = wrap.querySelector(".longtext__view");
      const input = wrap.querySelector(".longtext__input");
      if (!view || !input) return;
      view.addEventListener("click", () => {
        view.hidden = true;
        input.hidden = false;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        autoGrow(input);
      });
      input.addEventListener("blur", () => {
        view.textContent = input.value || "（留空使用内置模板）";
        input.hidden = true;
        view.hidden = false;
      });
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
          if (node.tagName === "TEXTAREA") {
            if (typeof autoGrow === "function") autoGrow(node);
            // 同步展示态 pre（长文本字段）
            const wrap = node.closest(".longtext");
            const view = wrap && wrap.querySelector(".longtext__view");
            if (view) view.textContent = node.value || "（留空使用内置模板）";
          }
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

  function applyZoom(scale, persist = true) {
    // localStorage 读回来是字符串，必须先转数字再比对，否则 includes 永远 false，
    // 每次打开都被重置成 100%。
    const num = Number(scale);
    const v = Number.isFinite(num) && ZOOM_STEPS.some((s) => Math.abs(s - num) < 0.001)
      ? ZOOM_STEPS.find((s) => Math.abs(s - num) < 0.001)
      : 1;
    document.documentElement.style.setProperty("--fs", String(v));
    if (el.zoomVal) el.zoomVal.textContent = `${Math.round(v * 100)}%`;
    if (el.zoomIn) el.zoomIn.disabled = v >= ZOOM_STEPS[ZOOM_STEPS.length - 1];
    if (el.zoomOut) el.zoomOut.disabled = v <= ZOOM_STEPS[0];
    try {
      window.localStorage.setItem(ZOOM_KEY, String(v));
    } catch (err) {
      /* sandbox iframe 下 localStorage 不可用时走后端持久化 */
    }
    if (persist) {
      userTouchedPrefs.add("zoom");
      queueRemotePrefs({ zoom: v });
    }
    requestAnimationFrame(() => updateNavFrameMetrics());
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
    applyZoom(next, true);
  }

  function initZoom() {
    let saved = 1;
    try {
      saved = Number(window.localStorage.getItem(ZOOM_KEY)) || 1;
    } catch (err) {
      saved = 1;
    }
    applyZoom(saved, false);
    if (el.zoomIn) el.zoomIn.addEventListener("click", () => stepZoom(1));
    if (el.zoomOut) el.zoomOut.addEventListener("click", () => stepZoom(-1));
  }

  // 分区 tab：每个分组是独立分頁，互不共享滚动
  const TAB_KEY = "pl_panel_tab";
  const TABS = ["overview", "private", "group", "slang", "interact", "worker", "rhythm", "outbound", "schedule", "debounce", "identity", "context", "blacklist"];

  function setTab(name, persist = true) {
    const tab = TABS.includes(name) ? name : "overview";
    const tabItems = Array.from(document.querySelectorAll("#tabs li[data-tab]"));
    let activeIdx = 0;
    let activeEl = null;
    tabItems.forEach((li, i) => {
      const active = li.dataset.tab === tab;
      li.classList.toggle("is-selected", active);
      li.setAttribute("aria-selected", active ? "true" : "false");
      if (active) {
        activeIdx = i;
        activeEl = li;
      }
    });
    const meta = document.getElementById("navIndexMeta");
    if (meta && tabItems.length) {
      const cur = String(activeIdx + 1).padStart(2, "0");
      const total = String(tabItems.length).padStart(2, "0");
      meta.textContent = `${cur} / ${total}`;
    }
    if (activeEl && typeof activeEl.scrollIntoView === "function") {
      activeEl.scrollIntoView({ block: "nearest" });
    }
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
    if (persist) {
      userTouchedPrefs.add("tab");
      queueRemotePrefs({ tab });
    }
  }

  function initTabs() {
    let saved = "overview";
    try {
      saved = window.localStorage.getItem(TAB_KEY) || "overview";
      if (!TABS.includes(saved)) saved = "overview";
    } catch (err) {}
    setTab(saved, false);
    document.querySelectorAll("#tabs li").forEach((li) => {
      if (!li.dataset.tab) return; // 分组标题不可点击
      li.addEventListener("click", () => setTab(li.dataset.tab, true));
    });
  }

  // 群聊列表的折叠，状态记在本地，下次打开还是上次那样
  const FOLD_KEY = "pl_group_folded";

  function setGroupFold(folded, persist = true) {
    if (!el.groupSec || !el.groupFold) return;
    el.groupSec.classList.toggle("folded", folded);
    el.groupFold.textContent = folded ? "展开" : "收起";
    el.groupFold.setAttribute("aria-expanded", folded ? "false" : "true");
    try {
      window.localStorage.setItem(FOLD_KEY, folded ? "1" : "0");
    } catch (err) {}
    if (persist) {
      userTouchedPrefs.add("group_fold");
      queueRemotePrefs({ group_fold: Boolean(folded) });
    }
  }

  function initGroupFold() {
    let folded = false;
    try {
      folded = window.localStorage.getItem(FOLD_KEY) === "1";
    } catch (err) {
      folded = false;
    }
    setGroupFold(folded, false);
    if (el.groupFold) {
      el.groupFold.addEventListener("click", () => {
        setGroupFold(!el.groupSec.classList.contains("folded"), true);
      });
    }
  }

  async function loadRemotePrefs() {
    if (!bridge) return;
    try {
      const resp = await bridge.apiGet(`${API}/prefs`);
      const p = resp && resp.prefs;
      if (!p || typeof p !== "object") return;
      if (p.zoom != null && !userTouchedPrefs.has("zoom")) {
        applyZoom(p.zoom, false);
      }
      const nextTheme = (p.theme === "light" || p.theme === "dark") && !userTouchedPrefs.has("theme")
        ? p.theme : null;
      const nextMono = typeof p.mono === "boolean" && !userTouchedPrefs.has("mono")
        ? p.mono : undefined;
      if (nextTheme !== null || nextMono !== undefined) {
        applyThemeState(nextTheme, nextMono, false);
      }
      if (typeof p.tab === "string" && TABS.includes(p.tab) && !userTouchedPrefs.has("tab")) {
        setTab(p.tab, false);
      }
      if (typeof p.group_fold === "boolean" && !userTouchedPrefs.has("group_fold")) {
        setGroupFold(p.group_fold, false);
      }
      if (Object.keys(pendingPrefPatch).length) {
        queueRemotePrefs({});
      }
    } catch (err) {
      /* 兼容老后端或网络抖动，静默回退默认值 */
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
    if (el.slangAnalyze) el.slangAnalyze.addEventListener("click", async () => {
      toast("解析中，模型可能会调用搜索/MCP…", false);
      try {
        const resp = await bridge.apiPost(`${API}/action`, { action: "slang_analyze" });
        toast((resp && resp.message) || "已解析", !resp || !resp.ok);
        await refresh();
      } catch (err) { toast(err.message || String(err), true); }
    });
    if (el.slangClearAll) el.slangClearAll.addEventListener("click", async () => {
      try {
        const resp = await bridge.apiPost(`${API}/action`, { action: "slang_clear" });
        toast((resp && resp.message) || "已清空", !resp || !resp.ok);
        await refresh();
      } catch (err) { toast(err.message || String(err), true); }
    });
    document.querySelectorAll("[data-preset]").forEach((btn) => {
      btn.onclick = async () => {
        try {
          const resp = await bridge.apiPost(`${API}/action`, { action: "preset_apply", name: btn.dataset.preset });
          toast((resp && resp.message) || "已应用", !resp || !resp.ok);
          await refresh();
          await syncConfigFields();
          if (el.presetNote) el.presetNote.textContent = "已应用：" + (btn.textContent || "").trim();
        } catch (err) { toast(err.message || String(err), true); }
      };
    });
    if (el.motiveAddBtn) el.motiveAddBtn.addEventListener("click", async () => {
      const text = (el.motiveInput && el.motiveInput.value || "").trim();
      if (!text) { toast("请先填写心事内容", true); return; }
      try {
        const resp = await bridge.apiPost(`${API}/action`, { action: "motive_add", text });
        toast((resp && resp.message) || "已添加", !resp || !resp.ok);
        if (resp && resp.ok && el.motiveInput) el.motiveInput.value = "";
        await refresh();
      } catch (err) { toast(err.message || String(err), true); }
    });
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
    initNavFrame();
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
