# T-23 · 托盘驻留与网关生命周期（2026-11-06）

目标：桌面端默认允许驻留托盘；本机 gateway 的生命周期与桌面端一致——应用在托盘里时
gateway 继续运行，真正退出桌面端时 gateway 一并停止。远程/Cloud 后端不受影响。

## 实现

| 文件 | 改动 |
|---|---|
| `apps/desktop/electron/minimize-to-tray.ts` | 偏好文件缺失时默认开启托盘驻留（写坏的文件仍按旧行为关闭）；托盘提示与菜单使用 `app.getName()`，不再是硬编码 `Hermes` |
| `apps/desktop/electron/gateway-quit.ts`（新） | `stopGatewayForQuit()` 依次尝试 headless 烘焙根与更新根的 `venv`/`.venv` CLI，detached 执行 `hermes gateway stop -y`（带 `HERMES_HOME`）；找不到 CLI 或 spawn 失败只记日志，不阻塞退出 |
| `apps/desktop/electron/main.ts` | 退出 teardown 中调用 `stopGatewayForQuit([headlessRuntimeRoot(...), resolveUpdateRoot()], ...)`；只加一个调用，行数棘轮保持 18,220 |
| `apps/desktop/electron/quit-guard.ts` | 本机 gateway 改为“应用拥有”：退出确认提示本机工作会丢失；远程/Cloud 仍提示继续运行 |
| `apps/desktop/electron/minimize-to-tray.test.ts`、`quit-guard.test.ts`、`gateway-quit.test.ts` | 覆盖默认驻留、本机 gateway 归属、CLI 查找/参数/失败降级 |

说明：关闭窗口只隐藏到托盘，不触发 `before-quit`，所以不会停网关；托盘 Quit、`app.quit()`、
系统会话结束才走 teardown。停止命令不带 `--all`，只停当前 `HERMES_HOME` 的 owner，不会误停
其它 home 的网关。

## 测试

```
npx vitest run --project electron electron/gateway-quit.test.ts electron/quit-guard.test.ts \
  electron/minimize-to-tray.test.ts
→ 3 files, 34 passed

npx tsc --noEmit -p apps/desktop        → 0 errors
npx eslint <改动文件> --max-warnings 0   → 0 problems
python scripts/check                     → 14 checks, ok（health 18,220 行，0 blocking）
```

## 实机验收（Windows 11）

干净构建 `0dad8e3edb`（`dirty=false`），直接启动 `release-headless/win-unpacked/KouriChat Next Headless.exe`：

1. **启动**：gateway 就绪，连接 `local`；`hermes gateway status` → `✓ Gateway is running`。
2. **关闭窗口**：`CloseMainWindow` 后窗口隐藏（`MainWindowTitle` 为空），4 个应用进程仍在，
   gateway PID 仍为 43488、状态 running——托盘驻留生效。
3. **真正退出**：通过调试端口在渲染进程执行 `window.hermesDesktop.updateHold.quit()`，
   返回 `{ok:true}`；6 秒后应用进程归零，`hermes gateway status` →
   `✗ Gateway is not running`——网关随桌面端退出。

随后用同一安装包做静默覆盖安装（`setup.exe /S`，退出码 0），启动安装版，应用与 gateway
均正常启动（安装后本次实测 gateway PID 43748）。覆盖安装未触碰 `HERMES_HOME` 下的记忆与笔记。

## 产物（`apps/desktop/release-headless/`）

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `HermesHeadless-0.22.5-fork.1-win-x64-setup.exe` | 146,461,807 | `5B207F140F77D4C3CF6000A42C8E62A53E0409127544E0A35C696B0022F88281` |
| `HermesHeadless-0.22.5-fork.1-win-x64-portable.exe` | 146,265,662 | `673DA83DBEB8C1D08E7E7C2CD970BC2659DB680859664011E3FC14775B1B438A` |

包内 stamp：`commit=0dad8e3edb`、`dirty=false`、`payload=headless`、
`localRuntimeRoot=D:\VSCodeProjects\hermes-agent`。

## 边界

- 强制结束进程（任务管理器 / `Stop-Process -Force` / 崩溃）不走 `before-quit`，gateway 会继续
  存活；下次启动重新 attach。生命周期一致只覆盖正常退出路径。
- 远程 URL、Hermes Cloud、SSH 连接不会因为本机退出而停止；`quit-guard` 仍区分这两类后端。
- 托盘默认值只在偏好文件**不存在**时生效；已经显式关闭的用户保持关闭，仍可在设置里重新打开。
- 关闭窗口后 Chromium 会回收部分渲染进程，主进程与后台任务仍在；不是崩溃。
