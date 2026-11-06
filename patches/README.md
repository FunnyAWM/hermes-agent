# patches

本 fork 相对上游 **v0.23.0** 基线的全部改动，按提交拆成可重放的补丁列。

- 基线：`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`（本地 annotated tag `v0.23.0`）
- `0001`–`0056`：v0.23.0 之上的 56 个 fork 提交。**导出本目录的提交、只写进度记录的
  提交不入列**（它们改的就是本目录，或改的行由导出提交引入）；列数以后每次重导出都会变，
  以 `ls patches/00*.patch | wc -l` 为准
- `0057`：补上两个未跟踪的交付物文件 `INSTALL.md`、`scripts/download-managed-tools.ps1`
- `fork-side-of-merge-conflicts.patch`：不是提交补丁，是再往上合并时的冲突素材，
  不参与 `git am`

上一版基线（v0.22.5 / `f5e0dd8e9b`）的补丁列留在历史里，按标签取回：

```bash
git show v0.22.5-fork.1:patches/README.md
```

## 应用

在基线工作区（仓库根目录）执行：

```bash
git am --3way patches/00*.patch
```

补丁带 `--base` 信息，`--3way` 可用三方合并兜底；只有文件名以 `00` 开头的进入上面
这条命令，冲突素材不参与。

## 二进制素材

`patches/0043-feat-desktop-rebrand-the-app-as-KouriChat-Next.patch` 原本还改了 82 个
图标 / 图片（Windows ICO、mac ICNS、MSIX tile、窗口图标等）。内嵌二进制后单个补丁约
4 MB，过不了 `scripts/check` 的 500 KiB 单文件上限，因此已从补丁中剔除，清单见
[assets-excluded.txt](assets-excluded.txt)。

应用补丁后，从一份含这些文件的检出复制回来：

```powershell
$src = 'D:\VSCodeProjects\hermes-agent'
Get-Content patches/assets-excluded.txt | ForEach-Object {
  New-Item -Force -ItemType Directory (Split-Path $_) | Out-Null
  Copy-Item (Join-Path $src $_) $_
}
```

## 再往上游合并时

`fork-side-of-merge-conflicts.patch` 是 `pm/install.py` 在 fork 侧的全量改动，基准是
分叉点 `2bd378ed1b8d`（与 `origin/main` 的 merge-base）。合并 `origin/main`
（`90d6b98fd8`）之后按三方方式应用，让双方改动以冲突标记显形、逐处手工合：

```bash
git apply -3 patches/fork-side-of-merge-conflicts.patch
```

`package-lock.json` 不在其中：上游已迁到 pnpm 并删掉该文件，本地不再保留。

## 验证

在 `v0.23.0` 处新建干净 worktree，`git am --3way patches/00*.patch` 57 个全部成功；
重放后的工作树与本分支只差三类内容：这些补丁文件本身（导出提交不入列）、上面 82 个
二进制素材、以及 `0056` 新引入的两个未跟踪文件。

## 重新生成

导出提交与 TODO 进度记录不进补丁列，所以逐提交生成并跳过它们：

```bash
EXCL="$(git rev-parse <export-or-bookkeeping-sha> ...)"
n=1
for c in $(git rev-list --reverse v0.23.0..HEAD); do
  case " $EXCL " in *" $c "*) continue ;; esac
  git format-patch -1 --no-binary --no-signature --base=v0.23.0 --start-number $n -o patches "$c" >/dev/null
  n=$((n + 1))
done
```

`--no-binary` 必须显式给出：默认会内嵌二进制，单个补丁可到 4 MB，过不了体积门禁。
生成后仍需剔除 `Binary files ... differ` 所在的整段 `diff --git` 块。未跟踪的交付物按
同样办法单独生成一个补丁号（当前是 `0057`）。
