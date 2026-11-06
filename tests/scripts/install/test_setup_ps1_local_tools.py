"""Windows 开发初始化复用本机工具，重复执行不重新下载基础环境。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.platforms("windows")
def test_setup_reuses_local_uv_and_python_on_repeated_runs(tmp_path):
    root = Path(__file__).resolve().parents[3]
    powershell = shutil.which("powershell.exe")
    assert powershell
    repo = tmp_path / "checkout with spaces"
    package = repo / "pm"
    package.mkdir(parents=True)
    shutil.copyfile(root / "setup-hermes.ps1", repo / "setup-hermes.ps1")
    from pm.store import current_target

    target = current_target()
    (package / "lock.json").write_text(json.dumps({"packages": {
        "uv": {"version": "0.13.5", "artifacts": {target: {"url": "https://invalid.test/never", "sha256": "0" * 64}}},
        "python": {"version": f"{sys.version_info.major}.{sys.version_info.minor}.99"},
    }}), encoding="utf-8")
    (package / "__init__.py").touch()
    (package / "cli.py").write_text(
        "import os,pathlib,sys\n"
        "assert sys.prefix == sys.base_prefix\n"
        "assert pathlib.Path(os.environ['LOCAL_BIN']) in map(pathlib.Path, os.environ['PATH'].split(os.pathsep))\n"
        "with open(os.environ['PM_RECEIPT'], 'a') as f: f.write('installed\\n')\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "local tools"
    bin_dir.mkdir()
    probe = bin_dir / "uv-probe.py"
    probe.write_text(
        "import json,os,sys\n"
        "with open(os.environ['UV_RECEIPT'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1:] == ['--version']: print('uv 0.13.5')\n"
        "elif sys.argv[1:3] == ['python','find'] and '--no-managed-python' in sys.argv: print(sys._base_executable)\n"
        "else: sys.exit('unexpected download or managed lookup')\n", encoding="utf-8",
    )
    (bin_dir / "uv.cmd").write_text(f'@"{sys._base_executable}" "{probe}" %*\r\n', encoding="utf-8")
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
               LOCAL_BIN=str(bin_dir), HERMES_RUNTIME_DIR=str(tmp_path / "tools"),
               HERMES_HOME=str(tmp_path / "profile"), UV_RECEIPT=str(tmp_path / "uv.jsonl"),
               PM_RECEIPT=str(tmp_path / "pm.txt"))
    for _ in range(2):
        run = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(repo / "setup-hermes.ps1"), "-RuntimeOnly"],
                             cwd=repo, env=env, capture_output=True, text=True, timeout=30, check=False)
        assert run.returncode == 0, run.stdout + run.stderr
    calls = [json.loads(line) for line in Path(env["UV_RECEIPT"]).read_text().splitlines()]
    assert sum(call[:2] == ["python", "find"] for call in calls) == 2
    assert all(call == ["--version"] or "--no-managed-python" in call for call in calls)
    assert Path(env["PM_RECEIPT"]).read_text().splitlines() == ["installed", "installed"]
    assert not Path(env["HERMES_RUNTIME_DIR"]).exists()
