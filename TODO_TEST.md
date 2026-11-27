# Super Agent 测试计划

> 目标：验证可启停的 Super Agent 插件在真实 Hermes 插件、Gateway、Session、终端沙箱和消息回信边界下可恢复、可隔离、可审计。测试必须通过 `scripts/run_tests.sh` 运行；禁止读取源码文本、断言注册数量或编写仅检测实现形状的测试。

## 1. 当前覆盖与测试布局

已有覆盖应保留并按行为扩展：

- `tests/gateway/test_managed_worker_session_boundaries.py`
  - 已覆盖 managed-session Ticket 无 operator 权限。
  - 已覆盖 Worker Session 对用户可见且只读、只能被创建它的 Router 修改。
  - 已覆盖 Router 可读取用户 Session，但不可 submit/control/mutate；伪造 `source` 被拒绝。
- `tests/gateway/test_plugin_post_message.py`
  - 已覆盖后台任务使用持久化的 `source` 快照按原机器人渠道回信，并重新经过 Gateway 授权。

新增测试按职责放置：

| 建议文件 | 职责 |
| --- | --- |
| `tests/plugins/super_agent/test_store.py` | SQLite 状态机、幂等、恢复、收件箱/发件箱 |
| `tests/plugins/super_agent/test_runtime.py` | 两槽调度、Gateway RPC、turn/follow-up、取消与失败 |
| `tests/plugins/super_agent/test_worktrees.py` | Git worktree 生命周期与失败恢复 |
| `tests/plugins/super_agent/test_plugin.py` | 真实插件发现、启停、hook、slash command、`sa:` admission |
| `tests/plugins/super_agent/test_sandbox.py` | Router 指定等级与 Worker 强制执行 |
| `tests/plugins/super_agent/test_e2e.py` | 临时 `HERMES_HOME` 下真实导入的最小闭环 |
| `tests/gateway/test_managed_worker_session_boundaries.py` | Gateway 会话能力和租户边界扩展 |
| `tests/gateway/test_plugin_post_message.py` | 原渠道回信和授权失败扩展 |
| `tests/hermes_cli/test_gateway_client.py` | `purpose="managed-session"` 连接契约 |
| `tests/tui_gateway/test_session_contracts.py` | `managed_worker` 与 `read_only` 的 canonical contract |

所有 Store/插件测试使用 `tmp_path` 和测试自动注入的临时 `HERMES_HOME`。需要 profile 的测试同时设置 `HERMES_HOME` 并按现有 profile fixture 模式 patch `Path.home()`。并发测试用 `Event` 或 `tests.waiting.wait_until`，不得用非零 `sleep`。

## 2. P0：合并阻断测试

### P0-01 插件发现、启停与卸载

**文件**：`tests/plugins/super_agent/test_plugin.py`

**前置/夹具**：真实 `PluginManager`、临时 `HERMES_HOME`、启用 `super_agent` 的插件配置；按现有 bundled plugin 测试清理全局注册表。

**步骤与断言**：

1. 通过真实 discovery 路径加载插件，而非直接调用私有注册函数。
2. 断言 `/super-agent` 命令和 admission hook 可调用；插件关闭时二者均不接管消息。
3. force reload 后同一个命令只执行一次，旧后台任务被取消，Store 路径仍位于当前 profile 的插件数据目录。
4. unload 后命令、hook 和后台任务注册被释放；再次 load 可正常恢复，不产生重复调度循环。
5. 在插件 host 模式加载：若所需 ctx 能力受 host 边界支持，断言行为与 in-process 一致；若 manifest 明确要求 in-process，断言 host 模式在加载期给出稳定、可操作的拒绝信息，且不启动 Router。

### P0-02 `sa:` admission 与 slash commands

**文件**：`tests/plugins/super_agent/test_plugin.py`

**场景**：

- `sa: 修复登录问题` 创建一个 Ticket，并返回 Ticket ID 与排队状态。
- 大小写、首尾空白按最终约定解析；普通消息、仅 `sa:`、未知前缀不创建 Ticket。
- 同一条 Gateway 事件被 admission 重放时只返回原 Ticket，不重复创建 Effect/Assignment。
- `/super-agent new <task>` 创建 Ticket；缺少 task、未知 sandbox、`full-access` 返回用户可理解的错误且无数据库写入。
- `/super-agent status <id>` 仅返回当前 profile 的 Ticket；不存在或其他 profile 的 ID 不泄露信息。
- `/super-agent list` 只列当前 profile，顺序与状态稳定；不依赖固定条目数量。
- `/super-agent tell <id> <text>` 只对非终态 Ticket 入 inbox；空消息和终态 Ticket 被拒绝。
- `/super-agent cancel <id>` 持久取消，重复 cancel 幂等。
- `/super-agent retry <id>` 仅允许失败/取消后按产品约定的状态；运行中和已成功 Ticket 被拒绝。
- Worker Session 内触发 `sa:` 或 `/super-agent new` 不递归启动 Router；Worker 只保留 sandbox hook。

**断言**：每个命令同时检查用户返回值和 Store 最终状态，避免只测字符串。

### P0-03 profile 级提交幂等

**文件**：`tests/plugins/super_agent/test_store.py`

**步骤**：用相同 `(profile, source_instance, external_event_id)` 连续和并发调用 `submit()`，再用不同 profile 或不同 source/event 提交。

**断言**：

- 同一 profile 内只产生一个 Trigger、Ticket 和首个 dispatch Effect；所有调用拿到相同 Ticket ID，且只有首个返回 `created=True`。
- 不同 profile 可复用相同外部 ID，互不读取或更新。
- 空 task、非法 sandbox（含 `full-access`）在事务前失败，不留下半条 Trigger。
- `source_json` 可往返 Unicode，并保持原渠道回信所需字段。

### P0-04 claim、稳定 turn identity 与两阶段状态

**文件**：`tests/plugins/super_agent/test_store.py`

**步骤与断言**：

1. `claim_next()` 按创建顺序领取，并在一个事务内把 Effect/Ticket/Assignment 切到 running。
2. 两个 Store 连接并发 claim 同一队列，只能有一个领取同一 Effect。
3. 同一 Assignment 重复 `ensure_initial_turn()` 返回同一 turn；`input_id` 稳定为 Ticket + generation + sequence，重启后不变。
4. `mark_turn_submitted()` 同时保存 turn 与 assignment 的 admission ID。
5. `mark_turn_terminal()` 重复执行不破坏结果或重复消费 inbox。
6. Assignment 绑定 Worker Session 后可通过 `assignment_sandbox(session_id)` 取到唯一等级；未知或旧 Session 返回 `None`。

### P0-05 重启恢复和 stale assignment 防护

**文件**：`tests/plugins/super_agent/test_store.py`

**步骤**：构造 running Ticket/Effect/Assignment/Turn、delivering inbox、sending outbox，关闭并重新创建 Store 后调用 `recover()`。

**断言**：

- running Assignment 变为 interrupted；running Ticket/Effect 回到可领取状态；running Turn 回 pending；delivering inbox 回 pending。
- 升级前未确认的在途记录保留为 unknown，由操作员核对后手动重投。新版 sending 在重启后按原投递标识重试。
- 恢复领取复用同一 Effect/Assignment 和稳定 input ID，不创建第二个业务动作。
- 旧 generation 的 `complete_if_idle()`、`fail_assignment()`、turn receipt 均不能覆盖新 generation 的状态或发送通知。
- recover 重复调用幂等。

### P0-06 cancel/retry/tell/follow-up 状态机

**文件**：`tests/plugins/super_agent/test_store.py`

**步骤与断言**：

- queued Ticket cancel：Ticket/Effect 进入取消终态，并只生成一次取消 Outbox。
- running Ticket cancel：持久标记取消；之后迟到的 Worker 成功/失败 receipt 不能复活 Ticket。
- 重复 cancel 不新增 Outbox。
- failed/cancelled Ticket retry：新增下一 attempt Effect，generation 递增，retry_count 递增；旧 Effect/Assignment 保持历史不可变。
- succeeded/running Ticket retry 返回 false。
- running Ticket 收到多条 tell：按 ID 顺序合并到一个 follow-up Turn；该 Turn 活跃时再次调度返回同一 Turn。
- follow-up Turn terminal 后 inbox 变 consumed；提交前崩溃恢复时 delivering 退回 pending。
- 有 pending/delivering inbox 时 `complete_if_idle()` 必须拒绝；全部消费后才能完成并产生一次完成 Outbox。

### P0-07 两槽 Router 调度

**文件**：`tests/plugins/super_agent/test_runtime.py`

**前置/夹具**：真实 Store；Gateway 客户端使用行为 fake，记录 create/resume/submit/receipt/cancel；用 `Event` 控制任务完成。

**步骤与断言**：

- 排入三个 Ticket，只启动两个 Worker；任一终止后第三个才开始。
- 同一调度 tick 不会对一个 Assignment 重复 create/submit。
- 一个槽创建/提交失败只失败对应 Assignment，另一槽继续运行。
- Router restart 后从 Store 恢复 interrupted/pending 工作，槽计数由持久状态重建，不因旧后台 task 永久占槽。
- unload/关闭会停止领取新 Ticket，取消自身调度 task，并对运行中 RPC 执行有界收尾；不会把用户 Session 当作 Worker 恢复。
- 迟到 receipt 带旧 admission/generation 时被忽略；当前 receipt 才推进状态。

### P0-08 Gateway managed-session 能力与双向只读边界

**文件**：扩展 `tests/gateway/test_managed_worker_session_boundaries.py`

**在现有测试基础上补充**：

- managed-session Ticket 只能按同一 purpose 兑换一次，过期、错 profile、错 purpose 均拒绝。
- Router 连接不能使用 host scope，也不能获得 approve/respond/operator 能力。
- Router A 创建的 Worker：Router A 可 create/read/resume/submit/control；Router B 只能按其 manager 身份被拒绝 read/control/submit。
- 普通用户可 list/read/resume Worker transcript，但 create/submit/stop/delete/reset/rename/steer/approve/respond 全部被统一授权层拒绝。
- Router 可读取用户 Session（含 list/info/resume），但 submit/control/mutate 均拒绝；读取结果不授予写能力。
- 用同一对用户 Session 与 Router/Worker Session 跑双向权限矩阵：`user -> router-owned` 和 `router -> user-owned` 都只允许 list/read/resume；submit/steer/stop/delete/reset/rename/approve/respond 逐项拒绝。断言拒绝后两边的 generation、transcript、cwd、sandbox、owner 和 pending admission 均未改变，证明“双向只读”不是仅靠 UI 隐藏写按钮。
- 用户连接不能兑换 managed-session Ticket、获得 `session:manage-worker` 或借用 Router principal；Router 读取用户 Session 也不能把它重新标记为 `managed_worker` 或绑定为 Assignment。
- 用户创建 Session 的 `source` 不能伪造成 `managed_worker`；Router 创建只能是 `managed_worker`。
- Router 创建 Worker 时强制进程隔离 policy；调用者传入会降低隔离的 launch mode、operator/yolo 组合或不允许的 toolset 时按最终契约拒绝或规范化。
- Worker 列表项和 resume info 都带 `read_only: true`，CLI/TUI canonical 转换后仍保留。

### P0-09 profile、Router 与 Worker 租户隔离

**文件**：`tests/gateway/test_managed_worker_session_boundaries.py`、`tests/plugins/super_agent/test_store.py`

**矩阵**：profile A/B × Router A1/A2 × Worker generation 1/2。

**断言**：

- profile A 的 Ticket、SessionRef、Router grant 不能用于 profile B。
- 同 profile 的另一 Router principal 不能读取或修改不属于自己的 Worker。
- Router A/B 各创建一个 Worker 后分别执行 list/read/submit/control 交叉矩阵：每个 Router 只看见并管理自己的 Worker；错误响应不泄露对方 Worker 的标题、cwd、sandbox、transcript 或 Session 是否正在执行。
- 同一 Router 的旧 generation Session 不得接收新 Assignment 的 submit/control。
- session listing 对普通用户显示本 profile Worker；对 Router 显示用户 Session 与自己拥有的 Worker，但不显示其他 Router 的 Worker。
- 未绑定 profile scope 的后台线程/回调失败关闭，不回落到启动进程的默认 profile。

### P0-10 Router 指定沙箱，Worker 强制执行

**文件**：`tests/plugins/super_agent/test_sandbox.py`

**参数化等级**：`read-only`、`workspace-write`、`workspace-exec`。

**步骤与断言**：

1. Router 提交每种等级，Assignment 持久保存且 Worker Session 使用相同等级。
2. Worker 的每次 terminal `pre_tool_call` 无条件把 args 中的 `sandbox` 覆盖为 Assignment 等级；模型传 `full-access`、未知值或省略字段都不能绕过。
3. 非 terminal 工具不被该 hook 改写。
4. 未知 Session、用户 Session、旧 Assignment Session 的 terminal 调用被拒绝，不回落到默认 sandbox。
5. `full-access` 在命令解析、Store trust boundary、runtime launch 三层至少有行为证明，均不创建 Worker。
6. sandctl/本地 sandbox 未启用或不可用时，Router 明确失败 Ticket，且 Gateway `session.create` 未发生。
7. Worker 只得到所需 terminal toolset；Router 自身不会获得 terminal/file 执行工具。

### P0-11 Worker Gateway RPC 生命周期

**文件**：`tests/plugins/super_agent/test_runtime.py`、`tests/hermes_cli/test_gateway_client.py`

**步骤与断言**：

- `connect_gateway(purpose="managed-session")` 向启动端只申请该 purpose，subject 稳定区分 Router 实例。
- 创建参数包含 `source="managed_worker"`、隔离 worktree cwd、既定 toolsets/yolo；提交参数包含 `finite=True`、`unattended=True` 和稳定 input ID。
- 首次执行 create→bind→submit；重启后已有 Session 走 resume→submit，不重复 create。
- Gateway 返回 duplicate input/admission receipt 时按同一次 Turn 收敛。
- session.create、resume、submit、receipt 任一步拒绝/断线都会留下可恢复状态，不把未确认提交标记成功。
- cancel 会控制当前 Router 所有的 Worker Session；控制拒绝或断线保留 Ticket 的持久 cancelled 状态，并记录可见错误。

### P0-12 Outbox 原渠道回信与不确定投递

**文件**：`tests/plugins/super_agent/test_store.py`、扩展 `tests/gateway/test_plugin_post_message.py`、`tests/plugins/super_agent/test_runtime.py`

**步骤与断言**：

- success/failure/cancel 各只 enqueue 一条终态通知，携带 Trigger 的原 `source_json`。
- `claim_outbox()` 原子地把 pending 改为 sending；两个 dispatcher 不能领取同一条。
- 明确成功后 delivered；明确发送失败后按最终策略回 pending 或 failed，并保留 error。
- 进程在发送后、receipt 前崩溃：新版记录用同一投递标识重试，Hermes 目的会话仅有一条消息；外部 IM 丢回执时可能重复。
- source 缺失、过期、所属 profile 不符、平台授权撤销时不发送，并记录错误；不得回落到当前绑定会话。
- source 合法时调用 `ctx.apost_message(text, source=source)`，实际 adapter 收到原 chat/thread/account 目标。
- Worker 无法直接发送消息；只有 Router/Outbox 路径调用 post_message。

### P0-13 Git worktree 创建、使用与失败恢复

**文件**：`tests/plugins/super_agent/test_worktrees.py`

**前置**：在 `tmp_path` 初始化真实最小 Git 仓库并提交一个文件；使用真实 `git`，不访问网络。

**步骤与断言**：

- 每个 Assignment 在 Store 指定路径创建独立 worktree，cwd 与仓库根解析正确；两个槽不会共享路径或分支。
- Worker Session cwd 精确等于 worktree，不是主 checkout。
- 非 Git workspace、脏主仓库、路径已存在、branch 冲突、git 命令失败时按最终产品约定明确失败，不启动 Worker，不删除用户文件。
- create 成功但 Gateway create/submit 失败时，清理由插件创建的 worktree；若目录有未提交 Worker 改动则保留并在 Ticket error 中给出路径。
- 正常完成/取消/重试的清理策略分别验证；只移除当前 Assignment 拥有的 worktree。
- restart 时已存在且属于同 Assignment 的 worktree 可复用；归属不匹配的路径拒绝复用。
- Windows 路径含空格与非 ASCII 时可创建、传给 Gateway 并清理，不依赖 shell 字符串拼接。

### P0-14 最小真实 E2E

**文件**：`tests/plugins/super_agent/test_e2e.py`

**前置**：临时 `HERMES_HOME`、真实 plugin discovery、真实 SQLite、真实 Gateway SessionAuthority/本地 WebSocket 客户端；模型 turn 和平台 adapter 只在外部边界使用确定性 fake，不 mock Store 或授权层。

**流程**：

1. 启用插件，通过机器人来源送入 `sa:` 事件。
2. Router 创建 Ticket，领取 Assignment，创建 managed Worker Session，提交初始 Turn。
3. fake Worker 返回中间结果；用户通过 `tell` 追加消息；同一 Session 收到 follow-up Turn。
4. Worker 返回终态；Ticket/Effect/Assignment/Turns/inbox 全部收敛。
5. Outbox 使用原来源回信；普通用户 session.list 可见 Worker 且只读。
6. 重放原事件、重放 receipt、重复调度，均不产生第二次执行或第二条终态回信。

**断言**：最终数据库关系、Gateway Session 可见性、adapter 调用和用户命令输出一致；测试退出后无活跃插件 task、Worker 进程或遗留可清理 worktree。

### P0-15 真实 Gateway + 微信 OpenClaw 回信闭环

**文件**：扩展 `tests/gateway/test_weixin_plugin.py`，并在 `tests/plugins/super_agent/test_e2e.py` 复用同一 fixture。

**前置/夹具**：启动真实 `GatewayRunner`、真实 `WeixinAdapter`、真实 Super Agent discovery 和临时 `HERMES_HOME`；仅把微信 iLink 的远端 HTTP 端点替换为 `127.0.0.1` 的 aiohttp fixture，不能 mock `GatewayRunner._plugin_post_message`、source/origin 恢复、adapter routing 或 Store。fixture 记录 `/sendmessage` 请求并返回固定成功 receipt。

**步骤与断言**：

1. 向真实 `WeixinAdapter._process_message()` 输入含唯一 `message_id`、`from_user_id`、`context_token` 和 `sa: <task>` 文本的 iLink payload。
2. 让消息走 adapter `handle_message`、Gateway admission、Super Agent Store/Router；断言 Trigger 保存的 source 含 `platform=weixin`、原 account/chat/user，Ticket 只创建一次。
3. 令 Worker 确定性完成并领取 Outbox；必须经 `ctx.apost_message(..., source=...)`、Gateway 授权、真实 `WeixinAdapter.send()` 到 loopback iLink。
4. 断言 `/sendmessage` 只收到一次，目标 `to` 等于原 `from_user_id`，使用同一 account 路由及已保存 context token；消息包含原 Ticket ID/终态，Store Outbox 为 delivered。
5. 重放相同微信 `message_id`、重启 Router 后重放 terminal receipt，均不得产生第二个 Ticket、Worker 或微信发送请求。
6. 撤销 Gateway 平台授权或把 source 改成另一 profile/account，断言发送被拒绝且 loopback iLink 零请求；不得回退到当前聊天或默认 profile。

该自动化用例运行真实 Gateway 和微信 adapter，只隔离外部网络。另设 secrets-gated `@pytest.mark.live` canary（默认不收集网络凭据）：仅在专用测试微信账号/会话显式配置时发送带随机 nonce 的一条任务，等待同一会话收到包含 nonce 的终态回信，并在超时后失败；不得使用个人聊天作为固定目标。

### P0-16 Router pause/resume 持久化与运行中 Worker

**文件**：`tests/plugins/super_agent/test_store.py`、`tests/plugins/super_agent/test_runtime.py`、`tests/plugins/super_agent/test_plugin.py`

**前置/夹具**：真实 Store；行为 Gateway fake 用 `Event` 把 Worker A 停在已提交 admission，另准备 queued Ticket B 和 pending Outbox。不得用 `sleep` 判断暂停。

**步骤与断言**：

1. 调用 `/super-agent pause`，断言 `router_state.paused=true` 持久写入，只产生一条 pause control event；重复 pause 返回 already paused，不新增重复审计。
2. 暂停期间连续调用 Store `claim_next()` 和 `claim_outbox()`，都返回 `None`；统计 Gateway create/submit 和 `ctx.apost_message` 调用均为 0，queued Effect 与 pending Outbox 状态不变。
3. 暂停发生前已运行的 Worker A 不被 cancel，仍继续轮询 receipt、消费 follow-up 并可进入 succeeded/failed；断言没有 `prompt.cancel`，原 Session/worktree/sandbox 不变。
4. Worker A 在暂停期间完成时，终态 Outbox 保持 pending，不发送；queued Ticket B 不启动。
5. 关闭 Runtime/Store 后用同 profile 重建并调用 `recover()`，断言仍 paused，恢复过程不会自动 claim B 或终态 Outbox。
6. 调用 `/super-agent resume`，只产生一条 resume control event；调度被唤醒后 B 可 claim，pending Outbox 可发送。重复 resume 幂等，不新增审计。
7. profile A pause 不影响 profile B 的 claim/delivery；两个 profile 重启后各自状态保持。

### P0-17 独立 Watchdog、不可变执行状态与长命令误报

**文件**：`tests/plugins/super_agent/test_store.py`、`tests/plugins/super_agent/test_runtime.py`、`tests/plugins/super_agent/test_plugin.py`

**前置/夹具**：构造一个 running Assignment/Turn，把持久 `updated_at` 置于阈值之前；在扫描前记录 Ticket、Effect、Assignment、Turn、inbox、sandbox、worker_session_id、admission_id、初始 prompt 文本和 worktree 的完整行为快照。使用可控时钟或 patch 模块 `_now()`，不用真实等待。

**步骤与断言**：

1. 首次 `scan_watchdog(threshold)` 只新增一条 active `watchdog_alerts`、一条唯一 kind 的 pending Outbox，并由 Runtime 写一条 warning 日志。
2. 对比扫描前后快照：Ticket/Effect/Assignment/Turn/inbox 状态与结果不变，sandbox、prompt、Session、worktree、generation、retry_count 不变；Gateway 未收到 create/submit/cancel/control，Watchdog 不执行 retry/takeover/stop。
3. 同一 Assignment 仍 stale 时重复扫描，只更新 alert 的 `last_seen/detail`，不新增 alert、Outbox 或“新告警”日志。
4. 模拟正常长 terminal/模型命令：`prompt.receipt` 持续返回 running 并跨过阈值。断言提示只陈述“无持久状态推进”并建议人工检查，不宣称 Worker 已死、失败或必须重试；Worker task/admission 继续运行。
5. 长命令随后返回 completed：正常完成当前 Turn/Ticket；下一次扫描把 alert 标为 resolved。若告警 Outbox 尚未发送，则改为 failed 并注明 resolved-before-delivery，避免过期误报；已发送告警保留审计但不重复发送。
6. 非终态 Worker 产生新的持久 Turn/Assignment 进度后再次扫描，同样 resolve；状态再次长期不推进时可重新激活同一唯一 alert，并只生成一条新的告警 Outbox。
7. failed/cancelled/taken_over/succeeded Assignment 不产生 active alert；阈值为 0、负数或非有限配置在启动/调用边界被拒绝。
8. `/super-agent status <id>` 显示 active alert 的 detail/suggestion；`health` 只统计 active alerts，resolved alerts 不计入活动数。

### P0-18 takeover、admission 取消、保留现场与三次重试

**文件**：`tests/plugins/super_agent/test_store.py`、`tests/plugins/super_agent/test_runtime.py`、`tests/plugins/super_agent/test_plugin.py`、`tests/plugins/super_agent/test_worktrees.py`

**前置/夹具**：running Ticket generation 1，已绑定 Worker Session、worktree 和 running Turn admission；Gateway fake 记录 resume/cancel/delete/create，worktree 内放一个未提交的审查文件。

**步骤与断言**：

1. `/super-agent takeover <id>` 原子地把 Ticket 和当前 Assignment 持久为 `taken_over`、未完成 Effect 为 done，记录 takeover control event 和唯一 taken_over Outbox。
2. Runtime 只调用原 Session 的 `session.resume` + `prompt.cancel(admission_id)`；不调用 session.delete/reset，不删除或清空 transcript，不删除 worktree。即使远端 cancel 断线/拒绝，持久 `taken_over` 仍是权威状态并保留可见错误日志。
3. 用户仍可在会话列表只读打开旧 Worker Session，worktree 和未提交文件仍存在；sandbox、任务文本、原 Turn/admission 历史不可变。重复 takeover 不新增 Outbox/control event，也不再次 cancel。
4. 对 taken_over Ticket 调用 retry 第 1 次：Ticket 回 queued，retry_count=1，新 dispatch Effect attempt 递增；claim 后生成 generation 2、新 Assignment/Session/worktree，旧 generation 1 保持 taken_over 且仍可审查。
5. 对 generation 2、3 分别执行 takeover/失败后 retry，验证 retry_count 依次为 2、3，generation 依次新增且每代使用独立稳定 input ID；sandbox 不升级，原 task/prompt 授权范围不变。
6. retry_count=3 后再次 takeover/失败，再调用第 4 次 retry：返回拒绝文案，Ticket 保持终态，不新增 Effect/Assignment/generation/Session/worktree/control event，Gateway 零调用。
7. 迟到的旧 generation receipt 不得把 taken_over 或新 generation 覆盖为 succeeded/failed；旧 admission 也不能接收新的 tell。

### P0-19 recover 的 restart_requeue 审计与幂等

**文件**：`tests/plugins/super_agent/test_store.py`、`tests/plugins/super_agent/test_runtime.py`

**步骤与断言**：

1. 构造两个本 profile running Assignment 和一个其他 profile running Assignment，调用 `recover()`。
2. 每个本 profile Assignment 恰有一条 `restart_requeue` control event，包含对应 Ticket/Assignment；本 profile Ticket/Effect/Turn/inbox/outbox 按既有恢复语义推进，其他 profile 完全不变。
3. 第二次及并发重复 `recover()` 不新增 `restart_requeue`，不再次改变 generation/retry_count/input_id，也不重复 enqueue Outbox。
4. 重建 Runtime 后先完成 recover 再启动 Router/Watchdog；`health` recent controls 和对应 Ticket `status` 的 Recovery record 都能看到 restart_requeue。
5. recover 发生在 paused profile 时保留 paused；只重排已中断工作，不启动 claim 或 delivery，直到显式 resume。

### P0-20 health/status/list/stop、重启审计与插件禁用

**文件**：`tests/plugins/super_agent/test_plugin.py`、`tests/plugins/super_agent/test_runtime.py`

**步骤与断言**：

1. 参数化 `/super-agent health`：running/paused Router、0/多个 active alerts、不同 Ticket 状态和最近 pause/resume/restart_requeue/retry/cancel/takeover control events；断言输出包含状态、阈值、按状态计数、active alert 建议和最近控制动作，不泄露 token/source 凭据。
2. `/super-agent list` 首行显示 Router 状态和 active alert 数；每个 Ticket 只显示 ID/状态/sandbox，不因 taken_over 或 restart 后崩溃。
3. `/super-agent status <id>` 显示 Worker Session/worktree、active Watchdog、`retry_count/3` 和该 Ticket 的 recovery/control record；其他 profile/未知 Ticket 不泄露信息。
4. `/super-agent stop <id>` 与 cancel 行为一致：持久 cancelled、记录 cancel event/Outbox，并对当前 admission 执行 resume + prompt.cancel；重复 stop 幂等，终态 Ticket 不产生新调用。重启后 health/status 仍可见该审计。
5. 通过真实 PluginManager 启用后再禁用 `super_agent` 并 force reload：unload 停止 Router/Watchdog task，之后 `post_gateway_admission` 不再由插件处理 `sa:`，不创建 Trigger/Ticket/Outbox，也不发送 Super Agent 回执。
6. 同一条 `sa:` 文本在插件禁用时继续进入 Gateway 的普通对话 admission/agent 路径并得到普通回复；普通非 `sa:` 消息在启用前后行为一致，证明禁用插件不会吞消息。
7. 禁用时保留既有 SQLite/Session/worktree；重新启用后按 recover 规则审计并排队，不能在禁用期间后台执行或回信。

### P0-21 `settings_page=root` 从 manifest 到 `AgentPluginRow`

**文件**：扩展 `tests/tui_gateway/test_plugins_manage_settings.py`、`apps/desktop/src/store/agent-plugins.test.ts`

**步骤与断言**：

1. 在临时 `HERMES_HOME` 安装一个带 `config_schema` 和 `settings_page: {location: root, title: Super Agent, icon: hubot, order: 85}` 的 native plugin，通过真实 `plugins.manage {action: list}` 读取，不直接调用 manifest parser。
2. 断言该 row 的 `settings_page` 完整保留 `location/title/icon/order`，`settings_schema` 仍包含全部设置字段；enable/disable/settings 保存后的 refreshed row 也保留相同元数据。
3. 把 RPC row 交给 Desktop `normalizeAgentPluginRow`，断言 `AgentPluginRow.settings_page` 不被丢失或改名，其他字段缺省规范化不改变该对象。
4. 旧后端 fixture 完全省略 `settings_page`，Desktop 可解析且不报错；显式 `null` 与省略字段得到相同兼容结果。
5. 行为验证 metadata trust boundary：非 mapping、非 `location=root`、空 title/icon、bool order 不提升页面；合法字段被裁剪空白。测试调用 `plugins.manage list` 观察结果，不读取 `plugin.yaml` 源码文本。

### P0-22 Desktop 一级菜单提升、去重与旧后端回落

**文件**：扩展 `apps/desktop/src/contrib/settings-pages.test.ts`、`apps/desktop/src/app/settings/plugin-settings.test.tsx`，必要时新增 `apps/desktop/src/app/settings/plugin-settings-root.test.tsx`

**前置/夹具**：构造一个 Super Agent row（有 schema、`settings_page.location=root`）和一个 legacy row（有 schema、无 metadata），通过真实 `pluginSettingsEntries`、`pluginSettingsTopLevelNav`、`pluginSettingsNavChildren` 与 Settings route harness 渲染。

**步骤与断言**：

1. Super Agent 只在 Settings 左侧一级菜单出现一次，标题为 `Super Agent`、图标解析为 hubot、排序按 order=85；点击后打开同一个自动生成的 schema form。
2. 同一 Super Agent entry 不出现在 `Plugins` group children、Plugins overview/search 的重复导航项或同一菜单的 desktop/agent 双份条目；用稳定 nav id/可见标签和点击后的 route 行为断言，不读组件源码。
3. 一级菜单 active 时 `Plugins` group 不 active；从 deep link `?tab=plugins&agent=super_agent&profile=<p>` 打开时仍选中一级 Super Agent 项，刷新后不降级到 overview。
4. legacy row 无 `settings_page` 时按原行为落入 `Plugins` group，页面可打开和保存；这证明 Desktop 对旧后端兼容，而不是要求所有后端同步升级。
5. root row 与同 package 的 Desktop contribution 并存时不折叠到 Desktop plugin 的 child；legacy row 仍可按原 ownership 规则折叠。
6. profile 切换、plugin disable/uninstall 或 RPC list 移除 row 后，一级菜单及时消失；当前 route 回到安全 overview，不留下指向已删除 entry 的可写表单。

### P0-23 `message_sources` 与新 Worker provider/model

**文件**：扩展 `tests/plugins/super_agent/test_plugin.py`、`tests/plugins/super_agent/test_runtime.py`

**`message_sources` 参数矩阵**：`["*"]`、`["weixin"]`、多个平台、大小写/首尾空白、`[]`、未知平台、非 list、含非字符串元素。

**步骤与断言**：

1. `*` 接管任意有 platform 的 `sa:` 入站；显式列表只接管匹配平台，比较时大小写无关并裁剪空白。
2. `[]`、未知或不匹配平台时 hook 返回 `None`，不创建 Trigger/Ticket/Outbox、不回复 Super Agent 文案；同一 `sa:` 消息继续进入普通 Gateway 对话路径。
3. 非法 `message_sources` 对 `sa:` 返回明确配置错误且不建 Ticket；普通非 `sa:` 消息仍返回 `None`，不能因插件配置错误吞掉普通对话。
4. 修改列表后下一条 Gateway admission 立即使用新值；已经接纳的 Ticket、运行 Worker 和已持久 source 不被追溯取消或改写。
5. `worker_provider` 与 `worker_model` 都非空时，只在全新 managed Worker 的 `session.create` 参数中出现；单独设置一个时只发送该字段，空字符串时两个字段都省略并继承 profile 默认值。
6. Assignment 已绑定 `worker_session_id` 时走 `session.resume`，即使设置刚改变也不发送 provider/model、不重新 create、不改变旧 Session 模型。
7. retry 产生新 generation 且需要新 Session 时读取最新 provider/model；旧 generation Session 保持原值。非字符串设置明确失败当前 Assignment，并在任何 Gateway create 前停止。

### P0-24 `max_concurrent_workers` 1–8 的 Router 全局并发

**文件**：扩展 `tests/plugins/super_agent/test_runtime.py`

**前置/夹具**：真实 Store 排入 9 个 Ticket；Gateway/Worker fake 用 `Event` 阻塞每个 Worker，并原子记录当前与历史峰值 in-flight 数。不得用 wall-clock `sleep` 推断并发。

**步骤与断言**：

1. 参数化字符串配置 `"1"` 至 `"8"`，启动真实 Router loop；断言峰值恰为 `min(limit, 9)` 且任何时刻不超过 limit。释放一个 Event 后才允许领取下一个 Ticket。
2. 并发限制是该 profile Router 的全局 Worker task 数，不是单个 Ticket 的子 Worker 数：每个 running Ticket 恰好一个 Assignment、一个 Worker Session、一个活跃 worker task；follow-up turn 复用该 Worker，不增加并发计数。
3. 明确记录当前产品边界：每 Ticket 仍为单 Worker，`max_concurrent_workers=8` 表示最多 8 个 Ticket 并行，不允许一个 Ticket 扇出 8 个 Worker。
4. 运行时把 limit 从 4 降到 2：已有 4 个 Worker 不被 cancel；在活跃数降到 2 以下前不 claim 新 Ticket。把 2 升到 5：下一调度轮次补充到 5，无需重启。
5. profile A/B 各自使用自己的 limit；同一进程中 A 的峰值不消耗 B 的配额，也不能把两个 Router 错误合并成进程全局限制。
6. `0`、`9`、负数、bool、float、`"2.0"`、`"02"`、空字符串和非数字在读取边界拒绝；不 claim Ticket、不创建 Session。manifest/桌面选择器只提交 `1`–`8`。

### P0-25 设置保存的 profile 隔离与热生效边界

**文件**：扩展 `tests/tui_gateway/test_plugins_manage_settings.py`、`apps/desktop/src/store/agent-plugins.test.ts`、`apps/desktop/src/app/settings/plugin-settings-ownership.test.tsx`、`tests/plugins/super_agent/test_plugin.py`、`tests/plugins/super_agent/test_runtime.py`

**步骤与断言**：

1. 建立 profile A/B，各自通过真实 `plugins.manage {action: settings, key: super_agent, profile: ...}` 保存不同 `message_sources`、provider/model、max workers、workspace/sandbox/watchdog；断言只写目标 profile 的 `config.yaml`，另一个 profile 保持默认或原值。
2. 分别 `plugins.manage list` A/B，row 的 schema value 与各自配置一致；快速 A→B→A 切换时迟到 RPC 不覆盖当前 profile row。Desktop save 请求始终携带 Settings profile，A 的 refreshed row 不写入 B 当前列表。
3. `message_sources` 在保存后的下一条 admission 热生效；`max_concurrent_workers` 在下一 Router 调度轮次热生效，遵守“降额不取消现有 Worker，升额只补新 Worker”的 P0-24 边界。
4. `worker_provider`/`worker_model` 只在下一次创建新 Worker Session 时生效；已创建或 resume 的 Session 不变。`default_sandbox` 与 `workspace_root` 只影响保存后新建的 Ticket/Assignment，现有持久任务不迁移。
5. `watchdog_seconds` 是 Runtime 构造时快照：保存后当前 Watchdog 阈值不变，插件 reload/restart 后使用新值。Desktop 应显示已保存值，但不得暗示当前 Watchdog 已即时重启。
6. profile A 的热更新只唤醒/影响 A Router；B 的 intake、并发、Worker 模型、沙箱和 Watchdog 均不变。跨 profile 的既有 Ticket/Session/worktree 不能被重新绑定。
7. 保存非法 JSON/type/value 时后端原子拒绝，两个 profile 均不产生部分写入；Desktop 保留可编辑状态并显示失败，不把本地 row 当作已保存。
8. profile 在保存中途切换时，旧 profile 的成功写入仍只落旧 profile，但 UI 不把返回 row 合并进新 profile；未保存表单值在 scope 变化后按现有 ownership 规则丢弃或重新加载，不能误写下一 profile。

## 3. P1：重要回归

### P1-01 CLI/TUI canonical contract

**文件**：`tests/tui_gateway/test_session_contracts.py` 及已有 session contract 测试。

- `source="managed_worker"` 可跨 canonical/local/TUI 三种转换往返。
- `read_only` 缺省兼容旧 payload；新 Worker payload 在列表、详情、resume 后均为 true。
- 未知新字段仍按兼容契约忽略，旧客户端不会因 Worker Session 出现而崩溃。
- 用户界面对 Worker 的写动作依据服务端拒绝收敛；测试不得只断言按钮是否存在。

### P1-02 status/list 输出与敏感信息

**文件**：`tests/plugins/super_agent/test_plugin.py`

- queued/running/waiting_user/succeeded/failed/cancelled 的输出包含必要状态、Ticket ID、可操作的下一步。
- 不输出 bootstrap ticket、Gateway token、完整 source 凭据、环境变量或超出上限的 Worker error。
- 超长 task/result/error 有边界且 Unicode 不损坏。

### P1-03 并发竞态

**文件**：`tests/plugins/super_agent/test_store.py`、`test_runtime.py`

- submit 与 cancel 并发：最终只能是未开始或 cancelled，不能 cancelled 后启动。
- complete 与 tell 并发：tell 若已持久化则阻止完成或形成明确的新 follow-up；不得丢消息。
- cancel 与 Worker receipt 并发：cancel 胜出后终态不可复活。
- retry 与迟到旧 receipt 并发：旧 generation 不覆盖新 generation。
- 两个 Router loop 被意外启动时，SQLite claim 仍保证单次 Assignment；第二个 loop 不突破两槽总约束。
- outbox settle 与 recover 并发后，新版待重试通知保持同一投递标识，已确认的回报不被迟到的失败回执覆盖。

### P1-04 数据库损坏与迁移

**文件**：`tests/plugins/super_agent/test_store.py`

- 空目录首次启动建库；再次启动保持数据。
- 旧 schema fixture 经公开 Store 构造迁移后仍可 list/recover/claim；fixture 只包含持久数据，不读取 migration 源码。
- 外键损坏、不可写目录、非 SQLite 文件给出明确启动失败，Router 不部分运行。
- WAL/连接关闭后可备份复制并重新打开；测试不写真实用户目录。

### P1-05 Gateway 生命周期

**文件**：Gateway 现有 bootstrap/socket/session 测试目录。

- Gateway draining/restart wait 对 managed Router 连接按设计关闭或重连，不让后台连接阻塞 drain。
- Ticket 单次兑换和断线重连不会扩大 capability。
- Worker Session prune/delete/retire 后 Router 的旧引用得到 not_found/permission_denied，不访问已淘汰 agent。
- 多 profile Gateway 在 owner scope 外执行 Router callback 时失败关闭。

### P1-06 安全输入

**文件**：`tests/plugins/super_agent/test_plugin.py`、`test_worktrees.py`、`test_sandbox.py`

- task、tell、workspace、Ticket ID 中的换行、引号、`..`、绝对路径、shell 元字符不会进入 shell 拼接或越过 workspace。
- symlink/junction 指向 workspace 外时不被 worktree 清理递归删除。
- source payload 中伪造 profile/session/adapter 字段不能改变授权主体。
- 模型输出不能修改 Store 中 sandbox、workspace、session owner 或 generation。

### P1-07 Windows 行为

**文件**：`tests/plugins/super_agent/test_worktrees.py`、`test_runtime.py`；需要真实进程拓扑的用例标记 `@pytest.mark.platforms("windows")`。

- 含盘符、空格、中文和长路径的仓库/worktree 可运行。
- Worker 子进程使用项目已有 hidden-window/process-group 机制，不弹窗口；取消能结束完整进程树。
- 文件仍被 Worker 占用时清理不会无限重试或删除别的目录，而是保留路径并报告。
- 不伪造 `sys.platform`。仅真实 Windows 进程行为进入平台 lane；纯路径 helper 以路径数据参数化。

### P1-08 插件隔离与公共 API

**文件**：`tests/plugins/super_agent/test_plugin.py`、现有 plugin isolation/boundary 测试。

- 插件只使用列入公共 API 的 core 名称或 ctx 方法；通过真实 boundary lint 行为验证，不在测试中读取 `plugin_api.json` 文本做快照。
- host wire 可序列化所有调用参数/返回值；不得把 Gateway adapter、live SessionAuthority、SQLite connection 跨进程传递。
- in-process/host 两种允许模式下 profile 数据目录一致且互不串租户。

## 4. P2：容量与运维

### P2-01 队列和容量

**文件**：`tests/plugins/super_agent/test_runtime.py`

- 批量排队时始终最多两个运行 Worker，完成顺序变化不丢任务。
- `list` 的 limit 生效且稳定，不断言固定总数。
- 大量 inbox/outbox 后查询仍按索引关系返回正确结果；只设置宽松上限，避免脆弱计时断言。

### P2-02 长时间恢复与人工核对

**文件**：`tests/plugins/super_agent/test_store.py`、`test_runtime.py`

- 升级前的 unknown Outbox 可在 status 查看，仅在显式 redeliver 后重试。
- 长期 interrupted Assignment 可 retry/cancel，不自动获得更高沙箱等级。
- orphan worktree 扫描只报告 Super Agent 命名空间内目录，绝不删除无法证明归属的目录。

### P2-03 渠道兼容矩阵

**文件**：平台 adapter 已有测试旁或 `tests/plugins/super_agent/test_e2e.py` 参数化 source envelope。

- 当前已配置机器人渠道与微信 OpenClaw 至少各提供一条 source envelope fixture；微信行必须复用 P0-15 的真实 Gateway/`WeixinAdapter` loopback 闭环，不能只调用 formatter 或直接调用 `send()`。
- 验证私聊、群聊/thread/reply anchor 在终态回信中保持；无 thread 能力的平台只退回原会话。
- 平台消息编辑、重复 delivery、adapter 重启不改变 Trigger 幂等键。

## 5. 最小阻断命令

实现阶段按改动范围先运行：

```bash
scripts/run_tests.sh tests/plugins/super_agent/
scripts/run_tests.sh tests/gateway/test_managed_worker_session_boundaries.py tests/gateway/test_plugin_post_message.py
scripts/run_tests.sh tests/hermes_cli/test_gateway_client.py tests/tui_gateway/test_session_contracts.py
python scripts/check
```

若建议文件尚未创建，使用已存在的对应目录或真实文件名，不得改用裸 `pytest`。

## 6. 完整回归与验收

```bash
scripts/run_tests.sh tests/plugins/ tests/gateway/ tests/hermes_cli/ tests/tui_gateway/
scripts/run_tests.sh
python scripts/check
```

最终验收还需一次本机人工冒烟：启用插件，先从一个已配置机器人渠道、再从专用微信 OpenClaw 测试会话各提交一条带不同 nonce 的 `sa:` 任务；确认两槽限制、Worker Session 在会话列表只读可见、用户 Session 与 Router 双向只读、`tell/cancel/retry` 生效、三种沙箱等级均由 Router 指定且 `full-access` 被拒绝，并确认每个 nonce 只回到各自原渠道/原会话一次。记录 Ticket ID、Worker Session ID、微信发送 receipt 和时间戳；人工冒烟只补真实渠道/进程证明，不能替代上述自动化行为测试。
