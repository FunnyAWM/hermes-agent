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

## 产物（`apps/desktop/release-headless/`）

构建（脏工作区，仅打包验证）：

```powershell
$env:HERMES_DESKTOP_VARIANT = "headless"
$env:HERMES_DESKTOP_LOCAL_ROOT = "D:\VSCodeProjects\hermes-agent"
npm run build
npx electron-builder --config electron-builder.fork.cjs --win nsis portable --x64 --publish never `
  -c.directories.output=release-headless `
  -c.electronDist="C:\Users\11429\AppData\Local\electron\Cache\0a74c7b0...\electron-v44.9.3-win32-x64.zip"
```

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `HermesHeadless-0.22.5-fork.1-win-x64-setup.exe` | 146,818,202 | `E371FCBBB397250C752D56FA5FB27B25F47B4EDB87446A0A5804BC6AE02B97A1` |
| `HermesHeadless-0.22.5-fork.1-win-x64-portable.exe` | 146,629,492 | `4A68A3118DC9D03AF2031B4CEBE3A88DA3AE19CD5D7DCCD52872B44170EDAF8B` |

包内 stamp（`release-headless/win-unpacked/resources/install-stamp.json`）：

```json
{"commit":"58d1f503f6f7adff111154b5f9e016c7566f399a","dirty":true,"payload":"headless",
 "distribution":"desktop-app","updateMechanism":"external",
 "localRuntimeRoot":"D:\\VSCodeProjects\\hermes-agent"}
```

可执行文件为 `Hermes Headless.exe`；与 bootstrap 包（`Hermes.exe`、`Hermes-…-setup.exe`）
文件名、appId、userData 均不同，可并存。

## 双击等价启动实测

直接启动 `release-headless/win-unpacked/Hermes Headless.exe`（不设任何环境变量），
`%LOCALAPPDATA%\hermes\logs\desktop.log` 新增：

```
14:54:48,285 [boot] Resolving Hermes backend
14:54:48,289 [backend] using Hermes source at D:\VSCodeProjects\hermes-agent
14:55:31,216 [backend] Hermes gateway is ready
14:55:31,222 [backend] using Hermes source at D:\VSCodeProjects\hermes-agent
14:55:31,249 [gateway-route win=main] published connection=local
```

即：烘焙根生效、命中阶梯 1b、43 秒内后端就绪、连接发布为 local；全程无 bootstrap 日志。
`CloseMainWindow` 关闭，应用干净退出（`release-headless` 进程数归零）；gateway 进程按
Desktop 设计**继续存活**（`local-gateway.ts`：desktop 只拥有有界 CLI 客户端，不杀 detached owner），
需要停用 `hermes gateway stop`。

## 边界

- 构建戳 `dirty: true`：本次变体改动当时未提交，产物只作功能验证，不作发行。
- 上面的两个安装包构建于**收口重构之前**的工作树；重构只把阶梯 1–1b、末级硬停与
  Runtime 行抽成函数（行为不变），由 11 项 `headless-runtime` 用例、8 文件 54 项回归与
  `scripts/check` 覆盖。若需与最终源码逐字节对应的安装包，按上面命令重建即可。
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
