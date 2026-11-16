# AGENTS_PROJECT.md · hermes-agent（二次开发分支）

> 基线：`AGENTS_PYTHON.md` v1.0
> 基线：`AGENTS_FRONTEND.md` v1.0（仅 `apps/`、`ui-tui/`、`web/`）

本文件只写项目特定约束；基线已有的规则不在此重复。

---

## 1. 仓库定位

本仓库是 `nousresearch/hermes-agent` 的**二次开发分支**，不是向上游提交改动的贡献工作区。上游
`AGENTS.md` 里有两类内容必须分开看：

- **上游政策** — Contribution rubric、triage sweeper 规则、PR / issue / review / salvage 流程。
  它们描述「怎样把改动合进上游」，**不约束本仓库的日常开发**；仅在上游回贡时作参考。
- **代码事实** — facade + siblings 布局、`scripts/check` 门禁、`scripts/run_tests.sh`、路由表、
  per-unit 尺寸棘轮。这些是代码库现状而非政策，**继续成立**，改动前照读。

冲突时的权威顺序：**本文件 > 两份基线 > 上游 `AGENTS.md` 的政策部分**。上游 `AGENTS.md` 的代码事实部分
不属于冲突项——它是描述，不是规则。

## 2. 上游同步

**基座已钉死**：

| 项 | 值 |
| --- | --- |
| 远程 | `origin` = `https://github.com/FunnyAWM/hermes-agent.git`（个人 fork，非上游 `nousresearch/hermes-agent`） |
| 分支 | `main`，跟踪 `origin/main` |
| 基座提交 | 上游 v0.23.2 Release 提交（本机无该 tag，待 `git ls-remote --tags` 核对；上一基座 v0.23.0 = `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`） |
| 对应版本 | **v0.23.2 Release**（分支 `patch/v0.23.2`；本地只有 `v0.23.0` 及更早 tag） |
| 历史状态 | 2026-11-06 `origin/main` 由 GitHub「Discard commits」硬回退到上游 `90d6b98fd8`；本地 `main` 随后 `reset --hard` 到 v0.23.0，fork 改动移到 `patches-v0.23.0` 分支 |

⚠️ **上游 tag 列表尚未机器核对**：打 `v0.23.0` 时 GitHub 经代理不可达，`git ls-remote --tags` 取不到上游
tag 清单，「`2bd378e` = 上游 v0.23.0 Release」按使用者核对记录。网络恢复后核验一次：
`git ls-remote --tags https://github.com/NousResearch/hermes-agent.git 'refs/tags/v0.23.0*'`。

**同步节奏（2026-10-28 定）：跟随上游稳定 Release tag。**

| 项 | 口径 |
| --- | --- |
| 触发 | 上游发布新的**稳定** tag（`vX.Y.Z`）。pre-release / RC 一律不跟 |
| 流程 | `git fetch upstream --tags` → 建 `sync/vX.Y.Z` 分支 → 合入 → 跑 `python scripts/check` + `scripts/run_tests.sh` + 本仓回归（T-12 / T-13）→ 全绿才进 `main` |
| 冲突 | 下列本地私有路径保留本地版；内核补丁按 `PRD.md` §5.4 登记表逐条 rebase，不允许「取其一」 |
| 失败 | 停在当前基座继续开发，不阻塞 |

**版本号（2026-11-16 改，独立版本线）**：当前桌面 `apps/desktop/package.json` = `0.3.25-lungo`，
Python `pyproject.toml` = `0.3.25+lungo`（PEP 440 本地版本写法，同义不同串）。
原「上游版本-fork.<N>」方案（2026-10-28 定）停用：版本号不再随上游发版变化，
两处 `version` 只在 fork 自己发版时重写。

⚠️ 两条边界：`-lungo` 在 SemVer 里是 pre-release，排序低于 `0.3.25`；且本仓 `hermes_cli/update_channel.py:67`
的 `STABLE_TAG_RE` 只认 `vX.Y.Z`——这个版本串**不得**传给 `scripts/releases/*` 或更新通道的校验器。
细节见 `PRD.md` §5.4。

⚠️ **上游 remote 尚未配置**：现在只有 `origin`（fork），仓里也没有任何上游 tag。要执行这条策略，先加一次：
`git remote add upstream https://github.com/nousresearch/hermes-agent.git`，之后 `git fetch upstream --tags` 才取得到 Release tag。
在此之前默认**不同步**——基座是冻结的，所有开发在基座之上向前走。

- 本地私有路径（合并上游时保留本地版本，不随上游覆盖）：`AGENTS_PYTHON.md`、`AGENTS_FRONTEND.md`、
  `AGENTS_PROJECT.md`、`PRD.md`、`TODO.md`、`IMPLEMENTATION_ROADMAP.md`、`UPSTREAM_UPGRADE_CHECKLIST.md`、`evidence/`、`.workbuddy/`，
  以及根 `AGENTS.md` 的 `## Working constraints` 节。
- 上游改到 `AGENTS*.md` 时：保留本文件的 §4 例外表与本地私有路径，其余可接受上游版本。

### 2.1 历史重写的安全网

覆盖前的状态保留在两个**仅本地、未推送**的引用里，需要回滚时直接从它们恢复：

```
backup/pre-reset-20261028   → 3f058ad593
backup-3f058ad-20261028     → 3f058ad593
```

确认基座无误后可删除：`git branch -D backup/pre-reset-20261028 && git tag -d backup-3f058ad-20261028`。

## 3. 工具链事实（基线的通用门禁不适用）

基线按通用 Python 项目写质量门禁（mypy + pylint + pytest 三件套）。本仓库不是那套：

| 项 | 本仓库实际 |
| --- | --- |
| 提交前门禁 | `python scripts/check`（ruff、code-health 棘轮、各类 guard） |
| 测试 | `scripts/run_tests.sh`——**禁止裸 `pytest`** |
| 类型检查 | `ty`（`[tool.ty.*]`）；无 `[tool.mypy]`，**不引入 mypy** |
| Lint | `ruff`（`[tool.ruff.*]`）；无 `.pylintrc`，**不引入 pylint** |
| 依赖与锁 | `hermes pm lock` + `uv.lock`；禁止用裸 pip/uv 改环境 |
| 提交信息 | 英文 `<type>(<scope>): <subject>`，沿用现有历史风格 |
| 根 `AGENTS.md` 体积 | `scripts/ci/check_agents_md_size.py` 卡 12,000 字符、链路 30,000 |

## 4. 对基线的受控例外

按基线 §0.3 登记于此，**不修改基线正文**。新增例外只改这张表。

| 基线条目 | 本仓库做法 | 理由 |
| --- | --- | --- |
| `AGENTS_PYTHON.md` §10 门禁 | `scripts/check` + `run_tests.sh` + `ty`/`ruff`，不用 mypy/pylint/pytest | 既有工具链，迁移成本远大于收益 |
| `AGENTS_PYTHON.md` §5 提交信息用中文 | 用英文 | 与上游历史一致，便于回贡与 diff 阅读 |
| `AGENTS_PYTHON.md` §2.2–2.3 强制 `TODO.md` / `MEMORY.md` | 记忆走 `plugins/memory/` 与 `.workbuddy/memory/` | 项目已有记忆机制，不另建并行一套 |
| `AGENTS_PYTHON.md` §13 caveman 输出风格 | 不启用 | 需要时由用户显式触发，不默认生效 |
| `AGENTS_FRONTEND.md` §10 工具门禁 | 以 `eslint.config.shared.mjs` + `scripts/check` 为准 | 同上 |
| `AGENTS_PYTHON.md` §5 force push 限制（仅当仓库只有 1 次提交且不含业务代码时允许） | 允许 `--force-with-lease` 重写 `main` | fork 的基座必须能钉到某个上游 release；2026-10-28 已依此覆盖一次（`3f058ad593` → `bf0203a794`），全程保留本地备份 ref（§2.1） |

**范围区分**：本表只登记「基线条款与仓库做法不一致」的例外。**产品层的内核补丁不在这里登记**，
以 `PRD.md` §5.4 的受控补丁登记表为准（该表记录改哪个文件、对应哪条需求、升级时的冲突风险）。

## 5. 领域红线

_部分待填。_ 二次开发的产品方向、领域规则、外部系统与第三方接口约定、部署与交付形态、团队约定。

按基线 §0.3：**本节未写的内容视为无项目约束，不得凭空假设**。遇到需要按业务判断的地方停下询问，
不要用「合理默认」代替决策。

### 5.1 发行口径（2026-11-13 使用者决定）

- **本仓产物由 fork 自己的打包链交付，不走上游 PR / GitHub Actions 作为发行门禁。**
  发行判定 = `python scripts/check` + `tsc --noEmit` + 本仓回归测试 + `build.ps1` 打包冒烟；
  上游 `ci.yaml` 的聚合门与本仓发行无关。
- 上游工作流（`ci.yaml`、`tests-os.yml` 等）留作参考与本地等价命令的来源，不推 PR 时不会运行；
  其中的平台专属用例（Windows umask / symlink、Linux-only 断言、真实 mDNS 等）**不作发行判据**。
- `tests/ci/test_prompt_surface.py` 的快照保持现状：它是上游的提示词评审工具，本仓不在发行路径上
  执行它；需要评审提示词改动时再在可完整下载依赖的环境重渲染，不因该用例红而阻断发行。
