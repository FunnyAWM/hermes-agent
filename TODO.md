# TODO.md · PC 端（Hermes 桌面 + 内核）

> **范围**：本文件只覆盖 PC 端——Windows / Linux 上的 Hermes 桌面客户端与它依赖的内核。
> **Android 客户端、同步中转不在本文件**，另有工作区。
> **事实源**：`PRD.md` v0.20（2026-11-06）。功能范围与验收判据以 PRD 为准，本文件只把它拆成可执行项。
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
| T-20 | §5.5 | 完整版（`payload=bundled`，运行时随包）——**2026-11-06 使用者裁决不做**：15 个受管包已离线备齐（3.6 GB），但密封 payload 把产物从约 140 MB 推到 3.7 GB 以上，「bundle 太重了」，改为只发 headless 包 + 用户按需补环境（交付口径记入 T-26）。构建能力保留，将来要出仍按证据里的解阻路径走 | 未完成 | 裁决不做，非环境受阻；体量与两条通路（fork 版本号 vs `desktop.py --commit`、镜像上传时间 vs `exclude-newer`）记录 → `evidence/t15-headless.md` §完整版 | `evidence/t15-headless.md` §完整版（bundled）：2026-11-06 裁决不做 |
| T-21 | §5.5 | 桌面机器人板块收敛为单一 default 机器人：只保留主会话，名称由用户设定；任务请求按语义分派到任务列表或定时任务 | 已完成 | — | `npx vitest run --project ui src/plugins/hermes-bots/{single-bot,roster-pane-derivation,bot-row}.test.*`（18 项）+ `HERMES_PYTHON=<venv> scripts/run_tests.sh tests/agent/test_system_prompt_root.py`（5 项）+ `scripts/check --only health` ok → [evidence/t21.md](./evidence/t21.md) |
| T-22 | §5.5 | 品牌改为 KouriChat Next：`assets/logo.svg` 生成全平台图标，可见名称改为 KouriChat Next（变体带后缀），内部 appId/userData/CLI 不变 | 已完成 | — | `npx vitest run --project electron electron/product-identity.test.ts scripts/{write-build-stamp,msix-shared,side-by-side-launchers}.test.mjs`（26 项）+ `node -e "require('./apps/desktop/product-identity.cjs')"` → [evidence/branding.md](./evidence/branding.md)；2026-11-06 产物元数据改 `FunnyAWM` / `Rosmontis` / `funnyawm@qq.com`，PE 字段实测 → [evidence/t15-headless.md](./evidence/t15-headless.md) |
| T-23 | §5.5 | 托盘驻留与网关生命周期：桌面端默认驻留托盘；关闭窗口不停网关；真正退出时停本机 gateway，远程/Cloud 不受影响 | 已完成 | — | `npx vitest run --project electron electron/{gateway-quit,quit-guard,minimize-to-tray}.test.ts`（34 通过）+ `tsc --noEmit` + eslint + `scripts/check` 14 checks ok；实机：关闭窗口后应用与 gateway 存活，真正 Quit 后应用进程归零且 `hermes gateway status` = not running；干净构建 `0dad8e3edb`，安装包哈希 → [evidence/t23-gateway-lifecycle.md](./evidence/t23-gateway-lifecycle.md) |
| T-24 | §5.5 | 交付物：fork 改动导出为 `patches/` 可重放补丁列（v0.22.5 基线上的 49 个补丁）——已被 T-25 的新基线补丁列取代，旧列仍可按 `git show v0.22.5-fork.1:patches/` 取回 | 已完成 | — | 旧基线验证记录见本行历史；新基线验证 → T-25 |
| T-25 | §5.5 | 上游基座迁到 v0.23.0（`2bd378ed1b`）：`origin/main` 硬回退 → `main` 复位打 tag → fork 55 个提交正向重放到 `patches-v0.23.0` → 版本号 re-anchor `0.23.0-fork.1` / `0.23.0+fork1` → 补丁列重导出（当时 57 个：`0001`–`0056` 提交 + `0057` 未跟踪交付物；同日改为按路径过滤口径后重新导出，数量以本目录为准） | 已完成 | — | `git am --3way patches/00*.patch` 在 v0.23.0 干净 worktree 全过（数量以 `ls patches/00*.patch | wc -l` 为准；2026-11-06 改为按路径过滤导出口径后，重放树与分支只差本目录文件与 82 个二进制，`TODO.md` / `PRD.md` / `evidence/` 逐字节一致）；`npx tsc --noEmit` 0 错误 + hermes-bots 1059 项（仅上游 `cron-detail` 一条在本机失败，纯 v0.23.0 同样失败）+ electron 45 项 + `scripts/run_tests.sh tests/pm/test_local_tools.py tests/agent/test_system_prompt_root.py tests/agent/test_notes_prompt.py`（26 通过）+ `verify_version_anchor.py --commit v0.23.0-fork.1` ok + `scripts/check` 14 checks ok → [evidence/t25-v0.23.0-port.md](./evidence/t25-v0.23.0-port.md) |
| T-26 | §5.5 | 无头包交付口径（取代 T-20 的 bundled 计划）：面向使用者的环境与安装说明 `INSTALL.md`（准备项、per-user 安装、venv 与依赖、运行时阶梯与缺失原文、按需 15 个受管工具、镜像上传时间门禁、自检、升级/回退/卸载、故障表）+ headless 包在 v0.23.0 基座重建并实机验证 | 已完成 | — | SHA-256 `090A9307…0F33`（setup 146,509,284 B）/ `C9594E83…F842`（portable 146,313,133 B）；包内 `install-stamp.json` = `payload=headless`、`commit=a0f8f725cd…`、`dirty=false`、`localRuntimeRoot` 已烘焙；实机 `win-unpacked` 启动日志 `Hermes gateway is ready` → `published connection=local` → `bot-roster rows=[local/default=""]`，无 bootstrap（`/api/model/info` 5 秒超时为今天已复现 6 次的冷启动竞态，非本次引入） → [evidence/t15-headless.md](./evidence/t15-headless.md)。`INSTALL.md` 事实来源：`pm/lock.json`（15 包版本）、`pyproject.toml`（`>=3.11,<3.15`）、`electron/headless-runtime.ts:29-31`、`product-identity.cjs`、镜像门禁实测（清华 `/pypi/web/simple` 报 `upload-time`、华为不报） |

| T-27 | §6.4 / PRD §5.4 | KouriChat Link 的 PC 侧落地（同 01–05、同 08）：`sync/` 新增包（BIP39 派生、AES-256-GCM 信封、HLC 定序、覆盖前归档、白名单、mDNS 无地址配对）+ `hermes sync` CLI + REST `/api/kourichat-link/*` + RPC `sync.*` + 桌面 Capabilities → Link 页 | 已完成 | 同 06（交互式冲突仲裁）、同 07（密钥轮换）、同 09（实时协同）仍不做 | `HERMES_PYTHON=<venv> scripts/run_tests.sh tests/sync tests/hermes_cli/test_kourichat_link_router.py tests/hermes_cli/test_sync_sessions_bridge.py tests/tui_gateway/contracts`（10 files, 118 passed）+ `npx tsc --noEmit -p apps/desktop/tsconfig.json` 0 错误 + `npx vitest run src/app/capabilities/link/link-tab.test.tsx src/app/capabilities/index.test.tsx`（15 通过）+ `python scripts/check` 14 checks ok；两个 `HERMES_HOME` 的真实 E2E：不填地址 `sync pair --words` 8s 内发现并配对、双向文件互通 → [evidence/t27-link.md](./evidence/t27-link.md) |

| T-28 | 记 04 / 05、PRD §7.3 / §7.4（v0.23） | 笔记清单软上限 128（默认）+ 可选硬上限（默认不设），取代固定 64 截断；front matter 增 `expires` / `lastUsed` 严格解析（对齐 Android `NoteFrontMatter`）；超软上限时到期笔记先归档 `.versions/` 再删；`notes/docs/` 读取回写 `lastUsed`、连续 N 轮未引用候选经用户确认后删；记忆与笔记工具覆盖前归档 | 已完成 | — | `.venv/Scripts/python.exe scripts/verify_pc_memory_notes.py`；`scripts/run_tests.sh tests/hermes_notes/ tests/agent/test_notes_prompt.py tests/tools/test_note_archive.py`；`python scripts/check` → `evidence/t28.md` |
| T-29 | 记 06（v0.23） | 整理轮次 `memory.organize_turns`（默认 0 关闭）：每 X 轮回答完成后独立整理请求只做 notes 维护（合并同类、清理过期），不写 `MEMORY.md` / `USER.md`；`.memory_incomplete` 标记 + 失败补做（落地时改为全量重计划，见 PRD §5.4）；与 `background_review` 并存；注入只走 user 消息 / 工具结果，不动冻结前缀 | 已完成 | — | `scripts/run_tests.sh tests/agent/test_notes_organizer.py tests/hermes_notes/` + `python scripts/check` → `evidence/t29.md` |
| T-30 | 用户指令 2026-11-12（复刻 deepseek-harness Auto Review）、PRD §5.4 | 新增 `approvals.mode: auto`：每个工具调用执行前用辅助 LLM 判定（low+allow / medium+allow\|deny / high+deny），allow 直接执行、deny 转人工、high 硬拒绝、非法/失败 fail-closed；`approvals.auto_review.{policy,history_messages,max_arg_chars,skip_tools}` 与 `auxiliary.auto_review` 路由；三端审批模式清单同步 | 已完成 | — | `scripts/run_tests.sh tests/tools/test_auto_review.py tests/tools/test_auto_review_executor.py tests/tools/test_approval_mode_auto.py tests/tools/test_approval_mode_parity.py tests/hermes_cli/test_approvals_command.py`（38 通过）+ 审批回归 209 通过 + `python scripts/check` 14 checks ok + `tsc --noEmit -p apps/desktop/tsconfig.json` 0 错误 → [evidence/t30.md](./evidence/t30.md) |
| T-31 | 用户指令 2026-11-13、T-30 收尾、PRD §5.4 | Auto Review 配置落进 Desktop「安全 → 审批」：`SECTIONS(safety)` 暴露 `approvals.auto_review.{policy,history_messages,max_arg_chars,skip_tools}`，`policy` 以 textarea 呈现；8 个语言的 `fieldLabels` / `fieldDescriptions` 补齐。沙箱档位扩到 sandctl 完整策略面：档位可带 `backend`（L1/L2/L3）与 `policy` spec 片段（fs/net/syscall/proc/secrets；列表并集、`read-only` 忽略 fs 覆盖）；custom 卡片提供网络 / Syscall / 隔离等级 / 资源上限 / 密钥控件；输入框模型左侧新增档位下拉；沙箱设置页与 `terminal.sandbox.*` 字段的 8 语言完整翻译（含 `ar` 的 `shell.approvalMode` 缺口） | 已完成 | — | `pytest tests/tools/test_local_command_sandbox.py tests/tools/test_auto_review.py tests/tools/test_auto_review_executor.py tests/tools/test_approval_mode_auto.py tests/tools/test_approval_mode_parity.py tests/hermes_cli/test_terminal_sandbox_config.py`（74 通过）+ `vitest run src/app/settings src/app/chat/composer src/app/chat/index.test.tsx`（179 files / 1327 通过）+ `vitest run src/app/settings/sandbox-profiles-panel.test.tsx`（8 通过，含 zh 渲染）+ `vitest run src/app/chat/use-chat-bar-state.test.tsx`（3 通过）+ `tsc --noEmit` 0 错误 + `python scripts/check` 14 checks ok → [evidence/t31.md](./evidence/t31.md) |
| T-32 | 用户指令 2026-11-13（删除会话仍在列表显示） | 会话删除后的缓存失效：`/resume` 会话选择器（`session-picker.tsx`）与命令面板原先用独立 query key、默认 60s 陈旧期、无墓碑过滤，删除后仍列出并可直接恢复死 id；改为共享 `['sessions', …]` 根键 + 连接作用域、`staleTime: 0`、按 lineage 任一 id 过滤墓碑；删除/归档/恢复成功后统一 `invalidateSessionListQueries()` + `broadcastSessionsChanged()`；设置页「已归档」永久删除改走共享逐出（`dropListedSession` / `$archivedSessions` / 墓碑）并在跨窗口广播时静默重载；导入会话的失效键从无效的 `['sessions']` 对齐到真实根键 | 已完成 | — | `vitest run src/app/session/hooks/use-session-list-actions.test.tsx src/app/session/hooks/use-session-actions.test.tsx src/app/session/hooks/use-session-actions src/app/settings/sessions-settings.test.tsx src/components/session-picker.test.tsx src/lib/query-client.test.ts`（30 files / 439 通过）+ `vitest run src/app/command-palette src/app/session-import src/app/contrib/wiring-background-queue-drain.test.tsx`（3 files / 5 通过）+ `tsc --noEmit` 0 错误 → [evidence/t32.md](./evidence/t32.md) |
| T-33 | 用户指令 2026-11-13（验证双窗口删除后会话列表是否同步）、T-32 未验证项 | 跨窗口删除同步：总线消息从「只刷新」升级为携带 `removed` / `released` id，接收窗口在模块级监听里先镜像墓碑、再跑业务处理器（排在订阅之前，刷新合并不会复活该行）；删除/归档/恢复/设置页广播全部带上 id；`use-desktop-integrations` 改为所有窗口都订阅，只有 `refreshSessions()` 对 secondary / browser 窗口跳过，缓存页失效与墓碑镜像照常 | 已完成 | — | `vitest run src/store/session-sync.test.ts src/app/contrib/hooks/use-desktop-integrations.test.tsx`（53 通过）+ `vitest run src/app/session/hooks/use-session-actions/pinned-session-removal.test.tsx src/app/settings/sessions-settings.test.tsx src/app/session/hooks/use-session-list-actions.test.tsx src/components/session-picker.test.tsx src/lib/query-client.test.ts`（59 通过）+ `vitest run src/app/chat/session-tile-actions.test.ts src/app/session/hooks/use-session-actions.test.tsx src/store/session-removal.test.ts`（171 通过）+ `tsc -p apps/desktop --noEmit` 0 错误 + `python scripts/check` 14 checks ok → [evidence/t33.md](./evidence/t33.md) |
| T-34 | 用户指令 2026-11-13（Auto Review 作为可切换档位） | 输入框档位下拉新增 Auto Review 复选项，与沙箱档位独立并存：勾选写入 `approvals.mode: auto`（经当前 profile 的网关路由），取消恢复切换前模式（按 profile 记忆，缺省 smart）；沙箱 profile 不受影响；9 语言文案与 `_keys.desktop.json` 同步 | 已完成 | — | `vitest run src/app/chat/composer/sandbox-pill.test.tsx src/app/chat/use-chat-bar-state.test.tsx src/i18n/overlay-completeness.test.ts`（12 通过）+ `vitest run src/app/chat/composer`（99/101，唯一失败单跑 7/7） + `vitest run --project electron scripts/i18n-keys.test.mjs`（2 通过） + `tsc -p apps/desktop --noEmit` 0 错误 + `python scripts/check --only health` ok → [evidence/t34.md](./evidence/t34.md) |
| T-35 | 用户指令 2026-11-13（审批对话框要含命令与 justification，理由用用户语言） | 审批链路新增一等字段 `justification`：Auto Review 判 deny / 失败转人工时同时给出待执行调用（`command`）与审查理由；`REVIEW_POLICY` 要求理由用最近一条 USER 消息的语言、直接对用户说话且只描述具体效果；理由经 `redact_approval_display` 脱敏后才进网关 payload、待审批队列与 pending 重放；桌面审批卡在命令块之后新增理由块（`data-slot="tool-approval-justification"`），9 语言文案与 `_keys.desktop.json` 同步 | 已完成 | — | `pytest -q tests/tools/test_auto_review.py tests/tools/test_auto_review_executor.py tests/tools/test_request_tool_approval.py tests/tools/test_approval.py tests/tools/test_approval_mode_parity.py`（221 通过 / 2 跳过）+ `vitest run --project ui src/components/assistant-ui/tool/approval.test.tsx src/i18n/overlay-completeness.test.ts`（20 通过）+ `vitest run --project electron scripts/i18n-keys.test.mjs`（2 通过）+ `tsc -p apps/desktop --noEmit` 0 错误 + `python scripts/check --only health` ok → [evidence/t35.md](./evidence/t35.md) |
| T-36 | 用户指令 2026-11-13（从 patch/v0.23.0 合并到 patch/v0.23.2 并适配新版本） | 上游 v0.23.2 基座合入 fork 88 个提交：12 个冲突逐条裁决（approval 双参数、native 目录项 + filename_match_key、deps-first + find_local、start_server 包装器、AGENTS 结构）；新版本不兼容项修正：npm→pnpm（build.ps1 / 离线工具脚本 / INSTALL / builder 注释）、版本号 0.23.2+fork1 与 0.23.2-fork.1、tool_executor 越过 2 000 行棘轮（Auto Review 闸门移入 sibling）、AGENTS 链路压回 30k、prompt_surface 渲染补 USERPROFILE | 已完成 | — | `python scripts/check`（17 checks ok，health 0 blocking）+ `tsc -p apps/desktop --noEmit` 0 错误 + 审批/沙箱 297 通过 + 同步/记忆/笔记 157 通过 + desktop 7 files / 44 tests + file_operations 23 个失败经基座 worktree 对照确认为 Windows 环境既有 → [evidence/t36.md](./evidence/t36.md) |

| T-37 | 聊 09（PRD v0.24）、PRD §5.4 | 会话滑动窗口与冻结窗口（移植 Android 设 08）：`context.session_window_turns` 只把最近 X 轮用户消息发给模型，`context.frozen_window_turns` 在满 X 轮时把最近 Y 轮冻成请求前缀、之后只追加直至再次满窗（Y 非法时只保留滑动窗口）；裁剪只作用于 `build_api_messages` 的发送副本，落盘会话、界面历史与压缩摘要不变，上下文估算按裁剪后内容计；两个键是 schema 虚拟字段（默认 0 关闭）并落在 Desktop「对话 → 机器人」，de/es/fr/ja/ru/zh-hant 文案同步（selection 深链补按字段名路由，否则 `setting=context.session_window_turns` 落回首个子页） | 已完成 | — | `scripts/run_tests.sh tests/agent/test_session_window.py`（12 项，含冻结窗口 wire 级用例）+ `npx vitest run src/app/settings src/i18n/overlay-completeness.test.ts` + `npx tsc --noEmit -p apps/desktop/tsconfig.json` + `python scripts/check`（16 checks ok；health 单独跑 0 blocking，基线漂移见 evidence） → [evidence/t37.md](./evidence/t37.md) |
| T-38 | 用户指令 2026-11-16（PRD v0.26）、PRD §5.4 | 轻量密封完整版：运行时（解释器 + venv + 源码快照，约 450 MB 装后）随包，首启离线可用；复用 `bundled` 变体全链（Electron 零改动），新增 `HERMES_BUNDLE_TOOLS=python` 工具子集过滤与密封子集跳过 uv-cache；`pm repair / gc / update / lock` 在密封布局下拒绝执行（只读，升级换包）；命名工具 `pm install` / `--tools-only` 与懒安装写可写 store，是缺工具时的拉取途径（裸 install / `--extra` / `--test-environment` 仍拒绝）；构建入口 `scripts/build-full.ps1`，产物 `apps/desktop/release-full/`（仅 NSIS）；其余工具运行时回退本机 PATH，缺了只是少能力 | 已完成 | — | `scripts/run_tests.sh tests/pm/test_sealed_cli.py tests/scripts/test_bundle_native.py`（26 项，4 跳）+ `python scripts/check` + `scripts/build-full.ps1` 实机构建与首启 → [evidence/t38.md](./evidence/t38.md) |
| T-39 | 用户指令 2026-11-17（PRD v0.28）、PRD §5.4 | pm 下载包走镜像站：新增 `HERMES_PM_DOWNLOAD_MIRROR` 前缀重写（ghproxy 式），拉取受管工具时镜像优先、原站与 hermes-assets 公共归档兜底，sha256 全程校验；未设环境变量时行为不变。npm/PyPI 沿用既有 knob（`npm_config_registry` / pip.conf `index-url`） | 已完成 | — | `scripts/run_tests.sh tests/pm/test_artifact_mirror.py`（13 过 0 败 2 跳）+ `python scripts/check` → 本行验证命令 |
| T-40 | 用户指令 2026-11-17（整理时机与手机一致）、PRD §5.4、记 06 | 整理时机对齐 Android `ChatViewModel`：整理请求从 turn prologue（回答开始前）移到 `turn_finalizer` 的 `_mirror_turn_to_memory` 之后，gate `final_response and not interrupted and not vetoed`（错误/中止/拦截轮丢弃名额，对齐 `finishStream`）；新增 `notes/.organize-incomplete` 补做标记——pass 前写、成功清除、失败/被杀保留，恢复路径让任意下一轮补做（仍需 `organize_turns>0`）；`_notes_organizer_in_flight` 防重入（恢复路径可能每轮都到期），`thread.start()` 失败回滚。窗口部分（设 08）经审计确认与 Android 逐轮一致，无需改动 | 已完成 | — | `scripts/run_tests.sh tests/agent/test_notes_organizer.py tests/agent/test_session_window.py`（19 过 0 败：organizer 7 = 原 4 + 新 3）+ 等价 ruff 全过 → [evidence/t40.md](./evidence/t40.md) |
| T-41 | 用户指令 2026-11-17（安装时长异常）、PRD §5.4、T-38 | 完整版安装时长异常：实测安装包 583 MB / 展开 2.2 GB / 7.6 万文件，超 T-38 设计（~200 MB / ~450 MB）3 倍；根因是密封子集 venv 仍用 `--all-extras`（49 个可选集成，`lark_oapi` 113M / `playwright` 109M / `googleapiclient` 109M / `dingtalk` 93M / 语音栈等），安装时逐文件解压 + Defender 扫描。改为 `--extra all`（政策规定的 9 个常驻 extras），`.pyc` 保留（`bake_bytecode` 覆盖率断言是防冷编译停顿的性能契约，不能删） | 已完成 | — | `scripts/run_tests.sh tests/scripts/test_bundle_native.py`（16 过 0 败 4 跳）+ `uv sync --frozen --dry-run --extra all` 解析通过 → [evidence/t41.md](./evidence/t41.md) |
| T-43 | 用户指令 2026-11-18（安装仍慢，重点查 asar） | 第二轮打包优化：① `asar.unpack` 收窄 `dist/**` → `dist/native/**`（原来 1,126 个字体/图片/CSS 全部解压到磁盘，只为 9.7 KB 的原生 helper）；② 密封包 extras 从 `[all]` 去掉 `google`（109 MB / 数千个 discovery 模块；google-workspace skill 在密封包内不可用）。压缩方式保持默认 `normal`（`store` 曾被验证但用户否决：安装包 330 MB → 约 1 GB）。剩余约 2 万文件是 `.py`/`.pyc`，受 `bake_bytecode` 断言与 Python 导入约束，已到下限 | 已完成 | — | `scripts/run_tests.sh tests/scripts/test_bundle_native.py`（16 过 0 败 4 跳）+ `vitest run scripts/after-pack.test.mjs`（3 过）+ `uv sync --frozen --dry-run --extra acp…youtube` 不含 google → [evidence/t41.md](./evidence/t41.md) |
| T-44 | 用户指令 2026-11-18（打包时完全忽略 .pyc） | 密封包不再携带任何 `.pyc`：`build_environment(compile_bytecode=False)`（不加 `--compile-bytecode`）+ 跳过 `bake_bytecode` + `_drop_bytecode` 清掉受管解释器的 stdlib pyc。payload 文件数 27,487 → 约 18,800（8,724 个 pyc，占 32%）。代价：首启一次性冷编译（只编译被导入的模块），之后写/读 `%LOCALAPPDATA%\hermes\pycache`——`launcher_wrapper.py` 对无 `.hermes-baked-pycache` 标记的 payload 早已支持该重定向。全量 bundled 变体行为不变 | 已完成 | — | `scripts/run_tests.sh tests/scripts/test_bundle_native.py tests/pm/test_workspace_build_inputs.py`（32 过 4 跳）+ `tests/pm/test_environment_command.py tests/pm/test_sync_bytecode_compile_fallback.py tests/pm/test_environment_build.py`（43 过 1 跳）+ 等价 ruff → [evidence/t44.md](./evidence/t44.md) |
| T-45 | 用户指令 2026-11-18（继续压缩文件数量） | 快照排除 `contributors/`（2,894 个每贡献者一文件的 `emails/<addr>`，只被 `scripts/add_contributor.py` / `audit_pr_attribution.py` 读）与 `evidence/`（66 个实施记录），两者运行时无消费者。payload 文件数再降 2,960。剩余大头：`plugin-catalog/` 2,146（需改 `load_catalog` 等 4 处读取才能折叠）、`dist-webapp` 1,086（985 个 js chunk，需改前端分块），代码与第三方依赖已无安全可去项 | 已完成 | — | `scripts/run_tests.sh tests/scripts/test_bundle_native.py`（17 过 4 跳）+ 等价 ruff → [evidence/t44.md](./evidence/t44.md) |
| T-42 | 用户指令 2026-11-17（Auto Review 不生效 + 沙箱档位） | ① Auto Review 每次 400 失败转人工：custom 端点的 `reasoning_effort: unknown variant \`none\`` 不在 `_REASONING_LEVEL_REJECTION_MARKERS` 里，分类失败导致 auxiliary ladder 不进入 floor step-up；补 `unknown variant` / `expected one of`。② 沙箱提权机制本已存在（`terminal(sandbox=…)` + 人工审批），但 `failure_note` 只指 config.yaml，模型改用更严的 `read-only`；补一句「对本条命令传 `sandbox="full-access"` 申请，用户审批后下一条恢复」；③ `approvals.mode: auto` 下提权不再二次人工——auto review 已在 executor 层带上完整参数与对话判定（`skip_tools` 含 terminal 时无审查，仍走人工），`REVIEW_POLICY` 增条款使 `sandbox` 请求按命令在该档位的实际副作用定级而非一律 medium | 已完成 | — | `scripts/run_tests.sh tests/tools/test_sandbox_escalation.py tests/agent/test_error_classifier_reasoning_level.py`（17 过 0 败，含 auto 模式 2 例）+ Desktop 实机复测 → [evidence/t42.md](./evidence/t42.md) |

**已完成项的回归提示**：T-03 / T-04 状态保持已完成，但 T-18 会改变提示词首段与笔记类别集合，
批次实施后须重跑 `tests/agent/test_notes_prompt.py`、`tests/agent/test_system_prompt.py` 并刷新证据。

---

## 2. 明确不做（避免被 PRD 的功能表误导）

以下条目在 PRD 里是 Android 的开发范围，PC 端不承接：记 08–09（留档 / 导出）、
聊 01–07（流式、中断、会话、错误态、草稿、模型切换）、文 01–09（文件选择器、分享意图、文本抽取、OCR）、
同 06 / 07 / 09（交互式冲突仲裁、密钥轮换、实时协同）、设 01–06（首启向导、模型配置、遥测开关）。
**记 07（记忆页）两边各有实现**：Android 为原生页；PC 已由桌面记忆页承接（PRD §5.4 v0.22 登记，
`hermes_cli/web_routers/memory_docs.py` + `apps/desktop/src/app/memory/`），不是「PC 不承接」。

**同 01–05、同 08 已改为 PC 端承接**（2026-11-09，T-27）：设备链的配对、白名单、加密封装、不透明
命名、客户端定序与状态可见都已实现，PC 可以当主设备也可以当从设备。仍不做的是 同 06 的交互式
冲突仲裁（覆盖前只归档到 `.versions/`，不做「选一边 / 都留 / 标冲突」）、同 07 的密钥轮换
（`generation` 固定为 1）、同 09 的实时协同。

PC 端对剩余条目的责任只有一条：**不要让自己的实现破坏「两端共享目录」这一前提**（PRD §5.3）。

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
