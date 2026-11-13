# T-19 · headless 桌面变体（2026-11-06）

目标：两种发行形态——**完整版**（`bundled`，运行时随包）与 **headless 版**（不带运行时，
优先用本机环境，找不到就明确报错，**永不进入 bootstrap 安装**）。

## 实现

| 文件 | 改动 |
|---|---|
| `apps/desktop/electron/install-stamp.ts` | `ArtifactKind` 增 `'headless'`；`InstallStamp` 增可选 `localRuntimeRoot` |
| `apps/desktop/electron/headless-runtime.ts`（新） | `headlessRuntime(stamp)` 只对 headless 生效并解析烘焙根；`HEADLESS_RUNTIME_MISSING` 文案 |
| `apps/desktop/electron/main.ts` | 解析阶梯插入 1b 级（env 之后、dev checkout 之前）；最后一级前对 headless 抛硬错误；About 的 Runtime 行按 `external/source` 分类 |
| `apps/desktop/electron/headless-runtime.ts`（收口重构） | 阶梯 1–1b 抽到 `explicitSourceBackend()`，末级硬停抽到 `refuseBootstrap()`，Runtime 行抽到 `headlessRuntimeRoot()` |
| `apps/desktop/electron/source-backend.ts`（收口重构） | `isHermesSourceRoot()` 与 `sourceBackendAt()` 从 `main.ts` 迁入，`main.ts` 回到 18220 行、`resolveHermesBackend` CC 回收 |
| `apps/desktop/scripts/write-build-stamp.mjs` | variant 白名单增 `headless`（`updateMechanism: external`）；`HERMES_DESKTOP_LOCAL_ROOT` 烘焙进 `localRuntimeRoot`（必须绝对路径） |
| `apps/desktop/scripts/bundle-electron-main.mjs` | 烘焙 stamp 时接受 `headless` |
| `apps/desktop/product-identity.cjs` | 身份 `Hermes Headless` / `hermes-headless` / `HermesHeadless`，与 `Hermes`、`Hermes Agent`、`Hermes Light` 互不重叠 |
| `apps/desktop/electron/app-version.ts`、`local-runtime-version.ts` | headless 与 bootstrap 同口径：版本取自本机运行时 |

解析顺序（headless）：`HERMES_DESKTOP_HERMES_ROOT`（显式 env）→ 烘焙的 `localRuntimeRoot` →
`HERMES_DESKTOP_HERMES` → `%LOCALAPPDATA%\hermes\hermes-agent` → 已发布 launcher → **报错**。
`light` 保持上游语义（无运行时、仅远程），未改动。

## 测试

```
npx vitest run --project electron electron/source-backend.test.ts electron/headless-runtime.test.ts \
  electron/payload-backend.test.ts electron/bootstrap-state.test.ts \
  electron/product-identity.test.ts electron/app-version.test.ts \
  electron/local-runtime-version.test.ts scripts/write-build-stamp.test.mjs
→ 8 files, 54 passed, 2 skipped
npx tsc --noEmit -p apps/desktop                   → 0 errors
npx eslint <上述改动文件> --max-warnings 0          → 0 problems
python scripts/check                                → 14 checks ok（health 0 blocking）
```

新增用例覆盖：非 headless 的 stamp 一律返回 null；烘焙根被解析；空白记录不回落 cwd；
缺运行时文案包含两条出路且不提 bootstrap；stamp 的 `payload/updateMechanism/localRuntimeRoot`
三字段；`localRuntimeRoot` 只被 headless 消费、相对路径报错；身份矩阵新增 headless 行；
env 覆盖优先于烘焙根；覆盖目标不是 checkout 时回落到烘焙根；烘焙根失效/无可用 Python 时
记录日志并继续下一级；`refuseBootstrap` 只对 headless 硬停；`headlessRuntimeRoot` 的回落条件。

## 产物（`apps/desktop/release-headless/`，2026-11-06 17:03 重建）

构建（干净工作树 `0d1be39e66`）：

```powershell
$env:HERMES_DESKTOP_VARIANT = "headless"
$env:HERMES_DESKTOP_LOCAL_ROOT = "D:\VSCodeProjects\hermes-agent"
npm run build
npx electron-builder --config electron-builder.fork.cjs --win nsis portable --x64 --publish never `
  -c.directories.output=release-headless `
  -c.electronDist="C:\Users\11429\AppData\Local\electron\Cache\0a74c7b0...\electron-v44.9.3-win32-x64.zip"
```

`HERMES_DESKTOP_VARIANT` 必须同时出现在 **打包** 进程：只给 `npm run build` 时主进程戳是
headless，但 `product-identity.cjs` 会按稳定版命名产出 `Hermes-…-setup.exe`（appId/userData
与既有安装重叠）。

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `HermesHeadless-0.22.5-fork.1-win-x64-setup.exe` | 146,461,650 | `55C14C5CE49C44074738F53A8D308007956EEBCE6076B84FD6BD712F97FFB640` |
| `HermesHeadless-0.22.5-fork.1-win-x64-portable.exe` | 146,265,492 | `363229FB3AC21E7F1EE2865F65E74B80205A65E901F836379FDF4C5154BDDA58` |

包内 stamp（`release-headless/win-unpacked/resources/install-stamp.json`）：

```json
{"commit":"0d1be39e66fe111bc2db59510827bde4c635e32e","dirty":false,"payload":"headless",
 "distribution":"desktop-app","updateMechanism":"external",
 "localRuntimeRoot":"D:\\VSCodeProjects\\hermes-agent"}
```

PE 元数据（`Get-Item … .VersionInfo` 实测）：

| 文件 | CompanyName | FileDescription | LegalCopyright |
|---|---|---|---|
| `KouriChat Next Headless.exe` | `FunnyAWM` | `KouriChat Next Headless` | `Copyright © 2026 FunnyAWM` |
| `…-setup.exe` / `…-portable.exe` | `FunnyAWM` | `Native desktop shell for Rosmontis.` | `Copyright © 2026 FunnyAWM` |

来源：安装包 `FileDescription` 取 `apps/desktop/package.json` 的 `description`；公司/版权取
`author.name` 与 `electron-builder.config.cjs` 的 `copyright`。联系邮箱
`funnyawm@qq.com` 记录在 `package.json` 的 `author.email`（PE 版本资源本身没有邮箱字段），
Linux 包 maintainer 同步为 `FunnyAWM <funnyawm@qq.com>`；About 面板版权同步改为 FunnyAWM。

可执行文件为 `KouriChat Next Headless.exe`（窗口标题 `KouriChat Next`，品牌图标已替换）；
与 bootstrap/稳定包（`Hermes.exe`、`Hermes-…-setup.exe`）文件名、appId、userData 均不同，
可并存。安装包文件名仍为 `HermesHeadless-*`：`artifactNamePascal` 有意保持内部身份不变，
否则既有安装无法原地升级。

## 双击等价启动实测（2026-11-06 17:04）

直接启动 `release-headless/win-unpacked/KouriChat Next Headless.exe`（不设任何环境变量），
`%LOCALAPPDATA%\hermes\logs\desktop.log` 新增：

```
17:04:04,841 [backend] using Hermes source at D:\VSCodeProjects\hermes-agent
17:04:13,772 [gateway] owner runs 8b7d5a18af… but this Hermes is 0d1be39e66…; restarting it
17:04:45,758 [backend] Hermes gateway is ready
17:04:45,781 [gateway-route win=main] published connection=local
17:04:49,772 [bot-roster win=main] snapshot active=local answered=70b8548e expected=70b8548e rows=[local/default=""]
```

即：烘焙根生效、命中阶梯 1b、连接发布为 local、后端就绪；全程无 bootstrap 日志。
`bot-roster` 快照只有 `local/default` 一行，与“机器人板块仅保留 default”一致。
网关 owner 是上一构建的 `8b7d5a18af`，启动时自动重启到当前 `0d1be39e66`。
`CloseMainWindow` 关闭，应用干净退出（进程数归零）；gateway 进程按 Desktop 设计**继续存活**
（`local-gateway.ts`：desktop 只拥有有界 CLI 客户端，不杀 detached owner），
需要停用 `hermes gateway stop`。

## 产物（`release-headless/`，基座 v0.23.0 重建，2026-11-06 21:43）

构建（干净工作树 `a0f8f725cd`；跑前把两个未跟踪交付物移到仓库外，因为 `dirty` 按
`git status --porcelain --untracked-files=all` 计算，未跟踪文件也算脏；跑完移回）。旧基线的
`HermesHeadless-0.22.5-fork.1-*` 与同一次 bundled 半途产物 `apps/desktop/build/agent-payload`
（3.7 GB）已删除；受管包 store `.cache/desktop-inputs/tools`（3.6 GB）保留。

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `HermesHeadless-0.23.0-fork.1-win-x64-setup.exe` | 146,509,284 | `090A93072233F9922A34C0CE723A8876B911D3D840BBA059CD7D1B6A759E0F33` |
| `HermesHeadless-0.23.0-fork.1-win-x64-portable.exe` | 146,313,133 | `C9594E83E0D55C3FB36143D632E6E3F6D8B3E013CE93BAD742BF229DF6DDF842` |

包内 stamp（`release-headless/win-unpacked/resources/install-stamp.json`）：

```json
{"commit":"a0f8f725cdbd66961dbdba4df6ffd6775dfc9d0e","branch":"patches","dirty":false,
 "payload":"headless","distribution":"desktop-app","updateMechanism":"external",
 "localRuntimeRoot":"D:\\VSCodeProjects\\hermes-agent"}
```

双击等价启动实测（`win-unpacked\KouriChat Next Headless.exe`，不设任何环境变量，
2026-11-06 21:44）：

```
21:44:33,995 [backend] Hermes gateway is ready
21:44:34,003 [backend] using Hermes source at D:\VSCodeProjects\hermes-agent
21:44:34,045 [gateway-route win=main] published connection=local
21:44:38,093 [gateway-route win=main] active key=default via=primary conn=local profile=default
21:44:39,299 [hermes:api GET /api/model/info] Error: Timed out connecting to Hermes backend after 5000ms
21:44:42,617 [bot-roster win=main] snapshot active=local answered=70b8548e expected=70b8548e rows=[local/default=""]
```

即：烘焙根命中、后端就绪、连接发布为 local、机器人板块只有 `local/default` 一行，全程无
bootstrap 日志。`/api/model/info` 的 5 秒超时是**冷启动竞态、非本次引入**：同一行日志今天已出现
6 次（17:11 一次、17:42 四次、本次一次），都在后端刚就绪的几秒内，之后路由与 roster 正常；
未继续定位（不属本任务口径）。`CloseMainWindow` 后进程仍为 4 个（T-23 的托盘驻留：关闭窗口只隐藏，
应用与本机 gateway 继续运行），真正退出需托盘 Quit。

21:43 之前还有一次 v0.23.0 重建（干净树 `ac3d203a74`，20:52，setup
`FD5E0EBB8D48D7C840F041633BC9C8963E7AE2BBBA756BEF7CFA504B8E1D2ADA`），已被本次取代，只换提交、
口径与结论一致（同一条实机日志路径、同样命中烘焙根）。

## 边界

- 构建戳 `dirty: false`，与 `0d1be39e66` 源码对应；更早的 `8b7d5a18af` 与 `58d1f503f6f7`
  两组产物已被本次重建取代。
- `localRuntimeRoot` 是构建机器的事实（`D:\VSCodeProjects\hermes-agent`）。换机器后该级失效，
  走后续阶梯；源码里这是数据，不是探测。
- T-12 的覆盖安装与哈希证据针对 bootstrap 包，对 headless 包不成立；headless 需要单独做
  覆盖安装回归（`scripts/verify_nsis_overwrite.ps1 -Installer release-headless/…`）。
- 未跑完整 electron vitest 套件（只跑改动相关文件 + typecheck + eslint）；`release-headless/`
  已加入 `.gitignore`，二进制不入库。

## 完整版（bundled）：2026-11-06 裁决不做

`prepare_tools` 需要 15 个受管包，账号 store 只有 ffmpeg/node/ripgrep/uv；缺 11 个：
`bws chromium cua-driver gh git llamacpp-cpu/cuda/hip/vulkan npm python`。其中 python 为
`cpython-3.14.9+20261021`（GitHub release 资产）。本机 egress 半通（PyPI 200、codeload 301、
`github.com` 000），且密封 payload 按设计不复用宿主工具（`pm/local_tools.py:154`）。

2026-11-06 把 15 个包全部离线取回（`.cache\desktop-inputs\tools`，3.6 GB，脚本
`scripts/download-managed-tools.ps1` 断点续传跑通）后仍判不做，原因换成体量：密封 payload 的
`stage_tools` + `desktop.py --commit` 要把 CPython、node、uv、ffmpeg 等一并打进包，产物从
headless 的约 140 MB 涨到 3.7 GB 以上，使用者确认「bundle 太重了」，改为**只发 headless 包，
环境由用户按需补齐**，口径写进 `INSTALL.md`（§3 依赖、§4 运行时阶梯、§5 工具、§6 镜像）。

上游原生通路仍未走通，两处障碍与解法都记在案：

- `scripts/bundles/desktop.py --commit` 拒绝 fork 版本号（要求 `X.Y.Z`，本 fork 是
  `0.23.0+fork1`）→ 走 fork 打包路径 `npm run payload` + `npm run build` + `electron-builder
  --config electron-builder.fork.cjs`。
- PM 的 3 天供应链年龄门禁（uv `exclude-newer`）要求索引报上传时间：华为云镜像不报，
  解析在 `setuptools>=40.8.0` 上无解；清华 `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`
  报时间且 wheel 可取，实测可用（`set UV_INDEX_URL=…`，不改仓库配置、不绕过门禁）。

如果将来要出完整版：放通 GitHub release 资产后 `scripts/bundles/native_build.py --prepare-only`
→ `--prepared` → `HERMES_DESKTOP_VARIANT=bundled`，或搬运 `tools/` + `uv-cache` 离线 `stage_tools`。
