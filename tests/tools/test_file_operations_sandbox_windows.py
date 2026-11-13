"""File operations under the command sandbox on Windows (``terminal.sandbox.enabled``).

A confined Windows session runs commands under PowerShell, which has none of ``head`` /
``sed`` / ``wc`` / ``base64`` / ``mktemp`` / ``mv``: every file operation must take the
in-process path (``local_command_sandbox.file_tools_native()``) instead of the POSIX shell
one. These tests pin the contract — the native path must answer exactly like the shell path
(line-number format, binary refusal text, offset/limit truncation) — and prove no shell
command is issued at all.

The patch goes on the module attribute (``local_command_sandbox.file_tools_native``),
which is the object production reads at call time.
"""

import os

import pytest

from tools.environments import local_command_sandbox
from tools.file_operations import ShellFileOperations
from tools.file_operations_common import ExecuteResult

pytestmark = pytest.mark.platforms("windows")


class ConfinedEnv:
    """A confined Windows backend: files are the host's, and any command sent to the shell
    fails the way PowerShell fails a POSIX builtin (exit 127). ``commands`` records what was
    sent, so a test can assert the shell was never touched."""

    is_local = True
    uses_host_filesystem = True

    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.env = dict(os.environ)
        self.commands = []

    def execute(self, command, cwd=None, **kwargs):
        self.commands.append(command)
        return {"output": "powershell: the term is not recognized", "returncode": 127}


@pytest.fixture()
def gate_native(monkeypatch):
    """The gate on: this session is confined, so the file tools go in-process."""
    monkeypatch.setattr(local_command_sandbox, "file_tools_native", lambda: True)


@pytest.fixture()
def ops(tmp_path):
    env = ConfinedEnv(tmp_path)
    return ShellFileOperations(env, cwd=str(tmp_path))


def _write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def test_gate_on_takes_the_native_path(gate_native, ops):
    assert ops._native_read_enabled() is True


def test_gate_off_keeps_the_shell_path(monkeypatch, tmp_path):
    monkeypatch.setattr(local_command_sandbox, "file_tools_native", lambda: False)
    env = ConfinedEnv(tmp_path)
    ops = ShellFileOperations(env, cwd=str(tmp_path))

    assert ops._native_read_enabled() is False
    target = tmp_path / "doomed.txt"
    target.write_text("bye", encoding="utf-8")
    ops.delete_file(str(target))
    assert env.commands, "gate off must keep Delete on the backend's python snippet"


def test_native_off_switch_outranks_the_gate(gate_native, monkeypatch, ops):
    monkeypatch.setenv("HERMES_NATIVE_FILE_READ", "0")

    assert ops._native_read_enabled() is False


def test_read_crlf_keeps_carriage_returns(gate_native, ops, tmp_path):
    """The shell path prints ``sed`` lines verbatim, so a CRLF file keeps its ``\\r`` in the
    page and counts three lines (``wc -l``)."""
    path = tmp_path / "crlf.txt"
    _write_bytes(path, b"alpha\r\nbeta\r\ngamma\r\n")

    result = ops.read_file(str(path))

    assert result.error is None
    assert result.content == "1|alpha\r\n2|beta\r\n3|gamma\r"
    assert result.total_lines == 3
    assert result.file_size == 20
    assert result.truncated is False
    assert ops.env.commands == []


def test_read_strips_a_utf8_bom(gate_native, ops, tmp_path):
    path = tmp_path / "bom.txt"
    _write_bytes(path, b"\xef\xbb\xbfone\ntwo\n")

    result = ops.read_file(str(path))

    assert result.error is None
    assert result.content == "1|one\n2|two"
    assert result.total_lines == 2


def test_read_refuses_binary_content(gate_native, ops, tmp_path):
    """A non-image binary: same byte-layer refusal (and same wording) as the shell path."""
    path = tmp_path / "blob.dat"
    _write_bytes(path, b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

    result = ops.read_file(str(path))

    assert result.is_binary is True
    assert result.error == "Binary file (PNG image data, 48 bytes) — cannot display as text."
    assert ops.env.commands == []


def test_read_honours_offset_and_limit(gate_native, ops, tmp_path):
    path = tmp_path / "lines.txt"
    _write_bytes(path, b"one\ntwo\nthree\nfour\n")

    result = ops.read_file(str(path), offset=2, limit=2)

    assert result.error is None
    assert result.content == "2|two\n3|three"
    assert result.total_lines == 4
    assert result.truncated is True
    assert result.hint == "Use offset=4 to continue reading (showing 2-3 of 4 lines)"


def test_read_accepts_msys_paths(gate_native, ops, tmp_path):
    """The model hands over ``/c/Users/x``; the in-process open needs ``C:\\Users\\x``."""
    path = tmp_path / "msys.txt"
    _write_bytes(path, b"here\n")
    msys = "/" + str(tmp_path)[0].lower() + str(tmp_path)[2:].replace("\\", "/")

    result = ops.read_file(msys + "/msys.txt")

    assert result.error is None
    assert result.content == "1|here"


def test_read_missing_file_reports_not_found(gate_native, ops, tmp_path):
    result = ops.read_file(str(tmp_path / "absent.txt"))

    assert result.not_found is True


def test_write_is_atomic_and_verified(gate_native, ops, tmp_path):
    path = tmp_path / "nested" / "note.txt"

    result = ops.write_file(str(path), "hello\nworld\n")

    assert result.error is None
    assert result.verified is True
    assert result.dirs_created is True
    assert path.read_bytes() == b"hello\nworld\n"
    assert ops.env.commands == []
    assert not list(path.parent.glob(".hermes-tmp.*")), "the temp file must not survive"


def test_write_keeps_a_bom_and_crlf(gate_native, ops, tmp_path):
    path = tmp_path / "keep.txt"
    _write_bytes(path, b"\xef\xbb\xbfone\r\ntwo\r\n")

    result = ops.write_file(str(path), "one\ntwo\nthree\n")

    assert result.error is None
    assert result.verified is True
    assert path.read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"


def test_patch_replace_persists(gate_native, ops, tmp_path):
    path = tmp_path / "patch.txt"
    _write_bytes(path, b"alpha\nbeta\ngamma\n")

    result = ops.patch_replace(str(path), "beta", "delta")

    assert result.error is None
    assert result.success is True
    assert path.read_bytes() == b"alpha\ndelta\ngamma\n"
    assert ops.env.commands == []


def test_patch_v4a_persists(gate_native, ops, tmp_path):
    path = tmp_path / "v4a.txt"
    _write_bytes(path, b"alpha\nbeta\n")
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {path.as_posix()}\n"
        "@@\n"
        "-beta\n"
        "+gamma\n"
        "*** End Patch\n"
    )

    result = ops.patch_v4a(patch)

    assert result.error is None
    assert path.read_bytes() == b"alpha\ngamma\n"
    assert ops.env.commands == []


def test_delete_removes_the_file(gate_native, ops, tmp_path):
    path = tmp_path / "doomed.txt"
    path.write_text("bye", encoding="utf-8")

    result = ops.delete_file(str(path))

    assert result.error is None
    assert not path.exists()
    assert ops.env.commands == []


def test_delete_refuses_a_directory(gate_native, ops, tmp_path):
    target = tmp_path / "dir"
    target.mkdir()
    (target / "keep.txt").write_text("still here", encoding="utf-8")

    result = ops.delete_file(str(target))

    assert result.error is not None
    assert "is a directory" in result.error
    assert target.exists()


def test_delete_of_a_missing_file_is_a_no_op(gate_native, ops, tmp_path):
    """Same contract as the python snippet: ``FileNotFoundError`` is swallowed."""
    assert ops.delete_file(str(tmp_path / "gone.txt")).error is None


def test_move_moves_the_file(gate_native, ops, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "dst.txt"

    result = ops.move_file(str(src), str(dst))

    assert result.error is None
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "payload"
    assert ops.env.commands == []


def test_move_into_an_existing_directory(gate_native, ops, tmp_path):
    """``mv src dir/`` puts the file inside ``dir``; ``os.replace`` alone would fail."""
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    holder = tmp_path / "holder"
    holder.mkdir()

    result = ops.move_file(str(src), str(holder))

    assert result.error is None
    assert (holder / "src.txt").read_text(encoding="utf-8") == "payload"


def test_move_reports_a_missing_parent(gate_native, ops, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")

    result = ops.move_file(str(src), str(tmp_path / "nowhere" / "dst.txt"))

    assert result.error is not None
    assert result.error.startswith("Failed to move ")
    assert src.exists()


def _pin_rg(ops, monkeypatch):
    """Pin rg resolution so the search tests never probe the shell for it."""
    monkeypatch.setattr(ops, "_rg_resolution_cache", {"rg": "/fake/rg"})
    monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd == "rg")


def test_filename_search_takes_the_native_rg_lane(gate_native, ops, tmp_path, monkeypatch):
    """``rg --files`` runs as a process of its own and its output is parsed as usual; the
    POSIX ``rg --files … | head`` pipeline (which PowerShell cannot run) is never built."""
    (tmp_path / "needle.txt").write_text("x", encoding="utf-8")
    _pin_rg(ops, monkeypatch)
    calls = []

    def _native(argv, fetch_limit, timeout, merge_stderr=False):
        calls.append(argv)
        return ExecuteResult(stdout=f"{tmp_path.as_posix()}/needle.txt\n", exit_code=0)

    monkeypatch.setattr(ops, "_run_rg_native", _native)

    result = ops.search("needle*", path=str(tmp_path), target="files")

    assert result.error is None
    assert [os.path.basename(p.replace("\\", "/")) for p in result.files] == ["needle.txt"]
    assert calls, "the native rg lane must be taken"
    assert ops.env.commands == []


def test_filename_search_without_rg_names_the_requirement(gate_native, ops, tmp_path, monkeypatch):
    """No ripgrep and no POSIX ``find``: an explicit error, never an empty tree."""
    (tmp_path / "needle.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ops, "_rg_resolution_cache", {})
    monkeypatch.setattr(ops, "_has_command", lambda cmd: False)

    result = ops.search("needle*", path=str(tmp_path), target="files")

    assert result.error is not None
    assert result.error.startswith("sandboxed Windows file search needs ripgrep")
    assert result.files == []
    assert ops.env.commands == []


def test_content_search_without_rg_names_the_requirement(gate_native, ops, tmp_path, monkeypatch):
    (tmp_path / "needle.txt").write_text("needle", encoding="utf-8")
    monkeypatch.setattr(ops, "_rg_resolution_cache", {})
    monkeypatch.setattr(ops, "_has_command", lambda cmd: False)

    result = ops.search("needle", path=str(tmp_path))

    assert result.error is not None
    assert result.error.startswith("sandboxed Windows file search needs ripgrep")
