# T-04 笔记按更新时间保留回归

日期：2026-11-03。依据 PRD §7.3 / §7.4，清单超限时必须按 `updated` 倒序保留最新笔记。

`hermes_notes/store.py` 的 `_FIELD_RE` 原先只提取 `name` 与 `description`。
虽然扫描结果用 `entry.updated` 排序，所有条目的日期实际均为空，最终由文件路径决定保留项。
补入 `updated` 的提取后，沿用现有排序，无新增索引或依赖。

回归用两个文件让文件名排序与日期排序相反，清单只保留一条。
修复前保留 `zzz-old`，应保留的 `aaa-new` 被遗漏；修复后保留 `aaa-new`，仍明示遗漏一条。

通过项目规定的 runner 验证，未使用裸 pytest：

```powershell
$env:HERMES_PYTHON='D:/VSCodeProjects/hermes-agent/.venv/Scripts/python.exe'
& 'C:/Program Files/Git/bin/bash.exe' scripts/run_tests.sh tests/agent/test_notes_prompt.py -k keeps_newest -j 1 --file-retries 0
& 'C:/Program Files/Git/bin/bash.exe' scripts/run_tests.sh tests/agent/test_notes_prompt.py tests/agent/test_system_prompt.py tests/tools/test_memory_tool.py -j 2 --file-retries 0
```

首条修复前为 1 failed；修复后完整相关回归为 **137 passed，0 failed，4 skipped**。
跳过项属于当前 Windows 不适用的平台测试。固定格式日期来自 PRD；本轮未改日期格式或会话缓存规则。
