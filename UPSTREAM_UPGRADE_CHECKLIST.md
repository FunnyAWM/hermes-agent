# 上游稳定版本升级 Checklist

本清单对应 `AGENTS_PROJECT.md` §2、`PRD.md` §5.4 与 `TODO.md` T-07/T-13。只跟随上游稳定 `vX.Y.Z` Release tag；pre-release / RC 不进入升级流程。失败时保留当前固定基座继续开发。

## 1. 准入与分支

- [ ] 确认当前 `HEAD`、桌面版本 `apps/desktop/package.json`、Python 版本 `pyproject.toml` 和工作区状态；记录完整提交 SHA。
- [ ] 确认 `upstream` 指向 `https://github.com/nousresearch/hermes-agent.git`；若未配置，按仓库维护者流程配置，不从 fork 的 `origin` 推断上游。
- [ ] 获取 tags 后确认候选 tag 为稳定 `vX.Y.Z`（不含 `-rc`、`-beta`、`-alpha` 或其他预发布后缀），并记录 tag 对应完整 SHA。
- [ ] 从已固定基座创建 `sync/vX.Y.Z`，禁止直接在 `main` 上合并。

## 2. Rebase 与定制审查

- [ ] 对照 `PRD.md` §5.4 受控补丁登记表逐项 rebase；冲突逐处人工解决并运行对应行为测试，不以“取其一”处理。
- [ ] 保留 `AGENTS_PYTHON.md`、`AGENTS_FRONTEND.md`、`AGENTS_PROJECT.md`、`PRD.md`、`TODO.md`、`.workbuddy/` 等本地私有路径，以及 `AGENTS.md` 的本地 Working constraints。
- [ ] 检查上游是否已实现本地补丁；若需求已满足或接口变化令补丁失效，删除本地补丁及对应死代码，并更新登记表。
- [ ] 检查并按新基座更新 Python `pyproject.toml` 与桌面 `apps/desktop/package.json` 版本：`上游版本-fork.<N>` / PEP 440 `上游版本+forkN`；换基座时 N=1。同一基座重新发布时 N 递增。fork 版本不得传给上游 release/tag 校验器。

## 3. 记忆兼容性（T-13）

运行 `python scripts/check_upstream_memory.py --tag vX.Y.Z`，并检查上游候选 diff：

- [ ] `MEMORY.md`、`USER.md` 仍是 UTF-8 Markdown 纯文本，PRD 上限仍为 2,200 / 1,375 字符。
- [ ] 字符计数口径未变：只计记忆条目，不把 system-prompt 横幅、标题或百分比计入。
- [ ] 条目分隔格式仍可解析；当前格式是完整分隔符 `\n§\n`，不是裸 `§`。
- [ ] 超限写入仍拒绝，返回剩余可用字符数，且不截断/改写原文件。
- [ ] 若上游改变格式、字符口径或拒绝语义：停止升级，先评估 PC/Android 两端兼容性、固定样本和迁移/回滚方案；不得只更新版本号。
- [ ] 保存该次检查输出、tag/SHA、审查人/日期及结论到 `evidence/t13-<tag>.md`。

## 4. 门禁与回归

- [ ] `python scripts/check`
- [ ] `scripts/run_tests.sh`
- [ ] 按本仓定制运行相关行为测试；至少覆盖记忆读写、桌面连接、打包配置及 TODO 中仍适用的回归。
- [ ] 完成 T-12 所需的 Windows NSIS 覆盖安装检查（若候选改动涉及安装或数据路径），确认 `HERMES_HOME/memories/` 与 `HERMES_HOME/notes/` 保留。
- [ ] 所有门禁通过后才合入 `main`；记录命令、结果、tag/SHA 与 evidence 路径。若受阻，留在 sync 分支并在 TODO 标注具体阻塞。
