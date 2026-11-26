"""T-05: compaction reloads notes and memory without splitting the conversation.

Only the summary response is simulated; prompt construction, file reads, session
archival and resume use the production implementations and a temporary profile.
"""

from persistence.conversation.messages import NewMessage, TranscriptRead, TranscriptView
from persistence.conversation.sessions import NewSession
from persistence_assembly import open_persistence, profile_scope
from tests.fixtures.storage_read import conversation, transcript_rows
from tools.memory_tool_store import MemoryStore


def test_compaction_refreshes_notes_in_same_persistent_session(tmp_path, monkeypatch):
    from agent.conversation_compression import compress_context
    from agent.conversation_loop import _restore_or_build_system_prompt
    from run_agent import AIAgent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "model:\n  default: test/model\n  provider: custom\n"
        "  base_url: http://127.0.0.1:9/v1\n  context_length: 131072\n",
        encoding="utf-8",
    )
    db = open_persistence(profile_scope(home=tmp_path))
    sid = "continuous-conversation"
    db.sessions.create(NewSession(sid, "cli", model="test/model"))
    for index in range(8):
        db.messages.append(sid, NewMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=f"Historical turn {index}: " + "ordinary conversation " * 100,
        ))
    agent = AIAgent(
        api_key="test-key", base_url="http://127.0.0.1:9/v1", provider="custom",
        model="test/model", quiet_mode=True, enabled_toolsets=[],
        persistence=db, session_id=sid, skip_context_files=True, skip_memory=True,
    )
    try:
        agent._memory_store = MemoryStore()
        agent._memory_store.load_from_disk()
        agent._memory_enabled = agent._user_profile_enabled = True
        _restore_or_build_system_prompt(agent, "", None)
        before_prompt = agent._cached_system_prompt
        writer = MemoryStore()
        writer.load_from_disk()
        assert writer.add("memory", "打包结论：Windows 使用 NSIS 与 portable。 ")["success"]
        note = tmp_path / "notes" / "work" / "packaging.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\nname: 打包方案\ndescription: Windows 安装方案\nupdated: 2026-11-03\n---\n"
            "NOT_FOR_SYSTEM_PROMPT: full note body.\n", encoding="utf-8",
        )
        assert agent._cached_system_prompt == before_prompt
        original = conversation(db, sid)
        historical = transcript_rows(db, sid)

        def summarize(messages, **kwargs):
            return [
                {"role": "user", "content": "[CONTEXT COMPACTION] Packaging decision preserved."},
                {"role": "assistant", "content": "Continue the same conversation."},
            ]

        monkeypatch.setattr(agent.context_compressor, "compress", summarize)
        agent._compression_feasibility_checked = True
        compressed, prompt = compress_context(agent, original, system_message="", approx_tokens=100_000)

        assert agent.session_id == sid
        assert db.sessions.get(sid).end_reason is None
        assert db.sessions.count_sessions() == 1
        assert "打包结论：Windows 使用 NSIS 与 portable。" in prompt
        assert "工作/打包方案 — Windows 安装方案" in prompt
        assert "NOT_FOR_SYSTEM_PROMPT" not in prompt
        restored = conversation(db, sid)
        assert [m["content"] for m in restored] == [m["content"] for m in compressed]
        archived = transcript_rows(db, sid, TranscriptRead(view=TranscriptView.ALL))
        assert all(any(row["id"] == old["id"] and row["content"] == old["content"]
                       for row in archived) for old in historical)
        assert any(not row["active"] for row in archived)
    finally:
        agent.close()
        db.close()
