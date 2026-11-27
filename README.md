# 自主生活与主动中枢（astrbot_plugin_proactive_life）

一体化活人感中枢——贯通 **入站准入 → 主动生活与状态注入 → 记忆与群聊感知 → 出站清洗与真人节奏** 全链路，让 Bot 不再只是「有问才答」的被动应答机，而是拥有真实作息、穿衣天气感、情绪余温、人物熟悉度与自然聊天节奏的鲜活角色。

本插件已深度融合多项常见独立插件能力，一套配置统一治理，无需重复安装。

---

## 核心功能模块

插件按消息生命周期分为 **4 大链路、14 个可独立开关的功能模块**（均可在 WebUI 中枢面板一键启停；关闭总开关后完全停止介入，细项配置完整保留）：

### 1. 入站准入与反射
| 模块 | 配置开关 | 能力说明 | 已整合替代 |
| --- | --- | --- | --- |
| **全局黑名单** | `模块_黑名单` | 支持 UID 列表与批量文本解析，在入站与主动发送双入口精准拦截，可选记录日志 | `global_blacklist` |
| **消息防抖** | `模块_防抖` | 自动合并连发消息与群内多人同时 `@` 的并发请求，支持图片上限、情绪拆分与表情过滤 | `smart_debounce` |
| **撤回取消** | `模块_撤回取消` | 监测用户撤回动作，自动中止尚未发出的回复或中断防抖聚合 | `recall_cancel` |
| **戳一戳互动** | `模块_戳一戳` | 响应戳一戳事件，支持结合近期语境由 LLM 生成自然回应 | `poke_to_llm` |
| **群聊跟读** | `模块_跟读` | 识别群内连续复读刷屏，按概率与冷却规则自然跟队 | `reread` |

### 2. 主动生活与状态注入
| 模块 | 配置开关 | 能力说明 | 已整合替代 |
| --- | --- | --- | --- |
| **私聊主动** | `模块_私聊` | 长时间静默后主动破冰；抛出问题未获回复时适时追问，并读取生活状态自然切入 | — |
| **群聊主动** | `模块_群聊` | 冷场破冰、自然插话接梗；察觉被嫌烦时自动静默闭嘴，支持分群独立覆盖策略 | — |
| **后台摸鱼** | `模块_摸鱼` | 空闲时自主上网探索（AstrBot 内置搜索 / MCP / B 站与贴吧热点），收获沉淀至心事池 | `web_surfer` |
| **天气与日程穿搭** | `模块_日程` / `模块_天气` | 拉取实况与预报天气驱动每日穿搭与日程生成；按时段精准推算「此刻在做」，同时注入主动与被动对话 | `life_scheduler` / `weather` |

### 3. 记忆、情绪与群感知
| 模块 | 配置开关 | 能力说明 | 已整合替代 |
| --- | --- | --- | --- |
| **上下文场景** | `模块_上下文` | 识别当前对话对象、生成短期对话总结、维护群成员印象画像 | `context_aware` |
| **群黑话挖掘** | `模块_黑话` | 自动统计群内高频梗词/黑话并调用 LLM 归纳释义，支持在面板手动编辑、分析与清理 | `slang_miner` |

### 4. 出站清洗与真人节奏
| 模块 | 配置开关 | 能力说明 | 已整合替代 |
| --- | --- | --- | --- |
| **思考过滤** | `模块_思考过滤` | 清理模型泄露的 `<think>` 块与裸思考前缀，避免推理草稿直接发给用户 | `thinking_filter` |
| **八股清洗** | `模块_八股清洗` | 剔除 AI 腔套话、官腔分点与格式化冗余，自动清理裁剪残留的行尾悬空标点 | `prose_polish` |
| **真人节奏** | `模块_节奏` | 按字数与条数上限将长回复拆分为短句气泡分段发送，修剪句末多余标点与颜文字，模拟真人打字节奏 | `human_rhythm` |

---

## 拟人化与沉浸增强

- **被动回复临时注入（`life_inject.py`）**：以低优先级临时拼入用户消息侧（不污染持久化系统提示词，保护 Prompt Cache），将「此刻时间/活动」「今日穿搭」「实时天气」「此刻心情」「可自然提起的心事」「跨会话人物记忆」自然带入日常聊天。
- **说话方式引导（`modules/speech_style.py`）**：提供 `gentle` / `normal` / `strict` 拟人语气强度，内建幂等标记与技术排障/代码长文场景自动豁免，闲聊像朋友随口聊、干活时不受短句束缚。
- **跨会话人物档案（`modules/person_memory.py`）**：持久化记录互动次数、熟悉度、上次聊到的话题与 7 天无互动熟悉度自然衰减，避免把刚发来的本轮消息误当成「上次说到」。
- **跨轮情绪状态机（`mood.py`）**：被冷落会委屈、被回应会开心、被喊闭嘴会闷闷不乐、摸鱼有收获会兴致高昂，持久化落盘并按衰减周期回归平静。
- **人设自动提炼与身份管理（`identity.py`）**：支持从当前 AstrBot 人格配置提取身份设定，并结合穿搭与天气生成更贴合角色口吻的日程安排。

---

## WebUI 中枢面板

在 AstrBot 后台进入 **「插件 → 自主生活与主动中枢 → 中枢面板」** 即可打开（通过插件页 Bridge 通信，不支持浏览器直接打开本地 HTML）。

界面采用黑白编辑式排版，支持浅色 / 深色 / 等宽三种主题、缩放调节与自适应布局，运行状态默认每 4 秒自动刷新。

### 13 个独立分栏

| 分组 | 分栏 | 功能概览 |
| --- | --- | --- |
| **总览** | **总览** | 重叠插件冲突提醒、近期活动日志、预设一键应用、心事池增删、全局调度与运行脉冲 |
| **互动** | **私聊** | 主动破冰、静默阈值、追问挂念策略 |
| | **群聊** | 破冰、插话、闭嘴判定及分群专属覆盖配置 |
| | **戳一戳 / 艾特** | 戳一戳响应、群聊跟读与撤回取消参数 |
| | **黑名单** | 全局黑名单 ID 列表、批量粘贴解析、主动发送拦截开关 |
| **内容** | **黑话** | 群黑话词典浏览、手动释义编辑、触发归纳分析与一键清空 |
| | **摸鱼** | 后台探索间隔、概率、搜索方向与模式 |
| | **日程** | 今日穿搭与日程展示、天气城市配置、带补充要求重新生成、手动改写保存 |
| | **出站清洗** | 思考过滤、八股清洗规则与拟人说话方式强度 |
| **系统** | **节奏** | 分段字数阈值、单次条数上限、打字间隔与标点/颜文字修剪 |
| | **防抖** | 合并窗口、图片上限、情绪拆分与实时聚合统计 |
| | **身份** | 角色人设重载、特征提炼与称呼映射 |
| | **上下文** | 场景感知、短期总结、成员印象配置与当前状态 |

### 面板 API

| 路由 | 方法 | 说明 |
| --- | --- | --- |
| `/astrbot_plugin_proactive_life/panel/status` | `GET` | 获取完整运行快照（冲突检测、日程/天气、黑名单、黑话、防抖、情绪、心事池等） |
| `/astrbot_plugin_proactive_life/panel/config` | `GET` / `POST` | 读取或热写入插件配置（受 `EDITABLE` 白名单保护，当前共 **175 项**配置键，四处严格对齐） |
| `/astrbot_plugin_proactive_life/panel/action` | `POST` | 执行面板动作：`schedule_generate`、`schedule_save`、`preset_apply`、`motive_add`、`motive_delete`、`persona_reload`、`persona_synthesize`、`slang_update_meaning`、`slang_analyze`、`slang_clear` |

### 配置热生效机制

`plugin.cfg` 采用直接映射 `AstrBotConfig` 的活视图（`_LiveConfig`）：无论在中枢面板还是 AstrBot 插件配置页修改并保存，均立即落盘并实时生效，无需重载插件。

---

## 架构与代码组织

```text
astrbot_plugin_proactive_life/
├── main.py                  # 插件主类、事件钩子、协调主循环、热点工具注册
├── config_defaults.py       # 175 项默认配置（与 _conf_schema.json / panel_config / app.js 四处对齐）
├── life_inject.py           # 被动回复临时生活状态/心情/心事/人物记忆注入
├── private_handler.py       # 私聊主动与追问链路
├── group_handler.py         # 群聊破冰/插话核心 + group_mute.py / group_prompt.py
├── worker_handler.py        # 后台摸鱼探索链路
├── agent_runner.py          # 统一 Agent 装配与调度
├── schedule_gen.py          # 天气感知穿搭与日程生成引擎
├── context_scene.py         # 场景感知、短期总结与成员印象
├── debounce.py              # 消息防抖合并与租约锁
├── rhythm_mixin.py          # 出站分段与 rhythm_trimmer.py 标点修剪
├── mood.py / motive_pool.py # 跨轮情绪状态机与心事话题池
├── identity.py              # 人设解析与身份提炼
├── web_api.py               # 面板路由入口 + panel_common / panel_status / panel_config / panel_actions
├── common.py / safe_io.py   # 共享日程/路径工具、原子异步 IO、handler_base.py / name_resolver.py
├── modules/                 # 融合业务子模块
│   ├── blacklist.py         # 全局黑名单准入
│   ├── recall_cancel.py     # 撤回取消
│   ├── poke.py              # 戳一戳互动 + interact_context.py
│   ├── reread.py            # 群聊跟读
│   ├── weather.py           # 天气拉取与缓存
│   ├── slang.py             # 群黑话挖掘 + slang_analyze.py
│   ├── person_memory.py     # 跨会话人物记忆与熟悉度衰减
│   ├── speech_style.py      # 拟人说话方式引导与技术场景豁免
│   ├── outbound.py          # 出站清洗编排
│   ├── think_filtering.py   # 思考过程过滤 + bare_thinking.py
│   └── prose_polish.py      # 八股文清洗 + polish_rules / polish_ai_rules / polish_bagu
├── pages/panel/             # WebUI 中枢面板前端（HTML / JS / CSS）
└── tests/                   # 自动化回归测试套件（203 passed）
```

### 数据持久化

全部运行数据通过 `StarTools.get_data_dir("astrbot_plugin_proactive_life")` 原子读写落盘：

- `schedule_data.json`：今日穿搭与日程（优先级高于旧版 `life_scheduler`）
- `weather_cache.json`：实况与预报天气缓存
- `motive_pool.json`：心事与话题池
- `private_state.json`：私聊交互计数与追问状态
- `person_memory.json`：跨会话人物熟悉度与上次话题记录
- `mood_state.json`：跨轮情绪状态持久化
- `mute_until.json`：各群静默截止时间
- `slang_dict.json`：群黑话词典（按需自动保存）

### 插件冲突自动检测

启动与打开面板总览时，会自动检测以下 **11 个重叠插件**是否同时启用并给出告警提示（只读提示，不会强行禁用）：

`life_scheduler` · `smart_debounce` · `context_aware` · `global_blacklist` · `recall_cancel` · `poke_to_llm` · `reread` · `thinking_filter` · `prose_polish` · `slang_miner` · `weather`

---

## 部署与维护

- **工具隔离**：内置热点工具命名为 `pl_bilibili_hot` 与 `pl_tieba_hot`，避免与第三方检索插件同名冲突。
- **修改生效**：修改配置即时热生效；修改 Python 源码后需在 AstrBot 后台重载插件。
- **回归测试**：修改代码后可在插件目录运行全量测试：
  ```bash
  pytest tests
  ```
- **维护约定**：增删任何配置项时，请保持 `_conf_schema.json`、`config_defaults.py`、`panel_config.py`（`EDITABLE`）与 `pages/panel/app.js`（`LABELS`）四处严格对齐。

---

## 更新日志

- **v1.5.3**
  - **面板白昼模式背景立绘调优**：拆分 `style.css` 中 `.bg-figure` 的深浅主题呼吸动画（修复原 `@keyframes figure-glow` 覆盖浅色 `opacity` 导致线条过重遮字的问题），白昼模式压低至淡雅水印（`0.075–0.12`）并对右侧正文区做平滑渐变柔化，深色模式保持不变
- **v1.5.2**
  - **八股清洗误伤收紧**：修复 `polish_ai_rules.py` 7 条过宽静态正则，防止「三十分钟 / 十分之一 / 满分十分」被误改为「很」，避免「我们一起…」「希望你…顺利」「都可以说」「话都说白了」被误删，并将「不仅…而且…」「既要…又要…还要…」由整句删除改为保留语义主干；新增误伤防护回归测试（203 passed）
- **v1.5.1**
  - **非阻塞天气请求**：`modules/weather.py` 新增 `get_weather_async` / `weather_prompt_async`，通过 `asyncio.to_thread` 卸载 Open-Meteo 网络请求与缓存读写；`ScheduleGenerator.generate()` 改为异步调用，消除事件循环阻塞
- **v1.5.0**
  - **全链路大融合**：正式整合黑名单准入、撤回取消、戳一戳、群聊跟读、真实天气穿搭、群黑话挖掘、跨会话人物记忆、情绪持久化、思考过滤、八股清洗与拟人说话方式引导，形成 14 模块一体化活人感中枢
  - **中枢面板升级**：扩展至 13 个分组分栏、10 项面板动作与 175 项四处对齐配置项，新增 11 项重叠插件冲突自动检测
  - **被动回复状态注入**：新增 `life_inject.py` 低优先级临时注入，兼顾真实生活感与 Prompt Cache 命中率
  - **实机体验与细节打磨**：
    - `speech_style` 补充幂等标记与技术/长文场景豁免，默认推荐柔和档位（`gentle`）
    - 修复 `person_memory` 同轮复读本轮消息为「上次说到」的时序问题，修复 7 天未互动熟悉度衰减分支
    - 修复凌晨早于首条日程 30 分钟以上错配白天活动的问题
    - `prose_polish` 新增行尾悬空标点清理，消除裁剪残留孤逗号
    - 新增 `tests/test_audit_fixes.py`，全量单元测试扩展至 **199 项全部通过**
- **v1.3.5**
  - **私聊分段与节奏对齐**：将私聊分段统一委托至 `rhythm_trimmer.split_message`，读取面板「每段字数 / 最多几条」配置并补充回归测试
- **v1.3.1**
  - 修复热点工具异步调用与公版路径脱敏；拆分 `web_api` / `group_handler` / `main`；引入 `safe_io.py` 原子读写与异步任务统一取消；完善面板响应式布局
- **v1.3.0**
  - 整合 `smart_debounce`，引入模块父级总开关与冲突检测，面板扩充至 8 分栏
- **v1.2.x**
  - 整合 `life_scheduler` 与 `context_aware`，加入跨轮情绪状态机与日程语境驱动
- **v1.1.x / v1.0.0**
  - 初版主动中枢发布，整合 `human_rhythm`、`web_surfer` 并上线 WebUI 中枢面板
