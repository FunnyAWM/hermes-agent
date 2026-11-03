# PC 改动验证记录

日期：2026-11-03。以下结果对应拉取前的本地工作区，基于 `19f122842c`；
拉取后的兼容性需另行验证，不能复用本记录作为新基座通过凭证。

| 检查 | 实际结果 |
| --- | --- |
| `.venv/Scripts/python.exe -X utf8 scripts/check` | 12 项检查通过；code health 0 blocking、0 advisory |
| `scripts/run_tests.sh tests/agent/test_notes_prompt.py tests/agent/test_system_prompt.py tests/tools/test_memory_tool.py -j 2 --file-retries 0` | 137 passed、0 failed、4 skipped（Windows 不适用的平台项） |
| `scripts/run_tests.sh tests/hermes_cli/test_dashboard_device_pairing.py tests/hermes_cli/test_passive_update_opt_out.py tests/hermes_cli/test_banner_release_channel.py -j 2 --file-retries 0` | 28 passed、0 failed |
| `scripts/verify_consolidation_chain.py` | 21 项数据交接检查通过；无模型或自动会话压缩调用 |
| `scripts/verify_memory_comparison.py` | 固定十题、来源事实与 PC 读取链路通过；无跨端模型答案比较 |
| `scripts/check_upstream_memory.py` | 当前解析与默认限额报告成功；未比较候选 tag |
| `node --check apps/desktop/electron-builder.fork.cjs`、`git diff --check` | 退出码 0 |

测试通过 Git Bash 的项目 runner 执行，`HERMES_PYTHON` 指向工作区 `.venv/Scripts/python.exe`。
没有使用裸 pytest，没有修改日常记忆、执行正式安装或发布。

门禁初次失败的问题已修复：验证脚本采用 BOM 兼容读取，上游检查脚本补子进程超时，
设备配对测试修正 import 写法，配置注释恢复行数约束。
历史记忆里的临时路径引用保留原文，并注明其历史性质。
