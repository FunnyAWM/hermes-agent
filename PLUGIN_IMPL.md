# Hermes 插件系统机制（上游实现调研）

> 调研对象：nousresearch/hermes-agent 基座（本仓 fork 的上游部分）的插件系统。
> 事实来源为仓内代码静态阅读，行号以当前工作区为准；权威一手文档是 `plugins/AGENTS.md` 与
> `website/docs/developer-guide/plugins/`（`index.md` 为作者指南 + 兼容契约原文，`public-api.md`
> 为公开 API 渲染产物，`catalog-submission.md` 为目录投稿指南）。本文是机制地图，供本 fork
> 二次开发时定位用，**不替代**上述文档。

---

## 1. 设计哲学：core 是窄腰，能力在边缘

两条不变量决定了插件系统的几乎所有形状（根 `AGENTS.md`）：

1. **每会话 prompt 前缀缓存神圣不可侵犯。** 插件不得在会话中途改变系统提示词/工具面；
   改变系统提示词状态的注册延到下一会话生效（`hermes_cli/plugins_activation.py` 的激活摘要：
   handlers 立即生效、tools/prompt 下一会话、MCP 服务器等 `mcp.reload`）。
2. **core 不长能力，插件长能力。** 每个模型工具都会随每次 API 调用下发，因此新核心工具的门槛极高；
   新能力以「CLI 命令 + skill」「服务门控工具」「插件」形式到达。

两条铁律（`plugins/AGENTS.md`）：

- **插件永不修改 core 文件**（`run_agent.py`、`cli.py`、`gateway/run.py`、`hermes_cli/main.py` 等）。
  缺能力时扩宽**通用**插件面（新 hook、新 ctx 方法），而不是给 core 塞插件专属逻辑。
- **core 与插件互不 import**：core 不 import 任何具体内置插件；插件只用公开 API
  （`hermes_cli/data/plugin_api.json` 白名单 + `ctx` + 文档化 ABC）。由 `scripts/boundary_lint.py`
  双向执法（见 §8）。

## 2. 插件种类与发现系统

| 种类 | 位置 | 发现机制 | 要点 |
|---|---|---|---|
| 通用（general） | `plugins/<name>/`、`~/.hermes/plugins/`、`./.hermes/plugins/`、pip entry points | `PluginManager`（`hermes_cli/plugins.py` facade，实现在 `plugins_discovery.py`/`plugins_loader.py`） | **later-wins**（project > user > bundled，`plugins_discovery.py:222-245`）；`register(ctx)` 注册 hooks/tools/CLI 命令/语言包 |
| 记忆提供方 | `plugins/memory/<name>/` | `plugins/memory/__init__.py`：bundled → `$HERMES_HOME/plugins/` → `./.hermes/plugins/`（`HERMES_ENABLE_PROJECT_PLUGINS`）→ `hermes_agent.memory_providers` entry points | **bundled-first**（防同名目录顶掉内置提供方）；由 `memory.provider` 按名激活；实现 `MemoryProvider` ABC（`agent/memory_provider.py`），由 `agent/memory_manager.py` 编排 |
| 模型提供方 | `plugins/model-providers/<name>/` | `providers/__init__.py._discover_providers()`，**惰性**（首次 `get_provider_profile()`/`list_providers()` 时） | `__init__.py` 调 `providers.register_provider(ProviderProfile(...))`；**last-writer-wins**，用户插件可覆盖内置 profile；`PluginManager` 记录其 manifest 但不 import（防双重实例化） |
| 上下文引擎 / 图像生成 / 视频 / 委托 / 网页搜索 / 浏览器 / TTS / 转写 / cron 提供方 / dashboard 登录后端等 | `plugins/context_engine/`、`plugins/image_gen/`、`plugins/delegation/`、`plugins/web/`、`plugins/browser/`、`plugins/cron_providers/`、`plugins/dashboard_auth/` … | 「ABC + 编排器 + per-plugin 目录」模式 | 分别接入 `agent/context_engine.py`、`agent/image_gen_provider.py`、`agent/delegation_provider.py` 等；`plugins/delegation/default` 就是现行委托路由 |
| 平台适配器 | `plugins/platforms/<name>/adapter.py` | gateway 加载 | token-lock 与 scoped-secret 规则见 `gateway/AGENTS.md`（`irc`、`feishu` 为范本） |

发现时机坑：`discover_plugins()`（`hermes_cli/plugins.py:878`）只是 import `model_tools.py` 的副作用；
不 import `model_tools.py` 就读插件状态的代码必须显式调用（幂等）。非强制调用在 `_discovered`
上短路；一切运行中加载路径（install/enable/update、`reload-plugins`）走 `discover_plugins(force=True)`。

## 3. Manifest（`plugin.yaml` / `plugin.yml` / `plugin.json`）

解析在 `hermes_cli/plugins_manifest.py`（`PluginManifest` 数据类 L360-419）。目录插件扫描深度限 2
（`plugins_discovery.py:141-187`）。关键字段：

| 字段 | 含义 |
|---|---|
| `name` / `key` | `key` 是路径派生的注册表 id（`disk-cleanup`、`image_gen/openai`），供 `plugins.enabled/disabled` 与 `hermes plugins list` 用；空则取 `name` |
| `kind` | `standalone`（默认，需 `plugins.enabled` 显式启用）/ `backend`（core 工具的可插拔后端；bundled 自动加载、用户安装需门控）/ `exclusive`（单活提供方，由 `<category>.provider` 选中，通用扫描跳过）/ `platform`（网关适配器） |
| `version` / `description` / `author` / `license` / `homepage` / `tags` | 元数据 |
| `requires_hermes` | 版本要求（`">=0.19"`，逗号分隔）；不满足则**导入前**记录错误并跳过 |
| `requires_plugins` | 建议性依赖 `[{"id","version_range"}]`；缺失只警告，但影响加载顺序 |
| `python_dependencies` | 声明 pip 依赖——**只校验展示，永不自动安装** |
| `provides_tools` / `provides_hooks` | 声明提供的工具/钩子名 |
| `capabilities` | 声明的能力 id。**声明是同意元数据、不是授权**；实际生效靠 `plugins.entries.<id>.granted_capabilities`（或旧 `allow_*`）（#64228） |
| `manifest_version` / `api_version` | v2 增量字段；文件格式版本 / 运行时 API 代数；v1 manifest 永久支持 |
| `config_schema` | `plugins.entries.<id>.settings` 的 schema；不匹配只警告 |
| `config_keys` | 插件会读的 config 点路径（如 `memory.retaindb`），使 `hermes config get/set` 可识别 |
| `emits` / `listens` | 事件总线声明（仅可发现性提示） |
| `provides_locales` / `locale_metadata` | 纯 manifest 语言包：`locales/<id>[.tui|.desktop].yaml` 自动注册，无需 `__init__.py`；由 `hermes_cli/plugin_validate_locales.py` 校验 |
| `portable` / `data_namespace` | 便携插件的 `plugin-data/<namespace>`；其 skill 命名为 `<name>:<skill>` |

kind 启发式推断（`plugins_manifest.py:287-298`）：manifest 没写 `kind:` 时按源码特征判——含
memory/cron/computer-use provider 标记者为 `exclusive`，含 `register_provider` + `ProviderProfile`
者为 `model-provider`。未知 manifest 字段一律忽略（兼容契约）。

## 4. 加载生命周期

1. **发现**：`discover_plugins()` 按 §2 顺序扫目录 + entry points，解 manifest，同名 later-wins
   （memory 提供方例外：bundled-first）。
2. **加载**：`plugins_loader.py:564-621`——创建 `PluginContext`，按 source 选宿主进程/本地模块/entry
   point，调用插件的 `register(ctx)`；**失败即卸载已注册的一切**并把错误记进
   `LoadedPlugin`（`hermes_cli/plugins.py:222`），在 `hermes plugins list` 呈现为 load error。
3. **激活**：`PluginManager.on_plugin_loaded` 在 force 扫描内对新加载插件触发，产出激活摘要
   （`plugins_activation.py`）：handlers 立即生效；tools/prompt 下一会话生效（保前缀缓存）；
   `deferred.mcp_servers` 等 `mcp.reload`。
4. **卸载/重载**：`ctx.on_unload` 回调 + 注册追踪（`_track_callback`/`_track_scoped_registration`）
   保证 force-reload 时旧注册被移除。
5. **profile scope**：一个进程服务多个 profile；`on_session_start/end`、`sync_turn`、`shutdown`
   等生命周期钩子由调用方绑定所属 profile scope（`_run_release_in_profile_scope` 等）后触发。
   提供方**不得**把 `initialize()` 时拿到的 `hermes_home` 缓存为「唯一的家」，也**不得**读
   `os.environ` 取凭证（用 `agent.secret_scope.get_secret`）；后台线程走
   `agent.memory_provider.spawn_context_thread`，裸 `threading.Thread` 会失去 scope 而 fail-closed。

## 5. PluginContext：插件能做的事

`register(ctx)` 是唯一入口，ctx 由 `hermes_cli/plugins_context.py` 提供（方法清单见该文件）：

- **工具**：`register_tool(name, toolset, schema, handler, check_fn, requires_env, is_async, ...)`
  （L270-307）写入 `tools.registry` 全局注册表，随工具面下发给模型。同名覆盖需要 `override=True`
  **且**运维在 config 里设 `plugins.entries.<plugin_id>.allow_tool_override: true`——否则任何插件
  都能静默顶掉 `write_file` 这类特权内置工具（#23194）。
- **钩子**：`register_hook(hook_name, callback, hold_stream=False)`（L741）；`register_middleware`。
- **MCP**：`ctx.call_mcp(server, tool, args)` 走 `tools.mcp_tool` 原生客户端（同信任门/熔断/重连），
  但受 `plugins.entries.<id>.mcp_allowlist` **默认拒绝**管控（L309-338）。
- **CLI**：`register_cli_command`（L442）把 argparse 子树挂进 `hermes` 命令树（启动时接线，无需改
  `main.py`）。记忆提供方的 `cli.py::register_cli(subparser)` 由 `discover_plugin_cli_commands()`
  接线，且只对**激活的**提供方生效，保持 `hermes --help` 干净。
- **提供方注册**：`register_memory_provider` / `register_context_engine` / `register_image_gen_provider`
  / `register_video_gen_provider` / `register_web_search_provider` / `register_browser_provider`
  / `register_tts_provider` / `register_transcription_provider` / `register_delegation_provider`
  / `register_dashboard_auth_provider` / `register_login_backend` / `register_secret_source`
  / `register_terminal_environment_provider` / `register_context_reference` 等（L495-874）。
- **平台**：`register_platform`（L585）等；平台插件**不得**改 `os.environ`，YAML 经
  `_shared.apply_yaml_bridge` 进 `PlatformConfig.extra`。
- **其他**：`register_system_prompt_section`、`register_locale(_dir)`、`register_session_env`、
  `register_redaction_patterns`、`register_approval_transport`、`register_auxiliary_task`、
  `get_config`。
- **事件桥**：`hermes_cli/plugin_events.py` 的 `broadcast_plugin_event(plugin_id, event, payload)`
  向桌面端全局事件流发 `plugin.<id>.<event>`（id/事件名正则校验，防止命名空间冒充）；
  `emit_plugin_session_event` 只发给某会话的观看者。这是插件向自己桌面半扇推送的唯一正门，
  禁止 import `tui_gateway.server` 私货。

## 6. 钩子系统

权威清单是 `hermes_cli/plugins.py:113` 的 `VALID_HOOKS`（同时是 shell-hook 的允许清单），
派发在 `hermes_cli/plugins_dispatch.py:275`（`invoke_hook` / `ainvoke_hook`，入口 facade 在
`hermes_cli/lifecycle.py:30`）。按功能分组：

- **工具面**：`pre_tool_call`、`post_tool_call`、`transform_tool_result`、`transform_terminal_output`。
  触发点 `model_tools.py`（L719、L889）。`pre_tool_call` 是策略钩子：**fail-closed**——超时/异常
  都产出 block 指令（`plugins_dispatch.py:303-323`）。
- **LLM 面**：`pre_llm_call`（可注入 context 或 `{"action":"block"}` 否决整个回合、零推理请求）、
  `post_llm_call`、`transform_llm_output`、`pre_api_request`/`post_api_request`/`api_request_error`、
  `transform_api_error_classification`（分类前运行，run-all-then-pick-first）。触发点在
  `agent/api_request_hooks.py`、turn 循环（`agent/turn_context.py`、`agent/conversation_loop.py`）。
- **辅助 LLM 调用**：`pre_auxiliary_call`/`post_auxiliary_call`（`agent/auxiliary_hooks.py`）——
  titling、压缩、MoA、vision、审批等，payload 同 `*_api_request` 形状 + `aux_task`；与回合级
  `*_api_request` 分离（#79733），观察者语义、fail-open。
- **流观察**：`on_stream_start/delta/end`、`on_interim_message`、`on_room_member_activity`
  （不可变 payload，不可改流）。
- **会话生命周期**：`on_session_start`/`on_session_end`/`on_session_finalize`/`on_session_reset`
  /`on_session_delete`/`on_session_open`、`agent_loop_stopped`、`post_compaction`。触发点：
  `agent/conversation_loop.py:933`、`hermes_state_common.py:447`、`gateway/run_shutdown_session_end.py` 等。
- **网关面**：`pre_gateway_dispatch`（可 skip/rewrite 入站消息；携带 live 对象，**仅 in-process**）、
  `post_gateway_admission`、`gateway_platform_event`（规范化信封，`gateway/run_adapters.py:1697`）。
- **审批/人工输入**：`pre_approval_request`、`post_approval_response`、`on_human_input_request/resolved`
  （观察者，否决走 `pre_tool_call`）。
- **验证/技能/子代理/斜杠命令**：`pre_verify`、`on_skill_lifecycle`、`subagent_start/stop`、
  `pre_command`（v1 忽略返回值；对 `/stop`、`/approve` 等逃生舱路径**故意不触发**）。
- **Kanban**：`kanban_task_claimed/completed/blocked`、`on_kanban_worker_spawned/exited/stale_claim`
  /`task_updated`/`dispatch_tick`（DB 提交后触发，观察者）。
- **其他**：`post_curator_run`。

派发语义（`plugins_dispatch.py:275-385`）：

- payload **只增不改**：`**kwargs` 回调拿全量，窄签名按签名内省只拿自己声明的字段。
- 每个回调**故障隔离**：异常/`SystemExit` 捕获上报（同一 (hook, callback, error) 只 WARNING 一次），
  不影响其他回调。
- 有界钩子在 `plugins.hook_callback_timeout` 下的 daemon worker 里跑，超时**弃 worker 不 join**；
  同回调抑制窗口 + 废弃 worker 上限防线程泄漏（#105223/#98382）；`pre_tool_call` 超时 fail-closed。
- shell hooks（`agent/shell_hooks.py`）与出站 webhook（`agent/outbound_webhooks.py`）挂在同一
  hook manager 上：config `hooks:` 把事件映射到 shell 脚本，复用 `VALID_HOOKS` 校验；
  `SHELL_UNSUPPORTED_HOOKS` 大声拒绝无指令通道的事件。

## 7. 插件目录（plugin catalog）：树外插件的唯一发现系统

- 一条目一 YAML：`plugin-catalog/<name>.yaml`，**40-hex SHA pin 强制**，人工 PR 合入
  （准入政策 `plugin-catalog/README.md`）。条目字段（`hermes_cli/plugin_catalog.py:88-113`）：
  `name/repo/sha/description/maintainer/tier/category/requires_hermes/subdir/docs_url/version/
  image/screenshots/readme/platforms/title/onboarding/capabilities/known_issues`；monorepo 用
  `repo#subdir` 作 `install_identifier`。校验在 `plugin_catalog.py:143-195`。
- `plugin-catalog/removed.yaml` 是**击杀名单**：一切安装路径（CLI/dashboard/TUI）拒绝命中项
  （repo URL 按 `host/owner/repo` 规范比对，`git@`/`ssh://`/`www.` 拼法都逃不掉）。仅 CLI 有
  `--allow-removed` 例外，会记进安装记录，且是唯一豁免后续 `update`/`enable`/load 时
  `gate_manifest` 同项检查的东西。
- 信任关系：catalog 溯源只认安装器所有的 `.install-metadata.json`（`catalog` 块 = 检出 commit）；
  树内 `.hermes-catalog.json` 只是给 Desktop「Install here」用的便利副本，**Python 侧永不信任**
  （仓库可伪造它）。
- 在线刷新：`hermes_cli/plugin_catalog.py` 从 docs 构建发布的 `/docs/api/plugin-catalog.json`
  活取，树内 YAML 兜底。裸名字解析经 catalog（`plugins_cmd_catalog.py:41-64`），否则报错——
  「永不新增第二个名字索引」。
- 策略：树外新插件只能以 catalog 条目或独立仓库（`~/.hermes/plugins/` / pip entry point）存在；
  内存提供方、第三方产品类插件已成批迁出 core（见 `plugins/AGENTS.md`「What may live in this tree」）。

## 8. 权限与安全边界

- **capability 声明 ≠ 授权**（§3）；工具覆盖需每插件 opt-in（§5）；MCP 调用默认拒绝（§5）。
- **公开 API 白名单** `hermes_cli/data/plugin_api.json`：记录插件可从 core import 的
  `module` / `module:Name`；`hermes plugins validate` 经 `plugin_validate_private_imports.py`
  对其余 core import 警告；`scripts/gen_plugin_api_docs.py` 从它渲染 `public-api.md`。
  白名单新增是**面向全体插件作者的公开承诺**，必须自带文档 + 行为测试。
- **boundary lint**（`scripts/boundary_lint.py`）：内置插件 import 非白名单 core 名、或 core import
  具体内置插件，超过提交的 baseline 即挂测试套件。`--update` 重写 baseline，只在有意跨界时用并在
  PR 里说明。内部 import 路径不是 API：PR #102117 的搬迁兼容层已移除，旧路径直接 `ImportError`
  （在 `hermes plugins list` 里显示为 load error）。
- **宿主隔离**（`plugins.isolation: host`，`hermes_cli/plugin_isolation.py`）：
  - `in_process`（默认）把用户插件 import 进主进程；`host` 把每个非 bundled 插件放进 per-profile
    宿主进程（父侧 `plugin_host.py`、子侧 `plugin_host_child.py`、帧协议 `plugin_host_wire.py`）。
  - 子进程拿到 `RemotePluginContext`，注册的工具/钩子/命令/提供方对象以 ABC 子类代理形式回到主进程。
  - **一切用户代码 import 路径都路由到宿主或拒绝**（general/memory/context/cron 的 `load_instance`、
    模型提供方 profile 的无凭证抽取 `plugin_host_profiles.py`、dashboard 的 ASGI 桥）；
    `plugin_loader.load_plugin_module` 拒绝其他任何合成命名空间 import。
  - 跨不过去的东西是**一张表**：`HOST_UNSUPPORTED_CTX_METHODS`（live gateway/adapter 对象）；
    运行时拒绝与静态审计（`plugin_isolation_audit.py`，`hermes plugins validate/show` 展示）同源。
  - 线上值按 ABC 注解还原（`plugin_host_wire.restore`）：core 消费方要类型检查的返回值必须是
    ABC 方法上注解的 dataclass，否则以 `Record`/`Opaque` 到达。
  - **host 不 spawn host**（`HERMES_PLUGIN_HOST_PROCESS=1` 强制 in-process）；隔离模式经 managed
    overlay 只读配置解析，`/etc/hermes/config.yaml` 压过 profile 自身配置——运维钉死时插件无法
    自行退出隔离。per-profile env 由 `served_profile_child_env` 提供，宿主只见自己 profile 的密钥。
- 其他校验器：`plugin_validate_locales.py`（语言包）、`plugin_validate_desktop.py`、
  `plugin_validate_core_override.py`（core 覆盖）、`plugin_validate.py`（总入口，`hermes plugins
  validate` / `plugin-doctor`）。

## 9. 安装、管理与更新

- CLI 命令族在 `hermes_cli/plugins_cmd*.py`：`install`（`plugins_cmd_install.py`）、`remove`、
  `enable`/`disable`（`plugins_cmd_toggle.py`，`cmd_enable` L728 / `cmd_disable` L785）、
  `update`/`update-all`、`list`、`search`/`info`/`validate`（`plugins_cmd_catalog.py`/`plugins_cmd_inspect.py`）、
  `plugin-doctor`（`plugins_cmd.py:1079`）。安装来源：catalog 名、git URL、本地目录、pip entry point。
- 状态与台账：`plugins_state.py`（enabled/disabled）、`plugins_ledger.py`（安装记录）、
  `plugins_provenance.py`、`plugins_transaction.py`（安装事务）。
- 登录/后端类插件只允许 bundled 或 catalog 中 verified/official 来源；非 catalog、未 pin、低 tier
  都会被拒并给原因（`plugins_cmd_catalog.py:206-246`）。
- 被动更新：`plugins_auto_update.py` —— unattended 更新是 **opt-in**（`plugins.auto_apply` 开关 +
  `unattended_gate` 门禁），`update_all_unattended` 批量应用、`catalog_pins_behind` 报告落后 pin。
- 「离开 core」的特性经 `hermes_cli/left_core_migration.py::LEFT_CORE` 行自动为老 home 安装
  catalog 插件（每 home 每行一次）；core 特例一律变成插件可设的 `PlatformEntry` 接缝，禁止名字
  判断。

## 10. 平台适配器插件

- 形态：`plugins/platforms/<name>/adapter.py`，`register(ctx)` 注册进 gateway。
- 注册项是 `gateway/platform_registry.py:47` 的 `PlatformEntry`：`trusted_inbound`（L121，豁免
  allowlist/配对，因为发件人是人，见 `gateway/authz_mixin.py:757`）、`display_tier`（L124，
  `gateway/display_config.py` 的显示默认层）、`display_overrides`、`shared_env_prefixes`（L131）。
  core 特判就是通过这些字段接缝消除的。
- 安全规则（token lock、scoped secret）见 `gateway/AGENTS.md`；`pre_gateway_dispatch` 因携带
  live gateway runner/session store 只能 in-process（`plugin_isolation.py:52-84`）。

## 11. 兼容性契约（行为契约，不是版本号）

没有单一 `PLUGIN_API_VERSION`；文档化的面**只增不改**：

1. hook payload 以 **keyword 字段**追加；回调按签名内省分发（老窄签名只收声明字段）。
2. `PluginContext` 方法永不删除/改名；新参数可选、有默认、尽量 keyword-only。
3. 未知 manifest 字段忽略；新 provider 方法带默认实现。
4. 本地 schema 版本只在有线协议/持久化契约的 capability 上存在，且旧状态必须保留或迁移。
5. 弃用：每进程一次警告 + 文档迁移说明 + ≥2 个次版本后才移除。
6. 兼容测试**通过真实发现路径加载冻结插件**断言行为，禁止数注册表/读源码/盯全局版本字面量。

## 12. 与本 fork 二次开发的交互注意点

- **补丁登记**：往内核加改动要进 `PRD.md` §5.4 登记表；插件系统的改动尽量落在
  配置/插件/客户端三层，别打 core 补丁。
- **boundary_lint baseline**：新增 `sync/`、`hermes_notes/` 等本地模块后，若内置插件 import 了
  baseline 外的名字会挂测试套件；有意跨界需说明并 `--update` 检查 diff。
- **被动更新补丁**：本 fork 默认关闭版本与插件被动更新检查（`hermes_cli/config_defaults.py`、
  `source_check.py`、`web_routers/actions.py`，PRD §5.4）；上游 `plugins_auto_update.py` /
  `plugins_updates.py` 可能变动，升级时逐条 rebase，保住 opt-in 语义与「禁用不冒充检查完成」。
- **版本串**：`0.3.25+kaltsit` 不得喂给上游 `STABLE_TAG_RE`（`hermes_cli/update_channel.py:67`）
  或 `scripts/releases/semver.py`；本 fork 不跑上游 release 流水线。
- **升级冲突高发区**：`agent/system_prompt.py` 拼装顺序、`VALID_HOOKS` 附近（`plugins.py:113`）、
  `plugins_manifest.py` 字段、`plugin-catalog/` 结构。合上游时先跑 `tests/plugins/`（真实发现
  路径 + 临时 `HERMES_HOME`）。
- **别忘了**：插件/钩子的 profile scope 约束（§4）同样约束本地新增的后台任务；测试用
  `scripts/run_tests.sh`，不写 `~/.hermes/`。

---

*调研日期：2026-11-25。行号随上游合入会漂移，以符号名与模块路径定位为准。*
