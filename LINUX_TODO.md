# Linux 验收待办

Windows 本机无法覆盖的项留在这里；完成后勾选并记录命令与结果。

- [ ] 运行 `scripts/run_tests.sh tests/gateway/test_cli_plugin_command_in_gateway.py`，确认 Windows 上跳过的 6 项 Linux 专用用例在 Linux 通过，记录发行版、内核与通过数。
- [ ] 重新跑 `scripts/run_tests.sh tests/plugins/super_agent/ tests/gateway/test_post_gateway_admission.py tests/gateway/test_plugin_post_message.py tests/gateway/test_managed_worker_session_boundaries.py tests/contracts/conversation/test_message_verbs_contract.py tests/hermes_cli/test_web_server_messaging_profiles.py tests/hermes_cli/test_plugin_validate_private_imports.py tests/tui_gateway/test_plugin_command_profile_scope.py`，记录与 Windows 基线的差异。
- [ ] 用 `vendor/sandctl/sandctl doctor` 记录 `l1_linux`、`l2_gvisor`、`l3_kata` 的可用性；Windows 只有 `l1_windows` 两档。
- [ ] 在真实 Worker 任务上分别验证 `read-only`、`workspace-write`、`workspace-exec` 的边界；记录每种后端下越界访问被拒绝的证据。
- [ ] 用可控长任务触发 `turn_timeout_seconds`，确认远端取消回执、并发槽释放顺序与下一代任务领取。
- [ ] 记录仍依赖外部条件的项目（真实 IM、gVisor/Kata 内核、网络策略），并标注未验证边界。
