# Kouri Harness Headless · 环境与安装说明

**适用对象**：`apps/desktop/release-headless/` 下的两个包——安装版
`HermesHeadless-0.23.0-fork.1-win-x64-setup.exe` 与免安装版
`HermesHeadless-0.23.0-fork.1-win-x64-portable.exe`。基座是上游 **v0.23.0**
（`2bd378ed1b`）。

**这个包是什么**：`headless` 指**包里不携带运行时**，不是无界面程序。它照常打开桌面窗口，
Agent 跑在你机器上已有的 Kouri Harness 源码树 + venv 上。好处是包小（约 140 MB）、升级源码不动包；
代价是需要你自己把环境补齐。想要开箱即用的完整版（运行时密封进包）目前没有发行计划——
包体会涨到 3.7 GB 以上，改为按需补环境。

---

## 1. 你需要准备什么

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 10 / 11，x64 | 安装包是 per-user，不需要管理员权限 |
| 磁盘 | 源码树 + venv 预留 3 GB；工具按需另计 | Chromium 约 170 MB，llama.cpp 变体每个约 100 MB |
| Kouri Harness 源码树 | 任意路径的一份检出 | 必须含 `hermes_cli\main.py`；桌面端按下面的阶梯找它 |
| Python | **3.11–3.14**（`pyproject.toml`：`>=3.11,<3.15`） | 必须是真实 CPython，Microsoft Store 的 `python` 是重定向 stub，建不出 venv |
| uv | 建议 0.13.x | 用于建 venv、装依赖、装受管工具 |
| 网络 | 首次装依赖需要访问 PyPI | 走镜像见 §6 |
| 模型凭据 | API Key 或 OAuth | 写在 `%LOCALAPPDATA%\hermes\.env`，**不要写进仓库或对话** |

## 2. 安装

1. 双击 `...-setup.exe`。它是引导式、per-user 安装：**不弹管理员提示**，默认装到
   `%LOCALAPPDATA%\Programs\Kouri Harness\`，安装过程中**可以自选安装目录**；不进 Program Files。
2. 免安装就双击 `...-portable.exe`，解压即用。
3. 安装后的程序名、快捷方式与窗口标题均为「Kouri Harness」（不带 Headless 后缀）。
   桌面端自己的偏好数据仍落在独立目录 `%APPDATA%\Kouri Harness Headless\`（appId
   `com.nousresearch.hermes-headless`），与正式版的 `%APPDATA%\Kouri Harness` 互不相通——
   两份安装可以并存、互不覆盖；若同时安装正式版，请在安装时选择不同的安装目录。
4. Agent 的运行数据（配置、会话、记忆、笔记、日志、工具）落在 **`%LOCALAPPDATA%\hermes\`**，
   与安装包位置无关：
   `config.yaml`、`state.db`、`sessions\`、`memories\`、`notes\`、`logs\`、`tools\`。

## 3. 补齐 Python 环境（必做）

在源码树里执行。已经有 `.venv` 的可跳到第 2 步。

```bat
cd /d D:\path\to\hermes-agent
uv venv .venv --python 3.14
.venv\Scripts\python.exe -m uv sync --all-extras --no-extra kittentts
```

或直接用 venv 里的 `uv`：`.venv\Scripts\uv.exe sync --all-extras --no-extra kittentts`。

- `--all-extras` 覆盖云端模型、消息平台、语音、文档解析等能力。
- `kittentts` 是显式 opt-in 的本地 TTS，不装不影响其他功能；要装就
  `uv sync --extra kittentts`。
- 缺依赖的典型表现是启动后 `ModuleNotFoundError`，或 `hermes doctor` 的 Required Packages 报失败。
  核心必需模块：`openai`、`rich`、`python-dotenv`、`ruamel.yaml`、`httpx`。

## 4. 让桌面端找到这份运行时

启动时按顺序解析，命中即用：

1. `HERMES_DESKTOP_HERMES_ROOT`（显式指定源码树，最可靠）
2. 构建时烘焙的路径（本包烘焙的是构建机路径，换机后一般不适用）
3. `HERMES_DESKTOP_HERMES`
4. `%LOCALAPPDATA%\hermes\hermes-agent`
5. 已发布的 launcher
6. 都找不到 → 报错退出

解释器只认该源码树自己的 `.venv\Scripts\python.exe`，其次 `venv\Scripts\python.exe`，也可以用
`HERMES_DESKTOP_PYTHON` 直接指定解释器。**它不会回退到 PATH 上的 Python，也永远不会自己去
bootstrap 装运行时。**

找不到运行时时弹出的原文是：

```
This headless build found no local Kouri Harness runtime, and it never installs one.
Point HERMES_DESKTOP_HERMES_ROOT at a checkout, or install Kouri Harness with `hermes pm install`.
```

处理方式：把 `HERMES_DESKTOP_HERMES_ROOT` 设成源码树绝对路径（用户环境变量里设一次即可），
或把源码树放到 `%LOCALAPPDATA%\hermes\hermes-agent`。

```bat
setx HERMES_DESKTOP_HERMES_ROOT "D:\path\to\hermes-agent"
```

## 5. 按需补基础工具（建议装，缺了只是少能力）

headless 不密封这些工具。**聊天、终端、文件、任务列表、定时任务、子代理不需要它们。**
PM 会优先复用你机器上已有的合格版本，复用不上才下载受管包。

| 工具 | 用途 | 本版锁定的版本 | 检查 |
| --- | --- | --- | --- |
| `git` | 工作区、git 工具（Windows 需含 Git Bash） | 2.53.0+3 | `git --version` |
| `ripgrep` | 代码搜索，缺失降级 grep | 15.2.0 | `rg --version` |
| `uv` | 依赖安装与锁文件 | 0.13.5 | `uv --version` |
| `node` | npx 型 MCP、部分技能 | 26.12.1 | `node -v` |
| `pnpm` | JS 工作区安装（版本由 `pm/lock.json` 钉死） | 12.15.0 | `pnpm -v` |
| `ffmpeg` | 音视频转码 | 9.0.1 | `ffmpeg -version` |
| `gh` | GitHub 操作 | 2.97.0 | `gh --version` |

```bat
.venv\Scripts\hermes.exe pm install git ripgrep uv node pnpm ffmpeg gh
```

按能力选装：

| 能力 | 需要 | 命令 |
| --- | --- | --- |
| 浏览器自动化 | `chromium`（1208 / 145.0.7632.6） | `hermes pm install chromium`，或在 `config.yaml` 设 `browser.executable_path` 指向你已有的 Chromium |
| 本地模型推理 | `llamacpp-cpu` / `-cuda` / `-hip` / `-vulkan`（11671） | NVIDIA 选 `-cuda`，AMD 选 `-hip` 或 `-vulkan`，纯 CPU 选 `-cpu`，**只装一个** |
| 桌面操作（computer-use） | `cua-driver` 0.21.0 | `hermes pm install cua-driver` |
| Bitwarden 密钥注入 | `bws` 2.0.0 | `hermes pm install bws` |

受管工具落在 `%LOCALAPPDATA%\hermes\tools\`。要一次把这 15 个包全部离线取回（例如给内网机器
准备），用仓库脚本：

```powershell
powershell -NoProfile -File .\scripts\download-managed-tools.ps1 -ListOnly   # 先看要下哪些
powershell -NoProfile -File .\scripts\download-managed-tools.ps1             # 断点续传，重复跑会跳过已完成项
```

默认落在 `.cache\desktop-inputs\tools`（约 3.6 GB），不会碰 `%LOCALAPPDATA%\hermes` 里的应用数据。

## 6. 网络受限、要用 PyPI 镜像时

PM 有一条供应链年龄门禁：**只接受发布满 3 天的依赖**。它需要索引提供每个包的上传时间。
所以选镜像时必须选**报上传时间**的，否则解析会失败，典型报错：

```
Because there are no versions of setuptools and you require setuptools>=40.8.0, ...
hint: `setuptools` was filtered by `exclude-newer` ... the package index did not report upload times
```

验证一个镜像能不能用（返回体里出现 `"upload-time"` 才行）：

```bash
curl -s -H 'Accept: application/vnd.pypi.simple.v1+json' \
  https://<你的镜像>/simple/setuptools/ | grep -o '"upload-time"' | head -1
```

实测记录：`https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple` 报时间，可用；
`https://mirrors.huaweicloud.com/repository/pypi/simple/` 不报时间，会卡在上面那条报错上。
使用时只设索引变量，不要改仓库配置：

```bat
set UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
```

门禁本身不建议绕过——它是防「刚发布数小时的包」的一条硬线。

## 7. 装完自检

- [ ] `cd /d D:\path\to\hermes-agent && .venv\Scripts\hermes.exe --version` 有版本输出
- [ ] `.venv\Scripts\hermes.exe doctor` 无 FAIL，重点看 Required Packages、External Tools、
      Tool Availability 三节
- [ ] 启动 `Kouri Harness.exe`（或开始菜单的「Kouri Harness」），`%LOCALAPPDATA%\hermes\logs\desktop.log` 里出现
      `[backend] using Kouri Harness source at <你的源码树>` 与 `published connection=local`
- [ ] 设置 → About → Runtime 显示 `external/source`，不是 bootstrap
- [ ] 发一条消息得到回复；机器人页只有一个默认助手（本版按单机器人形态收敛）

## 8. 升级、回退与卸载

- **升级 Agent**：只更新源码树（`git pull` 后重跑 §3 的 `uv sync`），不用重装桌面端。
- **升级桌面端**：直接跑新安装包覆盖安装。应用数据在 `%LOCALAPPDATA%\hermes\`，覆盖安装
  不动 `memories\`、`notes\`、`config.yaml`、`state.db`，全程不需要卸载。
- **没有自动更新**：fork 构建禁用了更新源与更新检查，不会在后台拉上游发布替换本包。
- **卸载**：控制面板或安装目录的卸载器。删掉后 `%LOCALAPPDATA%\hermes\` 仍在，想留档就别动它；
  真要清空是删这个目录，**不可撤销**，先备份。
- **换机器**：拷源码树 + 设 `HERMES_DESKTOP_HERMES_ROOT` + 重跑 §3；工具层用 §5 的离线脚本带上
  `.cache\desktop-inputs\tools` 或直接复制 `%LOCALAPPDATA%\hermes\tools\`。

## 9. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动提示找不到 Kouri Harness 运行时 | 烘焙路径在这台机器不存在，或该树没有 `.venv` | 设 `HERMES_DESKTOP_HERMES_ROOT` / `HERMES_DESKTOP_PYTHON`，或按 §3 建 venv |
| `ModuleNotFoundError` | 依赖没装齐，或装到了别的解释器 | 在**要用的那个** venv 里重跑 `uv sync --all-extras --no-extra kittentts` |
| `uv sync` 报 `setuptools` 无解 | 镜像不报上传时间 | 换 §6 验证过的镜像，或用默认 PyPI |
| 浏览器工具说缺 Chromium | 既没装受管包也没配路径 | `hermes pm install chromium` 或设 `browser.executable_path` |
| `pm install` 卡住 | GitHub release 资产不可达 | 放通网络，或用 §5 的离线脚本/拷贝 `tools\` |
| 建 venv 时报找不到 Python | Store 版 `python` 是 stub | 装真实 CPython 3.11–3.14 再 `uv venv` |
| 机器人连接掉线后消息页空白 | 会话与包无关，是运行时状态 | 看 `%LOCALAPPDATA%\hermes\logs\`，重开桌面端；配置不用重建 |
| `hermes sync pair` 扫描 8 秒没找到手机 | 路由器拦了组播，或手机在不同网段 | 先确认两端在同一个 /24：PC 会自动补一轮同网段 TCP 探测；仍找不到就填地址 `hermes sync pair 192.168.x.x:27890`，并放行 27890 入站 |
| 手机连上了但 PC 端 `sync now` 找不到主设备 | LAN 地址变了（DHCP/切 Wi-Fi/VPN） | 每次同步都会重新发现主设备并回写新地址；无需手动改配置 |
| 桌面“记忆”页是空的或打不开 | 后端版本旧，没有 `/api/memory/*` | 升级到本 fork 当前构建；文件确实不存在时会显示“还没有创建” |
| 记忆页编辑后保存失败 | 目标文件只读或不在白名单 | `notes/docs/` 是导入原文，设计上只读，改引用它的笔记即可；其余文件确认 `%LOCALAPPDATA%\hermes\` 未被系统设为只读 |

## 10. 本次构建的参考环境

| 项 | 版本 |
| --- | --- |
| 基座 | 上游 `v0.23.0`（`2bd378ed1b`），fork 版本 `0.23.0-fork.1` |
| Electron | 44.9.3 |
| Python（构建用 venv） | 3.14.2 |
| uv | 0.13.7 |
| 受管工具 | 见 §5 表（`pm/lock.json` 同一份） |

产物哈希、构建提交与实测记录见 [evidence/t15-headless.md](evidence/t15-headless.md)；
打包实现细节见 [apps/desktop/BUILDING.md](apps/desktop/BUILDING.md)。
