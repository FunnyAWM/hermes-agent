# 拉取后验证记录

日期：2026-11-03。基座为 `f5e0dd8e9b`，本地提交重放后 HEAD 为 `bb01dda886`。

- `origin/main` 已完成 rebase，记忆写入接口采用上游统一的 `_add_overflow` 路径。
- `pyproject.toml` 已将 `hermes_notes` 纳入 setuptools 包发现白名单。
- 隔离 `HERMES_HOME` 直接验证超限拒绝：已有 4/12 字符时返回 `8 chars remain available`，文件字节与条目均未变化。
- `tests/tools/test_memory_capacity.py` 已加入 add/patch、普通调用/operations 批量调用及原子性回归。
- 受仓库 Windows 写入保护影响，`scripts/run_tests.sh` 与 pytest 的临时目录创建无法启动；未将此环境错误计为代码失败。
- 新 PM 环境构建被镜像缺少发布时间元数据阻塞，未修改 `uv.lock`。
