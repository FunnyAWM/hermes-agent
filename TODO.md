# TODO.md · PC 端（Hermes 桌面 + 内核）

> **范围**：本文件只覆盖 PC 端——Windows / Linux 上的 Hermes 桌面客户端与它依赖的内核。
> **Android 客户端、同步中转不在本文件**，另有工作区。
> **事实源**：`PRD.md` v0.19（2026-11-06）。功能范围与验收判据以 PRD 为准，本文件只把它拆成可执行项。
> **统一验收入口**：`scripts/pc_acceptance_manifest.json` + `scripts/verify_pc_acceptance.py`
> （结构校验恒跑，`--run` 跑每条完成项命令；受阻项必须带 `blocked_reason` 与 `evidence/handoff/` 交接物）。
> **跨端口径对齐（2026-11-06）**：以 `D:\AndroidProjects\kourichat-next\docs\PRD_ANDROID.md`、
> `AGENTS_PROJECT.md`、`TODO.md`（T-81 / T-88 / T-112 / T-113）为事实源，统一记忆上限、计数口径、
> `notes/docs/`、根提示词与超长整理口径。PC 侧原按 2200/1375 完成的验证需按新契约复验。

> **本轮补充授权**：PC 与手机统一使用持续会话，整理不新建会话；安装脚本与 PM 优先复用符合要求的本机软件环境。
> **本轮授权**：有现有代码、有需求文档；用户要求继续按文档实现和验证 PC 待办，并提供 T-14 原件查找目录。本轮处理 T-11 的被动更新门禁、客户端状态与真实 Windows 网络观察，并依据找到的原件完成 T-14 路线对齐。
> **代码基座**：`f5e0dd8e9b`（2026-11-03 从 origin/main 同步，仓库仍无稳定 tag 可校验）。历史行号以相应 `evidence/` 记录的提交快照为准。
> **状态口径**：仅三种——`未完成` / `已完成` / `完成受阻`。`已完成` 必须带验证命令与证据路径。

---

## 0. 现状差异与裁决

PRD 初版是按「桌面端不改造」写的，代码现状与它的假设并不完全吻合。五条已全部裁决，
后面的待办不再受阻于决策。

| # | PRD 要求 | 代码现状（同步快照 `f5e0dd8e9b`） | 裁决 |
| --- | --- | --- | --- |
| D1 | §5.4 初版：**不 fork、不打补丁** | 本仓库已按 fork 使用（`AGENTS_PROJECT.md` §1） | **已裁决：允许受控补丁。** PRD §5.4 已改为「fork + 受控补丁」并附登记表（v0.2）。补丁必须登记、必须带测试 |
| D2 | §7.2：原 `MEMORY.md` 2,200 / `USER.md` 1,375 字符 | 原实现：`agent/agent_init.py` 默认值 2200 / 1375 | **已被 D6 取代（2026-11-06）**：按 Android 现行契约改为 `MEMORY.md` 模型写入默认 3000、`USER.md` 不限；旧默认值只作历史记录 |
| D3 | §7.1：`<数据根>/memories/MEMORY.md` | **已实现**：`tools/memory_tool.py:38-40`，`get_memory_dir()` = `HERMES_HOME/memories` | **已裁决：不单独处理。** 目录契约天然吻合，由 T-02 顺带覆盖 |
| D4 | §7.5：SOUL → AGENTS → MEMORY → USER → 笔记清单 → 附件 | **已对齐（T-03）**：volatile 层改为 `[MEMORY, USER, 笔记清单, skills 索引, ...]`，前五项相对顺序与 PRD 一致 | **已裁决并落地：按 PRD 对齐，改内核。** 改动点、代价与验证见 §4；执行项 T-03 已完成 |
| D5 | 记 04 / 05：`notes/` 笔记分片 + 清单注入 | **已实现（T-04）**：`hermes_notes/store.py` 扫描/渲染 + `agent/system_prompt.py` 注入；插件层无接入点，按 PRD §5.4 登记为受控补丁 | **已裁决：允许新增开发。** 目录形态为「按信息类别分一层子目录」，清单上限 64 条（PRD v0.3）。实现与证据见 T-04 |
| D6 | 记 03：记忆上限 | Android 2026-11-05 修订：仅模型写入 `MEMORY.md` 受硬上限约束，默认 **3000**、可配置；`USER.md` 不设上限；合并上限取消 | **已裁决：按 Android 对齐。** PC 默认值与 `0 = 不限` 语义同步修改，见 T-17 |
| D7 | 记 03：计数口径 | Android `CharCount.kt` 按「码点 + 簇尾归并」计数，emoji 修饰符 / ZWJ / 国旗 / 标签序列算 1 | **已裁决：按 Android 对齐。** PC 实现等价函数并统一用于上限与用量，见 T-17 |
| D8 | 记 04 / 记 05：`notes/docs/` | Android 2026-11-06 新增固定例外目录：一层放平、文件名取来源、模型只读、不参与摘要与合并，但计入清单 | **已裁决：按 Android 对齐。** PC 增加 `docs/` 类别、索引与只读守卫，见 T-18 |
| D9 | §7.5：根提示词 | Android `SystemPrompt.ROOT_PROMPT` 始终位于最前，先于 SOUL | **已裁决：按 Android 对齐。** PC 新增等价根约定并置于 stable 层首位，工具名按 PC 适配，见 T-18 |
| D10 | 远 01–04：远程模式 | Android PRD 将远程模式排在 M5；`AGENTS_PROJECT.md` 当前范围写「M5 远程模式不做」；Android 无实现 | **已裁决：PC 不等真机。** T-09b / T-09c 保持完成受阻，PC 侧准备与契约测试保留 |
| D11 | 记 06：超长整理 | Android T-81 实现口径：摘要与整理解耦；事实由常规记忆工具轮写入 notes，不在压缩同轮抽取 | **已裁决：T-05 改写口径。** 以文件结果、会话连续性与失败不覆盖为判据 |
| D12 | 隐 02 / 隐 07：出站 | Android 默认仅模型端点；可选联网检索默认关，显式授权后才允许对应出站 | **已裁决：PRD 增补显式例外。** T-11 判据只覆盖默认配置 |

**三个数字先记住**：3000 是 Android 现行的模型写入默认上限，`0` 表示不限；PC 原 2200/1375 已作废。
PC 端一部分活是「验证它确实生效」，而不是「实现它」——但 D4、D5、D6–D9 是真的要写代码。

---

## 1. 待办

| 待办 ID | 来源验收项 | 描述 | 状态 | 受阻原因与所需系统环境 | 验证命令 / 证据路径 |
| --- | --- | --- | --- | --- | --- |
| T-01 | §7.2 / 记 03 / G5 | 验证记忆上限在桌面生效：模型写入超 3,000 被拒绝、文件未被截断；`USER.md` 不设上限；超限返回实际字符数与上限；emoji 序列按 1 计数 | 已完成 | — | `scripts/verify_pc_memory_notes.py` 与 `tests/tools/test_memory_contract_alignment.py` → `evidence/t01.md`；2026-11-06 全绿 |
| T-02 | §7.1 / G2 | 验证数据根结构：`memories/MEMORY.md`、`memories/USER.md`、`SOUL.md`、`AGENTS.md` 位置与 PRD 一致 | 已完成 | — | `.venv\Scripts\python.exe` 默认配置与 `get_memory_dir()` 自检 → `evidence/t02.md` |
| T-03 | §7.5 / D4 | 对齐拼装顺序：把 `skills_prompt` 从 volatile 层首位移到 `MEMORY` / `USER` 之后，使 SOUL → AGENTS → MEMORY → USER 连续 | 已完成 | — | `agent/system_prompt.py:793` 调整为 MEMORY / USER → skills；改前后顺序见 `evidence/t03-before.md`、`evidence/t03-after.md` |
| T-04 | 记 04 / 记 05 / §7.1 / §7.4 | 实现 `notes/` 笔记分片与「显示名/名称 — 摘要」清单注入：`notes/<category>/<topic>.md`，**目录名英文小写、固定枚举 8 类（含只读 `docs/`）**（常量单点定义，目录与显示名成对存），清单上限 **64 条**，注入位置在 `USER` 之后 | 已完成 | — | `scripts/run_tests.sh tests/agent/test_notes_prompt.py` + `verify_pc_memory_notes.py` → `evidence/t04.md`；2026-11-03 补 `updated` 排序回归，修复前失败、修复后通过 → `evidence/t04-updated.md`；2026-11-06 补 `docs/` 只读守卫 |
| T-05 | 记 06 / D11 | 超长整理链：接近上限时生成摘要并原地压缩；长期事实由常规记忆工具轮写入 `notes/<category>/<topic>.md`，`MEMORY.md` 只保留近期摘要；会话 ID 与历史保留；失败不覆盖原上下文与已有文件。**不要求压缩同轮抽取**（对齐 Android T-81 实现口径） | 已完成 | — | `scripts/verify_consolidation_chain.py`（21 项）、`tests/e2e/core/compaction/test_consolidation_chain.py`（2 项，假模型端到端）→ `evidence/t05.md`；真实模型与跨端仍见 T-88 / T-10 |
| T-06 | §5.4 版本 | 钉版：版本号与提交号同时固定。方案已定 `0.22.5-fork.<N>`，桌面 `apps/desktop/package.json` = `0.22.5-fork.1`，Python `pyproject.toml` = `0.22.5+fork1`（PEP 440 写法，同义不同串，见 PRD §5.4） | 已完成 | —；本地 annotated tag `v0.22.5-fork.1` 已建（未推送） | `scripts/verify_version_anchor.py --commit v0.22.5-fork.1` → `ok`；`tests/scripts/test_fork_version.py`、`test_version_anchor.py` 26 项 → `evidence/t06.md`。装机属性由 T-12 核对 |
| T-07 | §5.4 跟随节奏 | 建立「跟随上游稳定 Release tag」的升级流程：`git fetch upstream --tags` → `sync/vX.Y.Z` 分支 → 门禁 + 回归 → 进 `main`。**前置：加 `upstream` remote（当前只有 `origin`）** | 完成受阻 | 预检脚本、checklist 与测试已完成；本机无 `upstream` remote，且到 GitHub 网络不可达，无法取得真实稳定 tag 做一次升级记录 | `scripts/check_upstream_upgrade.py --tag vX.Y.Z` 至 `ok`，再按 checklist 执行 → `evidence/t07.md`、`evidence/t07-dryrun.md`；`tests/scripts/test_upstream_upgrade_preflight.py` |
| T-08 | §5.4 改造边界 | 清点所有定制，逐条归入「配置 / 插件 / 客户端 / 受控补丁」四类；补丁部分必须与 PRD §5.4 登记表逐条对得上 | 已完成 | — | 当前路径与插件能力审阅；`evidence/t08.md` |
| T-09a | 远 04 | 设备令牌：一次性配对码换长期 token，可单独撤销。接入点是 `dashboard_auth/token_auth.py` 的 provider 插槽 + `register_token_route`；码语义对齐 `gateway/pairing.py`（8 位、1 小时、限速、锁定、不落日志）。先验证能否插件式接入，不能则按 PRD §5.4 登记补丁 | 已完成 | — | `plugins/dashboard_auth/device_pairing/`（纯插件，无内核改动）；23 passed + 真实服务器验收链 → `evidence/t09a.md` |
| T-09b | 远 01 | 局域网暴露：**必须显式开关**才允许非回环监听（默认 `127.0.0.1` 的现状不许改），开启时打印本机地址与配对入口；确认 Android 原生客户端不受 CORS 限制（`allow_origin_regex` 只限 localhost origin，那是浏览器约束） | 完成受阻 | PC 侧开关、非回环门禁、令牌链与 LAN 横幅已实现并自动化。Android PRD 将远程模式排在 M5，`AGENTS_PROJECT.md` 当前范围明确不做，Android 无实现，无法完成真机可达判据 | 已过：默认仅 `127.0.0.1`、显式开启后无凭据 401/403 → `evidence/t09b.md`；`tests/hermes_cli/test_lan_banner.py`、`test_serve_real_bind_windows.py` |
| T-09c | 远 02 / 03 | 模式标识与自动降级：PC 侧提供可判定的健康状态，手机显示「本机 / 远程」并在连不上时静默回退、保留草稿。UI 在 Android 侧，PC 只负责状态可信 | 完成受阻 | 判定逻辑、3 秒回退计时与草稿保留均在 Android 端；Android M5 未开始，当前无实现可联调。PC 侧原料与契约测试已就绪 | `tests/hermes_cli/test_remote_status_contract.py` → `evidence/t09c.md`；Android 实现后跑断网/杀进程实测 |
| T-10 | §9.3 两端可比 | 建固定记忆样本 + 10 个固定问题，作为「同一份记忆两个答案」的比对基线 | 完成受阻 | Android T-81 / T-88 均完成受阻；样本已按 3000 / USER 不限 / ROOT / `docs/` 重建并可机器比对，仍无跨端实测与模型配置 | `verify_memory_comparison.py --answers <json>`（0 全对 / 1 缺事实或禁止结论 / 3 缺答案）+ `tests/scripts/test_memory_comparison_answers.py` → `evidence/t10.md` |
| T-11 | §8.1 隐 02 / D12 | PC 端默认配置出站链路自查：除配置的模型端点外无其他外联；显式开启检索/工具后的出站另计，不在本项判据内 | 已完成 | —；真实 Electron 采集发现并修复内置主题拉取 `fonts.googleapis.com`（含 Google DNS UDP），修复后 12 秒 NetLog 零主机、Python 探针仅回环。安装包不带后端，未覆盖真实模型/对话流式请求 | `scripts/capture_desktop_network.py --desktop-exe apps/desktop/release/win-unpacked/Hermes.exe --duration 12 --json evidence/t11-desktop.json`（ok=true）；`tests/scripts/test_netlog_egress.py` 13 项 + `test_passive_update_opt_out.py` → `evidence/t11.md` |
| T-12 | §9.3 回归 | 升级不丢记忆：覆盖安装 NSIS 包（per-user）后 `memories/`、`notes/` 保留 | 已完成 | —；本机 `%LOCALAPPDATA%` 实际覆盖安装三次（首次 + 两次覆盖），隔离 `HERMES_HOME` 逐字节保留、真实 `~/.hermes` 未变；应用首跑停在 bootstrap，后端未启动 | `scripts/verify_nsis_overwrite.ps1 -Installer apps/desktop/release/Hermes-0.22.5-fork.1-win-x64-setup.exe` → `report.json` + `evidence/t12.md`；`tests/scripts/test_install_snapshot.py` 5 项 |
| T-13 | §11 R3 / D6–D9 | 上游记忆格式漂移监控：升级时检查记忆上限、计数口径、分隔符、`notes/docs/` 与根提示词是否变化 | 完成受阻 | 本地共享契约基线已建（3000 默认、USER 不限、簇尾归并计数、`\n§\n`、七类 + `docs/` 只读、根提示词），脚本与测试均读同一 fixture；跨版本比较依赖 T-07 的真实上游 tag | `evidence/fixtures/memory-contract.json`、`scripts/memory_contract.py`、`scripts/check_upstream_memory.py --json` + `tests/scripts/test_upstream_memory_check.py` → `evidence/t13.md` |
| T-14 | §11 R7 | 将历史路线对齐当前 Android 原生边界、目录与同步契约、PC fork 策略和已批准的远程实施顺序 | 已完成 | —；旧 HTML 原件保持不变，现行施工使用仓库对齐版 v1.6 | [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md)；原件与交付 SHA-256、相对链接及版本核对、`git diff --check` → [evidence/t14.md](./evidence/t14.md) |
| T-15 | §5.5 / Q6 | 打包改造：Windows 由 MSIX-only 改为 `nsis` + `portable`，Linux/mac 维持 `AppImage` / `dmg`+`zip`。覆盖层 `apps/desktop/electron-builder.fork.cjs` 已建，上游 `electron-builder.config.cjs` 未改 | 已完成 | — | 2026-11-06 在干净提交 `56b7516be4` 重建 `release/*-setup.exe`、`*-portable.exe`（`dirty=false`，NSIS per-user + portable，未签名），离线构建经 `-c.electronDist=<缓存 zip>`；大小、SHA-256 与产品版本 → `evidence/t15.md`。覆盖安装见 T-12 |
| T-16 | §5.5 | 安装脚本、开发初始化与 PM 优先复用兼容本机工具和 Python；缺失或不兼容才获取锁定版本 | 已完成 | — | `scripts/run_tests.sh tests/pm tests/scripts tests/scripts/install -j 2 -q`（316 文件）+ `scripts/check`（14 checks, ok）+ 一次性 `HERMES_HOME` 实机探针四项 → `evidence/t16.md`。2026-11-06 清理：58/65 失败已修或改为带理由的门禁，残余 7 项 + 1 并发偶发逐条记 `evidence/t16-failures.md`；统一入口 `scripts/verify_pc_acceptance.py`（20 任务：15 完成 / 5 受阻，受阻均带交接物）→ `tests/scripts/test_pc_acceptance_manifest.py`。未验证：受管制品的真实下载完成（本机无 egress）、posix 通道实跑、PowerShell 7 lane、密封发行包。2026-11-06 计划最终验证：`scripts/check` 14 checks ok（首轮红过一次——本轮新增的 POSIX 用例顶穿了 `test_config.py` 的行数棘轮，已按门禁处方移进 `tests/hermes_cli/test_config_exec_names.py`）；`tests/pm tests/scripts/install` 整批 164 文件 / 1002 通过 / 10 失败 / 192 跳过，其中 `test_plugin_survival_contract.py` 的 1 项是真缺陷（断言拿未转义路径比 `json.dumps` 输出，Windows 永远不匹配）已修并单用例复跑转绿，`test_pip_check.py` 的 4 项是相对 11-05 sweep 新增的受管 uv 供给问题（单文件复现，非并发），连同其余残余共 11 项记 `evidence/t16-failures.md`；宽口径 4029 文件全量在本机约 3.5 小时、单条命令上限 10 分钟，只跑到约 5%，12 个红全在 `tests/agent/lsp`（`pm` 之外、改动面之外），记为边界不冒充通过 → `evidence/t16.md` §「计划最终验证」 |
| T-17 | D6 / D7 / 记 03 | 记忆契约对齐：`MEMORY.md` 模型写入默认 3000、可配置、`0` 不限；`USER.md` 不设上限；超限整次拒绝并返回实际字符数与上限；码点 + 簇尾归并计数（emoji 序列算 1） | 已完成 | — | `tests/tools/test_memory_contract_alignment.py`、`test_memory_capacity.py`、`test_memory_tool.py` → `evidence/t01.md`；2026-11-06 提交 |
| T-18 | D8 / D9 / 记 04 / §7.5 | 根提示词与 `notes/docs/`：新增 PC 版根约定置于 stable 首位；`docs/` 类别只读、一层放平、计入清单、不参与摘要/合并 | 已完成 | — | `tests/agent/test_system_prompt_root.py`、`tests/hermes_notes/test_docs_category.py`、`tests/tools/test_notes_docs_readonly.py`、`scripts/capture_prompt_order.py` → `evidence/t03.md`、`evidence/t04.md`；2026-11-06 全绿 |
| T-19 | §5.5 | headless 发行变体：不自带运行时，按本机阶梯解析（env → 构建期 `localRuntimeRoot` → 已装 CLI → `%LOCALAPPDATA%\hermes\hermes-agent` → 已发布 launcher），找不到时报可执行错误且永不走 bootstrap；身份与完整版独立可并存 | 已完成 | — | `HERMES_DESKTOP_VARIANT=headless HERMES_DESKTOP_LOCAL_ROOT=<checkout> npm run build` + fork overlay 出 `release-headless/`；双击实测 43 秒后端就绪、连接发布 local；收口后 `npx vitest run --project electron`（8 文件 54 通过 / 2 跳过）+ typecheck + eslint + `scripts/check` 14 checks ok；2026-11-06 16:41 在干净提交 `8b7d5a18af` 重建（`dirty=false`，NSIS + portable，SHA-256 见证据）并实测单一 `local/default` 行 → `evidence/t15-headless.md` |
| T-20 | §5.5 | 完整版（`payload=bundled`，运行时随包） | 完成受阻 | 本机无 GitHub release 资产通路，缺 11 个受管包（`bws chromium cua-driver gh git llamacpp-cpu/cuda/hip/vulkan npm python`，python 为 `cpython-3.14.9+20261021`）；`prepare_tools` 按设计不复用宿主工具（`pm/local_tools.py:154`），`stage_tools` 按锁内 sha256 校验 | 解阻后：`scripts/bundles/native_build.py --prepare-only` → `--prepared` → `HERMES_DESKTOP_VARIANT=bundled`；阻塞证据与两条解阻路径 → `evidence/t15-headless.md` §完整版 |
| T-21 | §5.5 | 桌面机器人板块收敛为单一 default 机器人：只保留主会话，名称由用户设定；任务请求按语义分派到任务列表或定时任务 | 已完成 | — | `npx vitest run --project ui src/plugins/hermes-bots/{single-bot,roster-pane-derivation,bot-row}.test.*`（18 项）+ `HERMES_PYTHON=<venv> scripts/run_tests.sh tests/agent/test_system_prompt_root.py`（5 项）+ `scripts/check --only health` ok → [evidence/t21.md](./evidence/t21.md) |
| T-22 | §5.5 | 品牌改为 KouriChat Next：`assets/logo.svg` 生成全平台图标，可见名称改为 KouriChat Next（变体带后缀），内部 appId/userData/CLI 不变 | 已完成 | — | `npx vitest run --project electron electron/product-identity.test.ts scripts/{write-build-stamp,msix-shared,side-by-side-launchers}.test.mjs`（26 项）+ `node -e "require('./apps/desktop/product-identity.cjs')"` → [evidence/branding.md](./evidence/branding.md)；2026-11-06 产物元数据改 `FunnyAWM` / `Rosmontis` / `funnyawm@qq.com`，PE 字段实测 → [evidence/t15-headless.md](./evidence/t15-headless.md) |
| T-23 | §5.5 | 托盘驻留与网关生命周期：桌面端默认驻留托盘；关闭窗口不停网关；真正退出时停本机 gateway，远程/Cloud 不受影响 | 已完成 | — | `npx vitest run --project electron electron/{gateway-quit,quit-guard,minimize-to-tray}.test.ts`（34 通过）+ `tsc --noEmit` + eslint + `scripts/check` 14 checks ok；实机：关闭窗口后应用与 gateway 存活，真正 Quit 后应用进程归零且 `hermes gateway status` = not running；干净构建 `0dad8e3edb`，安装包哈希 → [evidence/t23-gateway-lifecycle.md](./evidence/t23-gateway-lifecycle.md) |
| T-24 | §5.5 | 交付物：fork 改动导出为 `patches/` 可重放补丁列（v0.22.5 基线上的 49 个补丁）——已被 T-25 的新基线补丁列取代，旧列仍可按 `git show v0.22.5-fork.1:patches/` 取回 | 已完成 | — | 旧基线验证记录见本行历史；新基线验证 → T-25 |
| T-25 | §5.5 | 上游基座迁到 v0.23.0（`2bd378ed1b`）：`origin/main` 硬回退 → `main` 复位打 tag → fork 55 个提交正向重放到 `patches-v0.23.0` → 版本号 re-anchor `0.23.0-fork.1` / `0.23.0+fork1` → 补丁列重导出 56 个 | 已完成 | — | `git am --3way patches/00*.patch` 在 v0.23.0 干净 worktree 全过；`npx tsc --noEmit` 0 错误 + hermes-bots 1059 项（仅上游 `cron-detail` 一条在本机失败，纯 v0.23.0 同样失败）+ electron 45 项 + `scripts/run_tests.sh tests/pm/test_local_tools.py tests/agent/test_system_prompt_root.py tests/agent/test_notes_prompt.py`（26 通过）+ `verify_version_anchor.py --commit v0.23.0-fork.1` ok + `scripts/check` 14 checks ok → [evidence/t25-v0.23.0-port.md](./evidence/t25-v0.23.0-port.md) |

**已完成项的回归提示**：T-03 / T-04 状态保持已完成，但 T-18 会改变提示词首段与笔记类别集合，
批次实施后须重跑 `tests/agent/test_notes_prompt.py`、`tests/agent/test_system_prompt.py` 并刷新证据。

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
   `═══…` + `MEMORY (your personal notes) [27% — 812/3,000 chars]` + `═══…`，条目间用 `\n§\n` 连接（`:25`）。
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
