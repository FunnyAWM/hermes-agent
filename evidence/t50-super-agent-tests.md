# T-50 Super Agent 定向回归

2026-11-26 在 Windows 上验证了 `patch/v0.23.2` 的工作区改动，基线提交为 `817bc90e79`。

| 验证入口 | 结果 |
| --- | --- |
| `scripts/run_tests.sh`，下方 12 个文件 | 122 项通过 |
| Desktop Vitest，5 个设置相关文件 | 42 项通过 |
| Desktop `tsc -p . --noEmit` | 通过 |
| `scripts/check --base HEAD` | 17 项检查中 16 项通过。代码健康检查覆盖 41 个文件，0 项阻断、0 项提示。全仓 Windows 编码检查仍报告 `agent/notes_organizer.py:117` 的既有问题 |

按分支默认基线运行 `scripts/check` 时，还报告了 `agent/turn_finalizer.py:640` 的复杂度和 `hermes_cli/bang_shell.py:91` 的异常处理问题。这两个文件与当前 HEAD 一致。

订阅回归先在原实现上失败。通过命令启用 observe 后，同一会话发来的消息没有审计记录。修复统一了 Gateway 命令与入站 hook 的会话键。用例验证 observe 只记录、auto 投递到原 Ticket、其他会话保持隔离，以及取消订阅后停止投递。旧版以 `slash` 保存的记录没有 Gateway 会话键，要在原会话重新设置订阅和 observe 模式。

其他新增断言覆盖消息来源过滤热更新、1 至 8 个 Worker 槽位满载与补位、创建和恢复 Worker 的模型参数、带 BOM 的中文长文本读回，以及独立插件设置页在 Plugins 分组折叠后的访问和保存。

## 复现

从仓库根目录使用已配置的测试解释器运行

```bash
scripts/run_tests.sh -j 4 tests/plugins/super_agent/ \
  tests/gateway/test_managed_worker_session_boundaries.py tests/gateway/test_plugin_post_message.py \
  tests/hermes_cli/test_gateway_client.py tests/tui_gateway/test_session_contracts.py \
  tests/tui_gateway/test_plugins_manage_settings.py tests/tools/test_local_command_sandbox.py \
  tests/tools/test_terminal_sandbox_builder_dispatch.py
```

从 `apps/desktop` 运行

```bash
pnpm exec vitest run --project ui src/app/settings/plugin-settings.test.tsx \
  src/app/settings/plugin-settings-ownership.test.tsx src/app/settings/plugin-settings-form.test.tsx \
  src/contrib/settings-pages.test.ts src/store/agent-plugins.test.ts
pnpm exec tsc -p . --noEmit
```

订阅和调度测试使用临时 SQLite 数据库。Worker RPC 使用测试替身，桌面表单在测试渲染器中运行。完整 Worker 模型调用、真实微信 OpenClaw 收发和全仓测试套件仍待验证。

## IM 自动路由与中文设置回归（2026-11-26）

在临时配置档加载真实插件后，将一条无前缀的 WhatsApp 文本送入 Gateway。断言 Router 创建一个工单，渠道自有 Agent 的消息处理函数调用次数为零。其他用例覆盖来源限制、消息 ID 重投、无 ID 的相同正文、订阅、观察模式和中文回复。

桌面组件测试从指定配置档获取模型目录，验证旧模型可见，切换提供商会清空旧模型，保存时提交新提供商和选中的模型。本轮通过 137 项 Python 测试、88 项桌面组件测试、6 项网页渠道测试和 15 项桌面产品名称测试。真实 IM 与手机扫码仍待联调。
