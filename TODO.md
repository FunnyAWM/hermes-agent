# TODO.md · PC 端（Hermes 桌面 + 内核）

> **范围**：本文件只覆盖 PC 端——Windows / Linux 上的 Hermes 桌面客户端与它依赖的内核。
> **Android 客户端、同步中转不在本文件**，另有工作区。
> **事实源**：`PRD.md` v0.11（2026-11-03）。功能范围与验收判据以 PRD 为准，本文件只把它拆成可执行项。

> **本轮授权**：有现有代码、有需求文档；用户要求继续按文档实现和验证 PC 待办。本轮完成 T-04 排序修复、T-05 数据侧检查、T-10 样本准备与 T-15 既有产物复核，并修正相应门禁问题。
> **代码基座**：`f5e0dd8e9b`（2026-11-03 从 origin/main 同步，仓库仍无稳定 tag 可校验）。本文件行号以当前 `HEAD` `19f122842c` 为准。
> **状态口径**：仅三种——`未完成` / `已完成` / `完成受阻`。`已完成` 必须带验证命令与证据路径。

---

## 0. 现状差异与裁决

PRD 初版是按「桌面端不改造」写的，代码现状与它的假设并不完全吻合。五条已全部裁决，
后面的待办不再受阻于决策。

| # | PRD 要求 | 代码现状（同步快照 `f5e0dd8e9b`） | 裁决 |
| --- | --- | --- | --- |
| D1 | §5.4 初版：**不 fork、不打补丁** | 本仓库已按 fork 使用（`AGENTS_PROJECT.md` §1） | **已裁决：允许受控补丁。** PRD §5.4 已改为「fork + 受控补丁」并附登记表（v0.2）。补丁必须登记、必须带测试 |
| D2 | §7.2：`MEMORY.md` 2,200 / `USER.md` 1,375 字符 | **已实现**：`agent/agent_init.py:1336-1337` 默认值即 2200 / 1375 | **已裁决：只验证，不改代码。** 见 T-01。注意默认值可被 `config.yaml` 的 `memory.*` 覆盖，验证时须读运行时值 |
| D3 | §7.1：`<数据根>/memories/MEMORY.md` | **已实现**：`tools/memory_tool.py:38-40`，`get_memory_dir()` = `HERMES_HOME/memories` | **已裁决：不单独处理。** 目录契约天然吻合，由 T-02 顺带覆盖 |
| D4 | §7.5：SOUL → AGENTS → MEMORY → USER → 笔记清单 → 附件 | **已对齐（T-03）**：volatile 层改为 `[MEMORY, USER, 笔记清单, skills 索引, ...]`，前五项相对顺序与 PRD 一致 | **已裁决并落地：按 PRD 对齐，改内核。** 改动点、代价与验证见 §4；执行项 T-03 已完成 |
| D5 | 记 04 / 05：`notes/` 笔记分片 + 清单注入 | **已实现（T-04）**：`hermes_notes/store.py` 扫描/渲染 + `agent/system_prompt.py` 注入；插件层无接入点，按 PRD §5.4 登记为受控补丁 | **已裁决：允许新增开发。** 目录形态为「按信息类别分一层子目录」，清单上限 64 条（PRD v0.3）。实现与证据见 T-04 |

**三个数字先记住**：2200 / 1375 是内核默认值，不是 PRD 带来的新需求。PC 端一部分活是「验证它确实生效」，
而不是「实现它」——但 D4、D5 是真的要写代码。

---

## 1. 待办

| 待办 ID | 来源验收项 | 描述 | 状态 | 受阻原因与所需系统环境 | 验证命令 / 证据路径 |
| --- | --- | --- | --- | --- | --- |
| T-01 | §7.2 / 记 03 / G5 | 验证记忆上限在桌面生效：写超 2,200 被拒绝、文件未被截断；超限返回当前剩余可用字符数 | 已完成 | — | `.venv\Scripts\python.exe` 临时 `HERMES_HOME` 行为自检；`evidence/t01.md` |
| T-02 | §7.1 / G2 | 验证数据根结构：`memories/MEMORY.md`、`memories/USER.md`、`SOUL.md`、`AGENTS.md` 位置与 PRD 一致 | 已完成 | — | `.venv\Scripts\python.exe` 默认配置与 `get_memory_dir()` 自检 → `evidence/t02.md` |
| T-03 | §7.5 / D4 | 对齐拼装顺序：把 `skills_prompt` 从 volatile 层首位移到 `MEMORY` / `USER` 之后，使 SOUL → AGENTS → MEMORY → USER 连续 | 已完成 | — | `agent/system_prompt.py:793` 调整为 MEMORY / USER → skills；改前后顺序见 `evidence/t03-before.md`、`evidence/t03-after.md` |
| T-04 | 记 04 / 记 05 / §7.1 / §7.4 | 实现 `notes/` 笔记分片与「显示名/名称 — 摘要」清单注入：`notes/<category>/<topic>.md`，**目录名英文小写、固定枚举 7 类**（常量单点定义，目录与显示名成对存），清单上限 **64 条**，注入位置在 `USER` 之后 | 已完成 | — | `scripts/run_tests.sh tests/agent/test_notes_prompt.py` + `verify_pc_memory_notes.py` → `evidence/t04.md`；2026-11-03 补 `updated` 排序回归，修复前失败、修复后通过 → `evidence/t04-updated.md` |
| T-05 | 记 06 | 核实超长整理链：摘要 → 抽取事实写入笔记 → 开新会话；抽取结果格式须与手机端一致 | 未完成 | 数据侧 21 项自检已通过：接近上限拒绝不改文件、整理后新会话重新读取。自动抽取笔记及连续对话实测仍未验证；PC 可用终端/文件工具，专用工具尚未裁决 | `.venv/Scripts/python.exe -X utf8 scripts/verify_consolidation_chain.py` → `evidence/t05.md`；仍需真实会话整理前后 session 与笔记 diff |
| T-06 | §5.4 版本 | 钉版：版本号与提交号同时固定。方案已定 `0.22.5-fork.<N>`，桌面 `apps/desktop/package.json` = `0.22.5-fork.1`，Python `pyproject.toml` = `0.22.5+fork1`（PEP 440 写法，同义不同串，见 PRD §5.4） | 完成受阻 | tag 未打（用户决定暂不补），`git describe --tags` 仍为空，只能用提交号 `bf0203a` 做锚点。版本号本身已写入，可被读 | `git describe --tags` 有输出且与桌面包版本一致 → `evidence/t06.md`。装机后另核对文件属性 → 产品版本是否带上 `fork` 号（Windows 四段版本可能吞掉 pre-release 段，吞了就靠卸载项显示名区分） |
| T-07 | §5.4 跟随节奏 | 建立「跟随上游稳定 Release tag」的升级流程：`git fetch upstream --tags` → `sync/vX.Y.Z` 分支 → 门禁 + 回归 → 进 `main`。**前置：加 `upstream` remote（当前只有 `origin`）** | 未完成 | 依赖 T-06 的版本语义；上游 remote 未配置 | 升级脚本 + 一份 tag 升级记录 → `evidence/t07.md` |
| T-08 | §5.4 改造边界 | 清点所有定制，逐条归入「配置 / 插件 / 客户端 / 受控补丁」四类；补丁部分必须与 PRD §5.4 登记表逐条对得上 | 已完成 | — | 当前路径与插件能力审阅；`evidence/t08.md` |
| T-09a | 远 04 | 设备令牌：一次性配对码换长期 token，可单独撤销。接入点是 `dashboard_auth/token_auth.py` 的 provider 插槽 + `register_token_route`；码语义对齐 `gateway/pairing.py`（8 位、1 小时、限速、锁定、不落日志）。先验证能否插件式接入，不能则按 PRD §5.4 登记补丁 | 已完成 | — | `plugins/dashboard_auth/device_pairing/`（纯插件，无内核改动）；23 passed + 真实服务器验收链 → `evidence/t09a.md` |
| T-09b | 远 01 | 局域网暴露：**必须显式开关**才允许非回环监听（默认 `127.0.0.1` 的现状不许改），开启时打印本机地址与配对入口；确认 Android 原生客户端不受 CORS 限制（`allow_origin_regex` 只限 localhost origin，那是浏览器约束） | 完成受阻 | PC 侧开关、非回环门禁与令牌链已实测。最终判据「手机可达且必须带 token」需 Android 工作区真机联调（T-09c）；启动横幅未枚举各网卡 LAN IP（易用性缺口） | 已过：默认 `netstat` 仅 `127.0.0.1`、显式开启后无凭据 401/403 → `evidence/t09b.md`；待补：真机可达 → 并入 `evidence/t09c.md` |
| T-09c | 远 02 / 03 | 模式标识与自动降级：PC 侧提供可判定的健康状态，手机显示「本机 / 远程」并在连不上时静默回退、保留草稿。UI 在 Android 侧，PC 只负责状态可信 | 完成受阻 | 判定逻辑、3 秒回退计时与草稿保留均在 Android 端。PC 侧原料已就绪：`GET /api/status`、结构化 401、WS close code | 安卓实现后跑 `hermes serve` 实测断网/杀进程 → `evidence/t09c.md` |
| T-10 | §9.3 两端可比 | 建固定记忆样本 + 10 个固定问题，作为「同一份记忆两个答案」的比对基线 | 完成受阻 | 样本、事实判据、SHA-256 与 PC 加载链路已就绪。需 Android 工作区读取同套样本，并在两端用同一模型与参数完成首轮十题答案比较；当前无跨端实测 | `evidence/fixtures/memory-comparison/` + `.venv/Scripts/python.exe -X utf8 scripts/verify_memory_comparison.py` → `evidence/t10.md` |
| T-11 | §8.1 隐 02 | PC 端出站链路自查：除配置的模型端点外无其他外联 | 未完成 | — | 抓包 / `netstat` 记录 → `evidence/t11.md` |
| T-12 | §9.3 回归 | 升级不丢记忆：覆盖安装 NSIS 包（per-user）后 `memories/`、`notes/` 保留 | 完成受阻 | T-15 已有 NSIS 试验包；当前为脏工作区构建，T-06 正式 tag 仍缺。需固定源码的构建及隔离 Windows 用户/虚拟机，实际覆盖安装后比较记忆、笔记与会话 | 升级前后目录 diff → `evidence/t12.md`；现有产物记录 → `evidence/t15.md` |
| T-13 | §11 R3 | 上游记忆格式漂移监控：升级时检查 `MEMORY.md` 解析格式与字符计数口径是否变化 | 未完成 | 依赖 T-07 | 升级 checklist 中一栏 + 历次检查记录 → `evidence/t13.md` |
| T-14 | §11 R7 | 路线文档仍是旧形态（含「内置运行时」「把内核装进沙箱」），与白皮书 Ver 1.2 冲突，需对齐 | 未完成 | 文档不在本仓库，需取得原件 | 对齐后的文档链接 → `evidence/t14.md` |
| T-15 | §5.5 / Q6 | 打包改造：Windows 由 MSIX-only 改为 `nsis` + `portable`，Linux/mac 维持 `AppImage` / `dmg`+`zip`。覆盖层 `apps/desktop/electron-builder.fork.cjs` 已建，上游 `electron-builder.config.cjs` 未改 | 已完成 | — | 已有 build/pack 日志与 `release/*-setup.exe`、`*-portable.exe`；本轮核对两份文件大小、SHA-256 与产品版本 → `evidence/t15.md`。产物未签名且构建戳 dirty，仅证明 Windows 打包通过；覆盖安装见 T-12 |

---

## 2. 明确不做（避免被 PRD 的功能表误导）

以下条目在 PRD 里是 Android 的开发范围，PC 端不承接：记 07（记忆页）、记 08–09（留档 / 导出）、
聊 01–07（流式、中断、会话、错误态、草稿、模型切换）、文 01–09（文件选择器、分享意图、文本抽取、OCR）、
同 01–09（配对、加密、定序、合并、轮换）、设 01–06（首启向导、模型配置、遥测开关）。

PC 端对它们的责任只有一条：**不要让自己的实现破坏「两端共享目录」这一前提**（PRD §5.3）。

---

## 3. 待决

`D` 编号留给 §0 的差异项；这里改用 `Q`，避免两套编号混用。

| 编号 | 决策 | 阻塞 | 状态与建议 |
| --- | --- | --- | --- |
| Q1 | 桌面端到底改不改内核？ | — | **已裁决**：允许受控补丁，PRD §5.4 v0.2 已落纸。原 §0 D1 |
| Q2 | PC 端验收人 | — | **已裁决：自验收。** 验收人即使用者本人；每项仍须留下可复跑的验证命令与 `evidence/` 证据，不因自验收就放宽 |
| Q3 | 是否保留「远程模式」入口（PRD 待决 3） | — | **已裁决：做。** PRD §6.6 v0.6 已升为必做；顺序固定：先远 04（鉴权）再远 01（连接），见 T-09a → T-09b |
| Q4 | 笔记清单上限（PRD 待决 2） | — | **已裁决：64 条（暂定）**，PRD §7.4 v0.3 已改。剩余工作只是按真实笔记量校准 |
| Q5 | 笔记类别是否固定枚举 | — | **已裁决：固定枚举**，初版 7 类，**目录名英文**（`work/` `study/` `life/` `health/` `finance/` `people/` `other/`）、显示名中文，PRD §7.1 v0.5。规则：写入严格校验、读取宽松归 `other/`、扩张只改常量 + 表 |
| Q6 | 交付形态与打包目标 | T-15、T-12 | **已裁决：Electron 打包，跨平台。** Windows 走 `nsis` + `portable`（弃 MSIX-only），Linux `AppImage`，mac `dmg` + `zip`；新增 `apps/desktop/electron-builder.fork.cjs` 覆盖层，上游主配置逐字不动；不做自动更新。PRD §5.5 v0.7 |
| Q7 | fork 的版本号方案 | T-06、T-12 | **已裁决：跟上游走 `上游版本-fork.<N>`**（当前 `0.22.5-fork.1`），已写入 `apps/desktop/package.json`，PRD §5.4 v0.8。换基座 N 归 1，同基座重出包 N+1 |

**Q3 说明（远程模式到底是什么）**

手机 App 默认是**本机模式**：直连模型端点，只做对话、记忆、读文件，不跑工具、不常驻、不监听端口（PRD §5.2）。
**远程模式**是另一条路：手机连上桌面网关，把它当成「前端」，真正的内核跑在桌面上——于是手机突然能用到
桌面才有的东西：终端命令、技能、定时任务、本地文件。

PRD 把它列为**可选、默认不走**，理由是「手机独自在外时，桌面不存在」，任何默认依赖另一台设备的设计会在最需要时失效。
但它不是没价值：它是唯一能让手机借用桌面完整能力的路径，砍掉就再没有第二个入口。

PC 端要承担的是 T-09a–c 三件：把令牌与配对做出来、把网关按开关暴露给手机、提供可信状态让手机显示模式并在断连时降级。**注意它带来一个新的攻击面**——
桌面网关背后是终端执行权，所以 PRD 已补 **远 04**：不默认监听 `0.0.0.0`、一次性配对码换令牌、令牌可撤销、拒绝即拒。
这条不是可选项，砍功能也不能砍它。

**决定做（2026-10-28）**，执行拆成 T-09a → T-09b → T-09c。勘察代码后有个好消息：桌面侧已有大半地基——

| 已有 | 位置 | 能否直接用 |
|---|---|---|
| Bearer 令牌鉴权（按路由逐条 opt-in、fail-closed、带审计） | `hermes_cli/dashboard_auth/token_auth.py` | 有 provider 插槽（`list_token_providers` / `register_token_route`），大概率能插件式接入，不必改内核 |
| 短时 WS ticket（30 秒、单次） | `hermes_cli/dashboard_auth/ws_tickets.py` | 手机侧可直接用，正好解决「WebSocket 升级不能带 Authorization」 |
| 一次性配对码（8 位、1 小时过期、限速、失败锁定、码不落日志） | `gateway/pairing.py` | 语义与远 04 完全对得上，但作用域是消息平台私聊，需抽象成通用配对 |
| 默认只监听回环 + Host 校验 + CORS 限 localhost | `hermes_cli/web_server.py:1351`、`web_server_boundary.py` | 现状已满足「不默认 `0.0.0.0`」；要补的是显式开关 |

所以真正要新写的只有两件：**设备令牌的签发与撤销**、**配对码换令牌的流程**。协议不用新起——
`/api/ws`（`web_routers/chat_ws.py`）就是现成的会话通道。

---

## 4. 附录：D4 详解（系统提示词的**实际**拼装顺序）

这一节是为 T-03 服务的。结论先给：**PRD §7.5 的六项里，只有一项是真的冲突，两项是缺段，一项走了另一条通道。**

### 4.1 它不是一条线性列表，是三层

`build_system_prompt`（`agent/system_prompt.py:810`）把 prompt 拼成三段，用 `\n\n` 连接（`:807` 的 `_join_tier`）：

```
stable  →  context  →  volatile
```

分层只为前缀缓存：stable 段在同一次会话里逐字节不变，改一处只让后面的段重算。
所以「PRD 要求的顺序」和「代码里的顺序」不是同一件事——三段内部的顺序才可比。

| 层 | 实际内容（按先后） | 代码位置 |
| --- | --- | --- |
| stable | `SOUL.md`（或缺省人格）→ help 指引 → 通用 / 工具 / 模型指引 → auto-load 技能钉 → coding 提纲 | `:756-776`、`:522` 的 `_identity_parts` |
| context | 调用方 `system_message` → **项目上下文文件（`AGENTS.md`）** → 工作区快照 → 平台提示 | `:779-790`、`:715-728` |
| volatile | `MEMORY` → `USER` → **笔记清单** → skills 索引 → 插件段 → profile 行 → 时间戳 → 运行环境提示 | `:793-800`、`:522-540` 的 `_memory_parts` |

### 4.2 与 PRD §7.5 逐项对位

| PRD 顺序 | 实际位置 | 结论 |
| --- | --- | --- |
| 1 `SOUL.md` | stable 层首位（`_identity_parts` 直接返回 SOUL，无则 `DEFAULT_AGENT_IDENTITY`） | **一致** |
| 2 `AGENTS.md` | context 层，紧跟 `system_message` | 相对顺序对；但它与 SOUL 之间隔着整个 stable 指引段，且**不保证加载**（见 4.3） |
| 3 `MEMORY.md` | volatile 层第 1 段 | **一致**（T-03 已把 skills 索引移到 `USER` 之后） |
| 4 `USER.md` | volatile 层第 3 段 | 与 MEMORY 相邻，顺序对 |
| 5 笔记清单 | volatile 层，紧跟 `USER` 之后（`_memory_parts` 末尾追加 `NOTES INDEX`） | **一致**（T-04 已实现） |
| 6 本轮附件 | 不进 system prompt，随 user 消息走 | **契约不同**：这是另一条通道，不是顺序问题 |

D4 的「顺序不齐」精确说是：**一个插入项（skills 索引）+ 一个缺失项（笔记清单）**，
`SOUL → AGENTS` 和 `MEMORY → USER` 这两段其实是对的。上一版 TODO 把它写成「顺序全乱」是过头了。
两项均已由 T-03 / T-04 收口（skills 索移到 `USER` 与笔记清单之后，清单注入补齐）。

### 4.3 三个容易漏的细节

1. **`AGENTS.md` 不是必加载项。** `build_context_files_prompt`（`agent/prompt_builder.py:1748`）是「首个命中即停」：
   `.hermes.md` / `HERMES.md` → `AGENTS.md` 链 → `CLAUDE.md` → `.cursorrules`（`:1770`）。
   桌面端数据目录里若存在 `HERMES.md`，`AGENTS.md` 整段不会被加载。**要比对顺序，先确认加载的是哪个文件**，
   否则对着一份空段讨论顺序没有意义。
2. **`MEMORY` / `USER` 块带横幅，计数口径必须两端一致。** `_render_block`（`tools/memory_tool_store.py:638`）输出
   `═══…` + `MEMORY (your personal notes) [37% — 812/2,200 chars]` + `═══…`，条目间用 `\n§\n` 连接（`:25`）。
   计数是 `len("\n§\n".join(entries))`（`:309`），**不含**标题、分隔线与百分比横幅。
   手机端若把横幅算进去，同一份记忆会显示两个百分比——这正是 PRD §7.5 说的「计数口径不同」，
   而它比顺序更容易被忽略，因为它不体现在肉眼可见的段落次序上。
3. **会话内不重渲染。** `format_for_system_prompt`（`:618`）返回的是 `load_from_disk` 时的**快照**；
   只有压缩后 `invalidate_system_prompt`（`agent/system_prompt.py:814`）才重读。所以「顺序对齐」不等于
   「会话内立刻一致」——PRD §5.3 已把这条记成桌面端固有副作用，不属本节缺陷。

### 4.4 最小补丁

已落地的写法（`agent/system_prompt.py:793`）：

```python
volatile_parts: list[str] = [*_memory_parts(agent), skills_prompt]
```

笔记清单没有单独的函数，而是在 `_memory_parts`（`:522`）末尾、`USER` 块之后追加：

```python
notes_index = NotesStore(get_hermes_home()).render_index()
if notes_index:
    parts.append(notes_index)
```

**代价要说清楚**：skills 索引放首位本来是有意的（`:790-792` 注释写着）——索引不变时它能留在复用前缀里。
移到 MEMORY 后面，等于**每次记忆写入都把 skills 索引一起推进重算区**，而记忆写入是高频事件，
这条缓存收益基本归零。这是为 PRD §7.5 付的确定成本，不是 bug，别在 review 时当性能回归改回去。

### 4.5 验证方式

抓取脚本已补：`scripts/capture_prompt_order.py`（构造最小 agent 桩 → `build_system_prompt` → 按 `═══` 与已知段头切分）。
判据仍是三条：

- `MEMORY` 块之前不再出现 skills 索引的任何一行；
- `MEMORY` → `USER` → 笔记清单 三段连续；
- `SOUL` 段确实存在（不是 `DEFAULT_AGENT_IDENTITY` 顶替）。
