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

## 产物（`apps/desktop/release-headless/`，2026-11-06 16:41 重建）

构建（干净工作树 `8b7d5a18af`）：

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
与既有安装重叠）。该次误产物已删除，下表为带变体重打包的结果。

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `HermesHeadless-0.22.5-fork.1-win-x64-setup.exe` | 146,461,618 | `51738510CB2FA911067E9DEAACCE0D9CE673A7DB52C9F95FD8D358967EF414A5` |
| `HermesHeadless-0.22.5-fork.1-win-x64-portable.exe` | 146,265,465 | `D4BD10BB861E1FC5B6879DC807EC89FFA236B5E9232C94EA293B0A8BC5CDDBFD` |

包内 stamp（`release-headless/win-unpacked/resources/install-stamp.json`）：

```json
{"commit":"8b7d5a18afea69860328136190f54618a5fbe3ce","dirty":false,"payload":"headless",
 "distribution":"desktop-app","updateMechanism":"external",
 "localRuntimeRoot":"D:\\VSCodeProjects\\hermes-agent"}
```

可执行文件为 `KouriChat Next Headless.exe`（窗口标题 `KouriChat Next`，品牌图标已替换）；
与 bootstrap/稳定包（`Hermes.exe`、`Hermes-…-setup.exe`）文件名、appId、userData 均不同，
可并存。安装包文件名仍为 `HermesHeadless-*`：`artifactNamePascal` 有意保持内部身份不变，
否则既有安装无法原地升级。

## 双击等价启动实测（2026-11-06 16:44）

直接启动 `release-headless/win-unpacked/KouriChat Next Headless.exe`（不设任何环境变量），
`%LOCALAPPDATA%\hermes\logs\desktop.log` 新增：

```
16:44:15,255 [backend] using Hermes source at D:\VSCodeProjects\hermes-agent
16:44:26,098 [gateway] owner runs 1d1e08e0… but this Hermes is 8b7d5a18af…; restarting it
16:44:56,615 [gateway-route win=main] published connection=local
16:45:13,371 [bot-roster win=main] snapshot active=local answered=70b8548e expected=70b8548e rows=[local/default=""]
16:45:28,150 [backend] Hermes gateway is ready
```

即：烘焙根生效、命中阶梯 1b、连接发布为 local、后端就绪；全程无 bootstrap 日志。
`bot-roster` 快照只有 `local/default` 一行，与“机器人板块仅保留 default”一致。
网关 owner 仍是旧提交 `1d1e08e`，启动时自动重启到当前 `8b7d5a18af`；重启窗口内
`GET /api/profiles` 报过一次 `Gateway ticket bootstrap failed`，网关就绪后未再出现
（属重启竞态，不是启动失败）。
`CloseMainWindow` 关闭，应用干净退出（进程数归零）；gateway 进程按 Desktop 设计**继续存活**
（`local-gateway.ts`：desktop 只拥有有界 CLI 客户端，不杀 detached owner），
需要停用 `hermes gateway stop`。

## 边界

- 构建戳 `dirty: false`，与 `8b7d5a18af` 源码对应；早先 14:53 那一组产物（`58d1f503f6f7`、
  `dirty: true`）已被本次重建取代。
- `localRuntimeRoot` 是构建机器的事实（`D:\VSCodeProjects\hermes-agent`）。换机器后该级失效，
  走后续阶梯；源码里这是数据，不是探测。
- T-12 的覆盖安装与哈希证据针对 bootstrap 包，对 headless 包不成立；headless 需要单独做
  覆盖安装回归（`scripts/verify_nsis_overwrite.ps1 -Installer release-headless/…`）。
- 未跑完整 electron vitest 套件（只跑改动相关文件 + typecheck + eslint）；`release-headless/`
  已加入 `.gitignore`，二进制不入库。

## 完整版（bundled）当前阻塞（T-20）

`prepare_tools` 需要 15 个受管包，账号 store 只有 ffmpeg/node/ripgrep/uv；缺 11 个：
`bws chromium cua-driver gh git llamacpp-cpu/cuda/hip/vulkan npm python`。其中 python 为
`cpython-3.14.9+20261021`（GitHub release 资产）。本机 egress 半通（PyPI 200、codeload 301、
`github.com` 000），且密封 payload 按设计不复用宿主工具（`pm/local_tools.py:154`）。
解除条件：放通 GitHub release 资产后跑 `scripts/bundles/native_build.py --prepare-only`，
或从另一台机器搬运 `tools/` + `uv-cache` 后离线 `stage_tools`。
