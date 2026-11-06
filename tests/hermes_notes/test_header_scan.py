"""PRD §7.4: indexing reads front matter once, independently of note bodies."""

from pathlib import Path

import pytest

from hermes_notes import NotesStore


class _ReadReceipt:
    def __init__(self, stream, chunks):
        self.stream = stream
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, *args):
        chunk = self.stream.read(*args)
        self.chunks.append(chunk)
        return chunk

    def readline(self, *args):
        chunk = self.stream.readline(*args)
        self.chunks.append(chunk)
        return chunk

    def __iter__(self):
        while chunk := self.readline():
            yield chunk


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_index_reads_only_front_matter_once(tmp_path, monkeypatch, newline):
    store = NotesStore(tmp_path)
    note = store.note_path("work", "release")
    note.parent.mkdir(parents=True)
    header = newline.join([
        "---", "name: 发布计划", "description: 本月发布的最终结论",
        "updated: 2026-11-03", "---", "",
    ]).encode()
    note.write_bytes(header + "正文无需读取。\n".encode() * 100_000)
    chunks = []
    open_file = Path.open

    def record_open(path, *args, **kwargs):
        stream = open_file(path, *args, **kwargs)
        return _ReadReceipt(stream, chunks) if path == note else stream

    monkeypatch.setattr(Path, "open", record_open)

    assert "工作/发布计划 — 本月发布的最终结论" in store.render_index()
    consumed = b"".join(chunk.encode("utf-8") if isinstance(chunk, str) else chunk for chunk in chunks)
    assert consumed.replace(b"\r\n", b"\n") == header.replace(b"\r\n", b"\n")


def test_index_accepts_bom_header_without_decoding_body(tmp_path):
    store = NotesStore(tmp_path)
    note = store.note_path("study", "book")
    note.parent.mkdir(parents=True)
    note.write_bytes("---\nname: 读书\ndescription: 阅读结论\n---\n".encode("utf-8-sig") + b"\xff\xfe")

    assert "学习/读书 — 阅读结论" in store.render_index()
