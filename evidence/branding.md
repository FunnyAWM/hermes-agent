# 品牌 · KouriChat Next（2026-11-06）

来源：使用者提供的 `logo.svg`，已入库为 `apps/desktop/assets/logo.svg`（566.9 方形，白底圆角 +
青色/黄色标记）。所有图标由这一份矢量生成，PNG 为 1024 主图缩放。

## 名称

| 位置 | 值 |
|---|---|
| `product-identity.cjs` | 显示名：`KouriChat Next` / `KouriChat Next Light` / `KouriChat Next Agent` / `KouriChat Next Headless` |
| `apps/desktop/package.json` | `productName: "KouriChat Next"`（`name` 仍为 `hermes`） |
| `apps/desktop/index.html` | `<title>KouriChat Next</title>` |
| `src/i18n/{en,zh,de,es,fr}.ts`、`ar_boot.ts` | `appName: 'KouriChat Next'`（更新状态/版本详情） |
| 安装包 / 可执行文件 | electron-builder `productName`、`executableName` 随显示名；Windows 可执行文件为 `KouriChat Next.exe` |

**刻意不改**：`appId`（`com.nousresearch.hermes*`）、`appNamePascal`、userData 键、`cliName`
（`hermes`）、MSIX 身份与 `artifactNamePascal`。这样覆盖安装、更新通道、CLI 启动器与已有用户数据
都不会换身份；代价是安装包文件名仍以 `Hermes*` 开头。

## 图标

| 输出 | 尺寸 |
|---|---|
| `apps/desktop/assets/icon{,-dark}.png`、`icon-mac.png`、`icon.icon/Assets/art-{light,dark}.png` | 1024 |
| `apps/desktop/assets/icon{,-dark}.ico` | 16/24/32/48/64/128/256 |
| `apps/desktop/assets/icon{,-dark}.icns` | png2icons 生成的 ICNS |
| `apps/desktop/assets/appx/*` | 现有每个声明尺寸；宽磁贴居中放方形标记 |
| `apps/desktop/public/apple-touch-icon.png` | 1024（窗口/网页图标） |
| `apps/desktop/public/nous-girl{,-dark}.png` | 256（应用内 BrandMark） |
| `apps/bootstrap-installer/src-tauri/icons/*`、`public/nous-girl.png` | 32/128/256、ICO、ICNS、art 层 |

生成方式（一次性，未新增依赖）：

```
npx sharp-cli -i assets/logo.svg -o logo-1024.png resize 1024 1024
python + Pillow 缩放写 PNG / 多尺寸 ICO
png2icons（临时目录安装）生成 ICNS
```

## 验证

```
node -e "console.log(require('./apps/desktop/product-identity.cjs'))"
→ displayName: 'KouriChat Next'；appId/appNamePascal/cliName 未变
npx vitest run --project electron electron/product-identity.test.ts \
  scripts/write-build-stamp.test.mjs scripts/msix-shared.test.mjs \
  scripts/side-by-side-launchers.test.mjs
→ 4 files, 26 tests passed
npx tsc --noEmit -p apps/desktop → 0 errors
```

`product-identity.test.ts` 的变体矩阵已更新为四个新显示名，仍要求四种变体的
displayName/appId/appNamePascal 两两不同。

## 边界

- `scripts/generate_icons.py` **未改**：它仍按上游 nous-girl 源图渲染全部 99 个产物。若有人重新运行
  该脚本或上游 `icons-freshness-check` workflow，这些图标会被覆盖回旧品牌。本次是手工产物，已在
  `assets/logo.svg` 保留唯一矢量源。
- `assets/icon.icon/Assets/mono.png`（macOS 26 Clear/Tinted 单色层）与 `assets/dmg-volume.icns`
  未替换；macOS 不是当前交付目标。
- 安装包未重建，未在资源管理器/开始菜单实机核对新图标；`release*` 目录中的旧包仍是旧品牌。
- 打包后 `productName` 变为 `KouriChat Next`，桌面本地 userData（连接注册表、界面偏好）预计会落到
  `%APPDATA%\KouriChat Next`；会话与记忆在 `HERMES_HOME`，不受影响。这一点未实机覆盖验证。
- 网页站、bootstrap 文案里仍出现的 “Hermes” 指运行时/网关，本次只改应用名，不做全文案改写。
