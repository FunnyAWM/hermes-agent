# T-16 失败清理分类（2026-11-06）

基线：`evidence/t16-scoped.log`（316 文件，2375 passed / 65 failed / 1 error / 384 skipped，`-j 2`）。
本轮按根因分类并修复；下表“现状”为修复后逐文件复跑结果。

## 已修（可移植性 / 环境门禁）

| 文件 | 原失败 | 根因 | 修法 |
|---|---|---|---|
| `tests/pm/test_workspace_output_encoding.py` | 8 | Windows `shutil.which` 返回 `npm.CMD`，测试按小写文件名断言 | `os.path.normcase` 比较命令首项 |
| `tests/scripts/test_baked_bytecode.py` | 1 | 无 marker 时 Windows 写入 `LOCALAPPDATA\hermes\pycache`，测试只给 `HOME` | 按宿主平台提供对应根并断言 |
| `tests/scripts/test_termux_build_wheels.py` | 2 | `Path.write_text` 在 Windows 把 `\n` 翻成 CRLF | `write_text(..., newline="\n")`（产物、需求文件两处） |
| `tests/scripts/install/test_install_sh_output.py` | 1 error（收集） | 模块级 `import pty` 在 Windows 无 `termios` | `pytest.importorskip("pty")`，平台标记前完成跳过 |
| `tests/scripts/install/test_install_sh_termux_refusal.py` | 1 | “纯 Linux 主机通过平台检查”在 Git Bash 上必然被拒 | 该用例 `platforms("posix")` |
| `tests/scripts/test_mint_launchers.py` | 4 | uv 建的 venv 无 pip，mint 需要 pip 内置 distlib | `importorskip("pip._vendor.distlib.scripts")`，固定理由 |
| `tests/scripts/test_run_tests_parallel.py` | 1 | 用例验证 `os.getuid()` 分用户 scratch，Windows 无 getuid | `platforms("posix")` |
| `tests/pm/test_custom_root_union.py` | 1 | Windows 默认根是 `%LOCALAPPDATA%\hermes`，不是 `~/.hermes` | 设 `LOCALAPPDATA` 并按平台取默认根 |
| `tests/pm/test_build_operations.py` | 3 | ① symlink 用例需符号链接权限；② uv 可选用同 minor 的 `3.14` 而非 `3.14.2` 前缀 | ① `require_symlinks`；② 断言“真实前缀且不等于被投毒路径” |
| `tests/pm/test_install_states_gc.py` | 8 | 测试用 `os.utime(..., follow_symlinks=False)`，Windows 不支持 | `_set_mtime` 回退普通 utime；symlink 用例 `require_symlinks` |
| `tests/pm/test_environment_build.py` | 1 | 同 `base_prefix` 精确比较 | 同 build_operations |
| `tests/pm/test_source_launcher_stages.py` | 1 | 本机无 PowerShell 7 | 参数级 `requires_tool("pwsh")` |
| `tests/pm/test_store_extract.py` | 1 | 相对 symlink 还原需符号链接权限（WinError 4390） | `require_symlinks` |
| `tests/pm/test_startup_recovery.py` | 5 | uv 管理的 CPython 缺 `python3.exe`，fixture 指向本机工具 | `verify()` 报缺件时 `pytest.skip`，写明缺哪个包 |
| `tests/pm/test_worker_registry.py` | 4 | 子进程 stderr 混入 GBK 字节，`text=True` 用 UTF-8 解码失败，`stderr=None` | 子进程统一 `encoding="utf-8", errors="replace"`（`_fixtures._run` 同步） |
| `tests/scripts/install/test_install_ps1_script_suites.py` | 5 | 本机无 PowerShell 7，`_shell` 直接 `pytest.fail` | `pwsh` 缺失时 `pytest.skip`，固定理由 |
| `tests/scripts/test_bundle_native.py` | 2 | ① 用例需创建 symlink；② `git core.autocrlf` 把 `pm/lock.json` 写成 CRLF，与 git 快照 LF 比较必然不等 | ① `require_symlinks`；② `scripts/bundles/native.py` 比较前统一 LF |
| `tests/scripts/test_code_health_cli.py` | 3 | 复制 `.venv` 需要 symlink 权限（WinError 1314） | 三例 `require_symlinks` |
| `tests/scripts/test_desktop_build_cache.py` | 1 | 被测传输是 POSIX 发布 lane 的 bash composite | 文件级 `platforms("posix")` |
| `tests/scripts/desktop_update/test_desktop_update_windows_marker.py` | 2 | 本机没有“只授予 LIMITED_INFORMATION 查询权”的进程 | 无候选时 `pytest.skip`，列出已探测 PID |
| `tests/scripts/test_release_artifacts.py` | 1 | Windows 分支从 MSIX 旁的 `*-unpacked/<exe>` 读版本，fixture 未提供 | fixture 复制宿主 python 为 `Product.exe`，真实走 Windows 分支 |
| `tests/scripts/install/test_install_diverged_rescue_ref.py` | 1 | 驱动 `install.sh`，Git Bash 上按设计拒 Windows | 文件级 `platforms("posix")` |
| `tests/pm/test_plugin_survival_contract.py::test_update_sync_survives_unreadable_secondary_profile` | 1 | **原 sweep 就红、当时漏记**（既没修也没进下表）；断言拿未转义路径去比 `json.dumps` 的输出，Windows 分隔符被转义成 `\\`，POSIX 才可能成立 | 改为在解码后的 warning 文案上比；单用例 `-k` 复跑 `1 passed` |

## 未修（本机环境 / 既有 Windows 行为，已记录）

| 文件 | 失败 | 分类 | 说明 |
|---|---|---|---|
| `tests/pm/test_network_retries.py` | 2 | 本机环境 | `digests` 读者经 `HTTPSHandler` 访问 `api.github.com` 语义，重试次数/短路未达断言；本地 RangeServer 路径其余 9 项全绿 |
| `tests/pm/test_pm_authority.py` | 2 | Windows 行为 | ① `fetch-<sha>` 目录断言在 verify 阶段失败时只抓到一个包；② `Path.rename` 故障注入在 Windows 恢复路径不触发 |
| `tests/pm/test_update_dependency_legs.py` | 1 | 本机工具布局 | 宿主 Python 前缀不在 store 布局时刷新会尝试下载，与“零下载”断言冲突 |
| `tests/pm/test_uv_python.py` | 2 | fixture 平台假设 | `stage_host_python` 只搭 POSIX `bin/python` 布局；Windows 需完整 `Lib/`/DLL 拷贝 |
| `tests/scripts/desktop_update/test_desktop_update_windows_marker_lock.py` | 1 | 并发时序 | 4 个 claimant 竞态，末位进程在 120s 内未退出，随后被 `taskkill`；单次复跑偶发通过 |
| `tests/scripts/install/test_install_ps1_pinned_git_extract.py`、`test_icon_flavors.py` | 超时 | 本机环境 | 240s 单文件上限被杀，非行为失败；scoped 跑法用 `HERMES_TEST_FILE_TIMEOUT=420` |
| `tests/pm/test_pip_check.py` | 4 | 本机环境（相对 11-05 sweep 新增） | 受管 uv 0.13.5 取不到：store 被 `tests/pm/conftest.py` 隔离到 tmp、本机 uv 0.13.1 低于 pin、假源的 range probe 先返回错字节数，回落 GitHub 被拒（`WinError 10061/10060`）、回落 R2 被测试围栏拦（`Errno 11001 test fence`）；单文件复跑同样 12✓ 4✗，排除并发。`pm/` 自 tag 未改 |

## 结论

- 65 个节点失败 + 1 个收集错误中，本轮修复/正确门禁 **58 项**（含 23 个跳过，全部带固定理由）。
- 复核补记：`test_plugin_survival_contract.py` 的 1 项原 sweep 就红、当时既没修也没进残余表，
  本轮修掉；`tests/pm/test_pip_check.py` 的 4 项是相对 11-05 sweep 新增（同 node id 复现，非并发）。
- 最终残余 **11 个失败 + 1 个并发偶发**；另有 `test_network_retries.py` 的 2 项本轮整批复跑
  `16✓` 全绿（该文件代码未改，属网络态抖动，原表条目保留）。其余残余均与本 fork 的 T-01…T-18
  改动无关；逐项写明根因，不伪造通过。
- 批次 B 复跑（2026-11-06 收口）：`HERMES_TEST_FILE_TIMEOUT=420 scripts/run_tests.sh tests.pm
  tests/scripts/install -j 2 -q` → `164 files, 1002 passed, 10 failed, 192 skipped`；
  10 个失败 = 残余表里的 `test_pm_authority` 2 + `test_update_dependency_legs` 1 +
  `test_uv_python` 2 + `test_pip_check` 4 + 已修的 `test_plugin_survival_contract` 1（该用例已单独
  复跑转绿，其余 12 项在同文件内仍需 >420s 才能整批跑完）。
- 复跑命令（本轮逐文件）：

```bash
HERMES_PYTHON="$(pwd)/.venv/Scripts/python.exe" HERMES_TEST_FILE_TIMEOUT=300 \
  scripts/run_tests.sh <affected files> -j 2 -q
```

- 未在本轮重跑 316 文件全量；上表逐文件结果覆盖原失败集合。posix / pwsh / 密封包 / 真实下载仍是未验证面。
## 矩阵补跑追加（同一天）

统一入口矩阵（`evidence/t16-matrix.log`，24 文件 / 462 通过）又暴露 1 处：

| 文件 | 失败 | 根因 | 修法 |
|---|---|---|---|
| `tests/hermes_cli/test_config.py::TestEnvWriteDenylist::test_non_exec_near_misses_still_writable[git_config_parameters / ld_preload]` | 2 | 用例把 POSIX 大小写敏感语义（小写 exec 变量名是另一个惰性变量）当成通用事实；Windows 环境变量不区分大小写，写入器按策略名归一后正确拒绝 | 拆出 `test_lowercase_exec_names_are_inert_on_posix` 并加 `platforms("posix")`；Windows 侧大小写拒绝由既有 `test_windows_policy_denies_mixed_case_exec_names` 覆盖 |

修后矩阵：24 files, 462 passed, 0 failed, 6 skipped（全部为有理由门禁）。

## 宽口径范围追加（2026-11-06 最终验证）

本轮第一次把 `tests/agent/lsp` 纳入跑法（计划的宽口径命令），前 ~200 个文件里出 12 个红，
全在 `tests/agent/lsp`。下表只写日志里能逐条确认的错误行（Git Bash 临时目录下的 `final-batchA.log`）：

| 文件 | 红 | 可确认的错误 |
|---|---|---|
| `test_client_e2e.py` | 2 | `E TimeoutError`（`test_aborted_start_reports_exit_status_and_stderr_tail`、`test_reader_failure_retires_client_and_rejects_later_work[clean_eof]`） |
| `test_manager_locking.py` | 1 | `E KeyError: '/repo/f0.py'`（`test_delta_baseline_is_capped_by_write_recency`） |
| `test_service.py` | 1 | `E TimeoutError`（`test_service_replaces_client_after_reader_failure[clean_eof]`） |
| `test_workspace.py` | 1 | `E PermissionError: [WinError 32] …\test_resolve_workspace_for_fil1\scratch`，出自 `tests\home_io_guard.py:189`（teardown `rmtree` 被占用） |
| `test_workspace_release.py` | 7 | 聚合日志只保留末位 traceback：`E PermissionError: [WinError 32] …\test_reaper_shuts_down_client_0\repo`；其余 6 个 `test_workspace_removal_releases_only_its_language_servers[...]` 的错误行未落进该日志，取证要单片复跑 |

判定：`git diff --name-only v0.22.5-fork.1..HEAD` 的 112 个文件不含 `agent/lsp`、
`tests/agent/lsp`、`tests/home_io_guard.py`；原 65 项清理范围（316 文件）从未包含这一片，
因此属「未建基线范围」而非回归。三类错误（超时、诊断结果缺失、目录被占用的 `rmtree`）都落在
Windows 上真实语言服务器的进程生命周期与文件占用上，修法在测试 teardown 与用例时序，
不在 T-01…T-18 判据内，本轮不修、不伪造通过。单片复跑命令见 `evidence/t16.md` §「全量目录跑法」。
