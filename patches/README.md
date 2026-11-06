# patches

本 fork 相对上游 **v0.23.0** 基线的全部改动，按提交拆成可重放的补丁列。

- 基线：`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`（本地 annotated tag `v0.23.0`）
- 按提交顺序编号的补丁覆盖 v0.23.0 之上的**全部** fork 改动。**只有改了本目录的提交不进列**；
  这类提交如果同时改了 PRD / TODO / evidence，那部分内容会单独导出成一个只含这些路径的补丁，
  所以重放结果和分支的文件内容一致。列数每次重导出都会变，以 `ls patches/00*.patch | wc -l` 为准
- 最后一个补丁是交付物 `INSTALL.md`、`scripts/download-managed-tools.ps1`（不属任何提交，
  由临时提交导出后退回，见「重新生成」）
- 二进制素材不在补丁里，见下节
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

在 `v0.23.0` 处新建干净 worktree，`git am --3way patches/00*.patch` 全部成功
（2026-11-06 最后一次整列重放；数量以 `ls patches/00*.patch | wc -l` 为准）。重放后的工作树
与分支只差两类内容：这些补丁文件本身（导出提交不入列）、上面 82 个二进制素材——
`git diff --name-only <分支 tip>..HEAD -- . ':!patches'` 只剩 82 个二进制加两个未跟踪交付物。
`TODO.md`、`PRD.md`、`evidence/`、`INSTALL.md` 逐字节一致；交付物脚本
`scripts/download-managed-tools.ps1` 内容一致，只有行尾按 `core.autocrlf` 在检出时转成 CRLF。

## 重新生成

逐提交生成；只改本目录的提交跳过，既改本目录又改跟踪文件的提交按路径过滤只导出跟踪文件：

```bash
OUT=<scratch-dir>; rm -rf "$OUT"; mkdir -p "$OUT"; n=1
for c in $(git rev-list --reverse v0.23.0..HEAD); do
  files=$(git show --name-only --format= "$c" | sed '/^$/d')
  tracked=$(printf '%s\n' "$files" | grep -v '^patches/' || true)
  [ -z "$tracked" ] && continue
  if printf '%s\n' "$files" | grep -q '^patches/'; then
    # shellcheck disable=SC2086
    git format-patch -1 --no-binary --no-signature --base=v0.23.0 --start-number $n -o "$OUT" "$c" -- $tracked >/dev/null
  else
    git format-patch -1 --no-binary --no-signature --base=v0.23.0 --start-number $n -o "$OUT" "$c" >/dev/null
  fi
  n=$((n + 1))
done
```

`--no-binary` 必须显式给出：默认会内嵌二进制，单个补丁可到 4 MB，过不了体积门禁。生成后
仍需剔除 `Binary files ... differ` 所在的整段 `diff --git` 块：

```bash
for f in "$OUT"/*.patch; do
  perl -0777 -i -pe 's/^diff --git[^\n]*\n(?:(?!^diff --git).)*?Binary files [^\n]*differ[^\n]*\n//msg' "$f"
done
```

未跟踪的交付物（`INSTALL.md`、`scripts/download-managed-tools.ps1`）不属任何提交，临时
提交它们、按下一个号导出、再 `git reset --soft HEAD~1` 退回，提交不留在分支上。
