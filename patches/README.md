# patches

把本 fork 相对上游 v0.22.5 基线的全部改动，按提交拆成可重放的补丁列。

- 基线：`f5e0dd8e9b097a9052e3c8ecdcd0c6d6ac49dc7`（v0.22.5）
- 覆盖到 `d3c6d52c7e` 为止的 48 个提交（导出本目录的提交自身不在列）
- 应用后重建的提交历史与 `main` 逐条同主题、同作者

## 应用

在基线工作区（仓库根目录）执行：

```bash
git am --3way patches/*.patch
```

补丁带有 `--base` 信息，`git am --3way` 可用三方合并兜底。

## 二进制素材

`patches/0040-*.patch` 原本还改了 82 个图标 / 图片（Windows ICO、mac ICNS、
MSIX tile、窗口图标等）。这些二进制补丁会被 `scripts/check` 的 500 KiB 单文件
上限拦下，因此已从补丁中剔除，清单见 [assets-excluded.txt](assets-excluded.txt)。

应用补丁后，从一份含这些文件的检出（本仓库 `main`）复制回来：

```powershell
$src = 'D:\VSCodeProjects\hermes-agent'
Get-Content patches/assets-excluded.txt | ForEach-Object {
  New-Item -Force -ItemType Directory (Split-Path $_) | Out-Null
  Copy-Item (Join-Path $src $_) $_
}
```

## 验证

在基线处新建干净 worktree，`git am --3way` 48 个补丁全部成功；重放后的提交主题
与 `main` 完全一致；与 `main`（`d3c6d52c7e`）对比，仅上面 82 个二进制素材不同，
文本文件零差异。

## 重新生成

```bash
git format-patch --no-binary --no-signature \
  --base=f5e0dd8e9b097a9052e3c8ecdcd0c6d6ac49dc7 \
  -o patches f5e0dd8e9b097a9052e3c8ecdcd0c6d6ac49dc7..HEAD
```

`--no-binary` 必须显式给出：默认会内嵌二进制，单个补丁可到 4 MB，过不了
`scripts/check` 的体积门禁。生成后仍需剔除 `Binary files ... differ` 段
（整段 `diff --git` 块）。
