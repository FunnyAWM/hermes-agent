# Super Agent 与 Desktop 剩余任务

交接日期为 2026-11-27。工作目录为 `D:\VSCodeProjects\hermes-agent`，分支为 `patch/v0.23.4`，当前 HEAD 为 `8fce5f9546`。实现保存在工作树中，尚未提交。**2026-11-27 本轮：A、B、C 已完成；D 核心链路已通过实机验收，`tell` / `stop` 手工冒烟待补。**

## A．修复仅写内置机器人时的建票提示

**接手人**为投递子 Agent。上次在派发这一项后中断，当前工作树仍保留旧判断。这个问题会影响从只读 IM 建票的用户：票已入队，通知实际无处送达，但建票提示没有给出设置指引。

- [x] **复现。** 使用临时 profile，设置 `message_sources: [weixin]`、`write_message_sources: [local]`，在插件条目根部授予 `allow_gateway_injection: true`。创建本档的本地会话 authority 和一个普通 Desktop 会话，先保持本档没有 canonical Bot Chat。让一条 `sa:` 消息从 Weixin 入口建票，记录返回的提示和票号。
- [x] **确认分歧发生的位置。** 对照 `plugins/super_agent/__init__.py::_has_destination`、`hermes_cli/plugin_messages.py::home_platforms` 和同文件的 `post_home`。当前发现逻辑仅凭本档存在 authority 就列出 `local`，实际发送还要按 `BOT_CHAT_TITLE` 找到 canonical Bot Chat。现有 `test_setup_hint_uses_the_profile_grant_and_configured_home` 覆盖了 Telegram Home 和缺少授权的情况，没有覆盖这两个本地状态的差异。
- [x] **写能复现的行为测试并修复。** 复用 `tests/plugins/super_agent/test_bot_chat.py`、`test_delivery.py` 中的临时 authority 和 Bot Chat 夹具。先验证缺少 Bot Chat 时建票仍成功，返回的同一条临时系统提示含票号、查看逐轮结果的方法和设置指引。让实际发现与投递使用一致的 profile、标题和会话判定，然后再跑同一测试。
- [x] **验证创建前后和跨档行为。** 只在另一个 profile 创建 Bot Chat，当前 profile 的下一张票仍应提示没有可写目的地。随后在当前 profile 创建 canonical Bot Chat，再用不同消息 ID 建票，确认提示消失且结果送达本档 Bot Chat。普通 Desktop 会话本身是获准的读写来源时，回报仍可回原会话，这条路径独立于 canonical Bot Chat。
- [x] **保护设置界面的约定。** 无论用户是否已经打开过 Bot Chat，“读取消息来源”和“写入消息来源”的多选下拉都始终提供“内置机器人”。下拉展示可配置的渠道，建票提示判断当下这张票能送往哪里。只改实际产生错误的目的地判断，检查外部 Home 和后台注入授权仍生效。

**完成凭据**为一条在旧判断下失败的回归、修复后的定向测试结果，以及无目的地和有目的地时两种建票提示的行为断言。

**本轮结果（2026-11-27）**：`hermes_cli/plugin_messages.py` 新增 `_local_bot_chat`，`home_platforms` 与 `post_home` 共用同一 profile 的 authority + canonical Bot Chat 判定；`_has_destination` 的 `bot_chat` 分支改为检查该 profile 是否存在 canonical Bot Chat。新增 `tests/plugins/super_agent/test_destination.py`（4 项）：无 Bot Chat 时提示含票号、逐轮结果与设置指引；当前档创建 Bot Chat 后提示消失；仅另一档创建仍提示；普通 Desktop 会话回报路径不变。定向回归 5 个文件 45 项通过；`tests/plugins/super_agent/` 整目录 10 个文件 105 项通过。

## B．核对历史清理与重放的交界，复现后直接修

**接手人**为 Worker／存储子 Agent，可与 A 并行。这是上次收尾时留下的边界核对。现有清理测试已经通过，下列场景尚无独立结论。

- [x] **先建立对照。** 阅读 `plugins/super_agent/history.py::prune`、`store.py::chat_receipt`、`forwarded_count`、`cancel`，以及 `tests/plugins/super_agent/test_history.py`。用两个临时 profile 和可控时间创建已结案、仍在运行及有待投递记录的票。只有显式设置正整数保留天数后才允许清理。保留天数为 0 时，同一批数据应原样保留。
- [x] **重放已过期票的追加消息。** 从真实聊天路由向一张票发送带稳定消息 ID 的自然语言追加要求，确认入箱一次。使该票结案、完成投递并过期，清理后再送入同一来源、同一消息 ID。测试时让分类器在再次被调用时返回 `NEW`，据此观察是否会新增 Ticket 或 Effect。当前清理会删除 `chat_forwards`，已有 `retired_tickets` 主要覆盖建票事件。若可复现误建票，保留拦截重放所需的最小身份，保持 profile 与会话隔离，勿为了去重保留消息正文。
- [x] **核对撤回后等待用户回答的原请求。** 撤回一张票，保留“是否交还普通助手”的待答提议，让票据达到保留期限。分别检查票据、`chat_offers` 和最小去重记录中是否仍有原任务全文。再让同一发送者在同一会话产生一个较新的提议，验证清理旧票不会误删新提议。如果要在“过期删除原文”和“数天后仍能回答旧提议”之间作产品选择，带上实际复现结果询问用户。
- [x] **核对外部已签收、Hermes 尚未写入历史的回报。** 用真实 turn lease 使 IM 回报暂时无法入史，确认 outbox 持有 `history_checkpoint`。推进清理时间，检查这张票及待写全文仍可恢复。释放租约后重试，应只补写一条完整 assistant 消息，适配器的发送次数保持一次。可复用 `tests/gateway/test_plugin_post_message.py::test_confirmed_external_report_replays_once_in_display_and_next_turn` 的投递夹具。
- [x] **按结果收口。** 对每个场景记录复现步骤、实际状态、预期状态。已有实现满足约定即可勾选；发现缺口时从对应用例修到通过。再次跑已有的旧版 spill、用户修改过的文件、待停止对账和其他 profile 保留用例。操作测试库时保留用户 Git 工作树及其中的修改。

**本轮结果（2026-11-27）**：`plugins/super_agent/history.py` 在删除 `chat_forwards` 前把 `[conversation, raw_id]` 解析成最小身份写入 `retired_tickets`（`INSERT OR IGNORE`，不保留正文）；过期票的追加消息重放由 `chat_receipt` 返回 `retired`，不再新增 Ticket/Effect。`tests/plugins/super_agent/test_history.py` 新增 3 个场景（过期重放、撤回提议、outbox `history_checkpoint`），连同原有用例共 5 项。撤回提议的原任务全文仍保留在 `chat_offers`，这是“数天后仍能回答旧提议”的既有取舍，未改动。定向回归 4 个文件 37 项通过。

## C．合并两个子 Agent 的成果并验证最终工作树

**接手人**为主 Agent。两个子 Agent 共享同一工作树。A 负责目的地发现与提示，B 负责存储、清理与重放，分别提交实际修改和测试结果给主 Agent。

1. 执行 `git status --short --branch`、`git diff --cached --stat`、`git diff --stat`。原有暂存改动与本轮未暂存改动共存，尤其是 `plugins/super_agent/__init__.py`、`runtime.py`、`store.py`。保留用户原有的 `PLAN.md`、`REVIEW-super-agent.md` 和 `owned/`。若两个子 Agent 都需修改同一文件，按函数分工并逐段核对 diff。
2. 检查未跟踪的新文件是否都在交付范围内。重点包括 `gateway/plugin_reports.py`、插件的 `delivery.py`、`history.py`、`worker_input.py`、`worker_turn.py`、Desktop 的 `plugin-message-sources-select.tsx` 及对应回归文件。仅看 `git diff --stat` 会漏掉这些文件。
3. 从仓库根目录运行下面的 Python 定向回归。这组路径同时覆盖插件、真实 Gateway／Session 入史、存储接口契约、渠道配置档隔离和 TUI 入口。任何失败先保留文件名、测试名和首个有效异常，再在同一入口缩小到失败文件重跑。

   ```powershell
   $env:HERMES_PYTHON = 'D:/VSCodeProjects/hermes-agent/.venv/Scripts/python.exe'
   $testPaths = @(
     'tests/plugins/super_agent/',
     'tests/gateway/test_post_gateway_admission.py',
     'tests/gateway/test_plugin_post_message.py',
     'tests/gateway/test_managed_worker_session_boundaries.py',
     'tests/gateway/test_cli_plugin_command_in_gateway.py',
     'tests/contracts/conversation/test_message_verbs_contract.py',
     'tests/hermes_cli/test_web_server_messaging_profiles.py',
     'tests/hermes_cli/test_plugin_validate_private_imports.py',
     'tests/tui_gateway/test_plugin_command_profile_scope.py'
   )
   & 'C:/Program Files/Git/bin/bash.exe' scripts/run_tests.sh -j 4 @testPaths
   ```

4. Windows 沙箱曾在 Git Bash 创建信号管道时报 `Win32 error 5`。用户已授权在此情况下使用标准脚本所调用的 `scripts/run_tests_parallel.py`。沿用独立测试解释器、逐文件子进程、无凭据环境和临时 profile，传入同一组测试路径。上次把待测源码复制到 `.pytest-cache/sa-src`，测试临时目录放在与源码副本并列的 `.pytest-cache/sa-verification`。重用此方式时先同步最新源码和未跟踪文件；临时测试 home 应在待测源码树外。为 Git 夹具设置临时目录的 `GIT_CEILING_DIRECTORIES`，避免空目录被识别为外层项目仓库。
5. 如 A 或 B 改动触及 Desktop、渠道配置接口或组件契约，在 `apps/desktop` 运行以下命令。在“读取／写入消息来源”中切换两个配置档，确认展示当前档已配置的 IM，包括暂时离线或没有 Home 的渠道，以及始终出现的“内置机器人”。旧的已选渠道可移除；“全部来源”“沿用读取来源”和空列表按各自语义保存。

   ```powershell
   $uiTests = @(
     'src/app/capabilities/index.test.tsx',
     'src/app/capabilities/link/link-tab.test.tsx',
     'src/app/settings/plugin-message-sources-select.test.tsx',
     'src/app/settings/plugin-catalog-select.test.tsx',
     'src/app/settings/plugin-settings-ownership.test.tsx',
     'src/app/settings/plugin-settings-form.test.tsx',
     'src/app/settings/plugin-settings.test.tsx',
     'src/app/messaging/index.test.tsx',
     'src/contrib/settings-pages.test.ts'
   )
   & ./node_modules/.bin/vitest.cmd run --project ui @uiTests
   & ./node_modules/.bin/tsc.cmd -p . --noEmit
   ```

6. 回到仓库根目录执行最终门禁。上次源码副本的整套检查中有 21 项通过，`health` 因 Git diff 超过 120 秒退出；在实际工作树单跑 `health` 得到 0 blocking、0 advisory。原工作区的 `apps/desktop/build` 等打包副本也曾被后端导入检查扫到。若再遇到，记录失败路径，并在包含最新改动的干净源码副本复核。交付记录写明整套与分项各自的结果。

   ```powershell
   & .venv/Scripts/python.exe -X utf8 scripts/check --base HEAD
   & .venv/Scripts/python.exe -X utf8 scripts/boundary_lint.py
   & .venv/Scripts/python.exe -X utf8 scripts/gen_plugin_api_docs.py --check
   git diff --check
   git diff --cached --check
   ```

7. 留存最终日志和失败复现，再核对本轮产生的临时目录。`.pytest-cache/sa-src` 是源码副本，`.pytest-cache/sa-verification` 含日志与临时夹具。清理前确认进程已退出、绝对路径与实际归属；此前两个 `code-health-*` 临时目录遇到 Windows ACL 拒绝，可单独记录。交付时列出业务代码修改、测试通过数、平台跳过项和实机验收状态。

**本轮结果（2026-11-27）**：Python 定向回归 18 个文件 224 项通过、0 失败、6 项 Linux 专用跳过（149.1s，`-j 4`）；Desktop 9 个文件 68 项通过，`tsc -p . --noEmit` 无错误；`boundary_lint.py`、`gen_plugin_api_docs.py --check`、`git diff --check`、`git diff --cached --check` 均通过。`scripts/check --base HEAD` 22 项检查中仅 `backend-imports` 失败，命中全在本地打包副本与旧验证副本（`apps/desktop/release-full/win-unpacked*`、`apps/desktop/build/agent-payload`、`.pytest-cache/sa-src`），无源码路径；在只含最新 `.py` 源码的干净副本上单跑该检查通过。`health` 0 blocking、0 advisory。本轮临时副本已清理；`sa-src` 与 `sa-verification` 为上一轮证据，保留。

## D．在专用环境验收真实 IM、沙箱和远端停止

这些项目依赖专用账号、会话或真实进程环境。当前记录只覆盖本地网关、适配器夹具和离线界面测试。

**状态：核心链路已通过实机验收，`tell` 手工冒烟已通过；`stop` 核心通过，cancelled 票的 turn 行仍为 running（stop_confirmed=1），作为后续状态机收口项。隔离 Gateway 已于 2026-11-27 停止，home 保留在 `E:\userdata\temp\hermes-super-agent`。** HERMES_HOME 为 `E:\userdata\temp\hermes-super-agent`，临时 Git 工作区为 `E:\userdata\temp\hermes-super-agent-workspace`，运行时目录为 `E:\userdata\temp\hermes-super-agent-runtime`；配置与操作步骤见该 home 的 `E2E_README.md`。

**实机结果（2026-11-27）**：
- 新票 `sa-e1eb3e37e3a6`（nonce 2032）：建票回执、Worker Session `local-c204...`、逐轮汇报、微信回信均通过；`README.md` 第一行 `# E2E workspace`，Ticket `succeeded`。
- 旧票 `sa-6a5ba0eddbe0`：重试生成 generation 3，Session 标题带 `g3`，结果 `succeeded` 并回信一次；retry 路径闭环。
- 实机暴露并修复：
  1. `worker_turn.py` 在 Gateway 报告 `unknown` 后未确认停止，retry 被旧 Worker 守卫永久挡住；现在同步 `confirm_stopped`。
  2. `runtime.py` 的 Worker Session 标题未区分 generation，retry 复用旧标题的 Session，`bind_worker_session` 撞 UNIQUE；标题改为 `Super Agent <ticket> g<generation>`。
  3. `tools/environments/local.py::_wrap_command` 不接受 `cwd_fallback`，Worker 终端工具全部失败；签名与基类对齐。
  4. `runtime.py::_consume_progress` 把 Gateway 断开时队列里的 `GatewayClientError` 当 dict 读取，覆盖原始失败并带停网关；现在非 dict 错误按流结束处理。
  5. worktree 的 common `.git` 不在 sandbox read grants 内，且宿主 `.gitconfig` 不可读；`build_policy` 现在解析 worktree `.git` 文件并加入 common git dir 的只读授权，沙箱 PowerShell 设置 `GIT_CONFIG_NOSYSTEM=1` / `GIT_CONFIG_GLOBAL=NUL`。
- 新增/更新测试：`test_worker_recovery.py::test_unknown_receipt_confirms_the_lost_worker_so_retry_can_claim`、`test_runtime.py` 标题断言与 `test_progress_stream_stops_on_a_gateway_disconnect`、`tests/tools/environments/test_local_wrap_command.py`、`tests/tools/environments/test_sandbox_worktree.py`。
- git 边界直接验证：`git rev-parse --is-inside-work-tree=true`、`git status --short` 返回 ` M README.md`、`git log -1 --oneline` 返回 `e61d307`。git Worker 票 `sa-5f...` 的第二次尝试在 300 秒模型超时，非 git 权限错误；`tell` / `stop` 手工冒烟仍未执行。
- tell 手工冒烟：`sa-7489bf634a22` 第一轮 45 秒睡眠 + README 报告，`tell` 追加在第二轮（nonce tell7）；两轮 terminal，outbox 12/13 均 delivered，`tell` 链路通过。
- stop 手工冒烟：`sa-2707f2b10f6b` 转 cancelled，assignment cancelled，cancel control event 1 条，outbox 14 delivered 一条；Worker 会话停止、stop_confirmed=1。遗留：turn 行仍为 running（stop_confirmed=1），现有测试未断言，列为后续状态机收口项。
- Linux 专用项见仓库根 `LINUX_TODO.md`。

1. 确认可用的专用测试 profile、机器人账号、接收会话和允许发送的范围，并检查 `sandctl`。准备干净的临时 Git 仓库。缺少指定 IM 会话或发送授权时，先记录所缺环境，待用户提供后再发消息。
2. 按 `TODO_TEST.md` 的人工冒烟步骤，从配置好的测试机器人和专用微信／OpenClaw 会话各提交一条带不同标识的无副作用任务。记录 profile、票号、Worker Session、目的地、发送时间和远端回执。
3. 追加一轮任务并检查逐轮汇报。核对最后一轮兼作结票汇报，Hermes 聊天历史和下一轮模型上下文各只出现一次逻辑全文。支持撤掉系统提示的渠道按现有时限撤掉建票提示；其他渠道保留简短提示。平台接受了消息但回执丢失时，外部 IM 可能重复，这是已接受的投递取舍。
4. 若有真实沙箱环境，分别验证 `read-only`、`workspace-write`、`workspace-exec` 的任务边界。用可控长任务触发已设置的回合硬时限，再观察远端取消回执、并发槽和下一代任务领取顺序。预期旧 Worker 确认停止后才释放相应槽位；默认硬时限 0 表示关闭。
5. Linux 环境可用时，通过 `scripts/run_tests.sh tests/gateway/test_cli_plugin_command_in_gateway.py` 补跑真实 CLI daemon 用例。记录操作系统、通过数及仍依赖外部条件的项目。

## 已验证的基线

| 范围 | 最近一次可核对的结果 |
| --- | --- |
| Python 综合定向回归（2026-11-27 最终） | 21 个文件，228 项通过，0 失败，8 项 Linux/posix 专用用例在 Windows 跳过；223.0s，`-j 4`。含 A 的 `test_destination.py`、B 的 3 个历史场景、D 的 Worker 停止确认、进度流断开、`_wrap_command` 与 worktree git 授权回归。 |
| 投递、租约、存储契约 | 后续 4 个文件复验，81 项通过。旧 Discord `guild_id` 兼容调整后，网关投递文件再次运行，12 项通过。见同目录的 `delivery-final.log`、`legacy-scope.log`。这些是重跑记录，计数有重叠。 |
| Desktop（2026-11-27） | 9 个文件，68 项通过。渠道下拉、配置档切换、旧值保存、同步页及设置路由已有覆盖；`tsc -p . --noEmit` 通过。 |
| 最终门禁（2026-11-27） | `scripts/check` 22 项；`footguns` 的 BOM 读取已修复并单项复跑通过；`no-tmp` / `backend-imports` 的剩余命中来自 `.codebuddy` 嵌套工作树与本地打包副本，非源码；干净源码副本复核这三项均通过。`health` 52 files，0 blocking / 0 advisory；边界、插件文档、diff 检查通过。 |
| 实机验收（2026-11-27） | 隔离 home + 真实 Weixin/OpenClaw（`4fb8fd6a`）+ `deepseek-v4.1-flash`：建票、Worker Session、逐轮汇报、微信回信、retry generation 3 全部通过。`tell` / `stop` 手工冒烟未执行；worktree 内 `git` 被沙箱拒绝，已记录。 |
| 同步页水平边距 | 离线 Chromium 在 400、640、1500 px 视口测得两侧各为 20、32、160 px，与相邻工具列表对齐。覆盖加载、未配对、已配对主设备和从设备。 |
| 已落地的 Super Agent 主流程 | 普通咨询继续聊天、明确执行请求直接建票、撤回本人任务后询问是否交还普通助手；逐轮投递与本地去重、旧通知手动重投、Worker 长输入、硬时限及停止对账、可选历史保留均已实现。历史保留默认关闭。升级前的 `failed`／`unknown` 留待人工核对，新通知和原有 `pending` 按投递策略自动处理。 |
