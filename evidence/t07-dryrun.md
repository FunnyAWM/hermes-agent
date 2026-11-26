# T-07 预检 dry-run（2026-11-06）

当日 `HEAD` 为 `7e2d80d6c4`，分支 `main`，工作区存在未提交改动（预检仅告警，不阻断）。

```text
$ .venv/Scripts/python.exe scripts/check_upstream_upgrade.py
current: 0.22.5+fork1 / 0.22.5-fork.1
FAIL upstream_remote: upstream=missing; origin=https://github.com/FunnyAWM/hermes-agent.git
PASS declarations: 0.22.5-fork.1 (0.22.5+fork1)
FAIL candidate_tag: no candidate stable tag passed (use --tag vX.Y.Z)
PASS checklist: ok
PASS memory_contract: aligned with evidence/fixtures/memory-contract.json
RESULT: blocked -> upstream_remote: ...; candidate_tag: ...
exit=1

$ .venv/Scripts/python.exe scripts/check_upstream_upgrade.py --tag v0.22.5-fork.1
FAIL candidate_tag: 'v0.22.5-fork.1' is not an upstream stable tag; fork/pre-release tags are rejected

$ .venv/Scripts/python.exe scripts/check_upstream_upgrade.py --tag v0.24.0
FAIL candidate_tag: 'v0.24.0' is not present locally; fetch is disabled in this preflight
```

结论：预检按设计阻断——无 upstream remote、无真实稳定候选 tag。本轮不配置远程、不 fetch，
T-07 保持完成受阻。解除步骤见 `evidence/t07.md`。
