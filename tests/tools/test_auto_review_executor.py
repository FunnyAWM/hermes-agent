"""The auto-review gate's decision routing (tools.approval mode == "auto")."""

from tools import auto_review as ar


def _decide(monkeypatch, decision, *, mode="auto", approved=False, tool="terminal"):
    calls = {"review": 0, "approval": 0, "reason": None, "command": None, "justification": None}

    def fake_review(tool_name, args, *, messages, cwd):
        calls["review"] += 1
        return decision

    def fake_approval(tool_name, reason, *, rule_key="", approval_callback=None,
                      no_human_block_message=None, command="", justification=""):
        calls["approval"] += 1
        calls["reason"] = reason
        calls["command"] = command
        calls["justification"] = justification
        return {"approved": approved, "message": None if approved else "the user rejected the call"}

    monkeypatch.setattr("tools.approval_context._get_approval_mode", lambda: mode)
    monkeypatch.setattr(ar, "review_tool_call", fake_review)
    monkeypatch.setattr(ar, "should_review", lambda name: name != "read_file")
    monkeypatch.setattr("tools.approval.request_tool_approval", fake_approval)
    body, error_type = ar.gate_tool_call(
        tool, {"command": "rm -rf build"},
        messages=[{"role": "user", "content": "clean the build dir"}], cwd=".",
    )
    return body, error_type, calls


def test_allow_runs_without_a_prompt(monkeypatch):
    body, error_type, calls = _decide(monkeypatch, ar.AutoReviewDecision(ar.ALLOW, "low"))
    assert (body, error_type) == (None, None)
    assert calls["review"] == 1
    assert calls["approval"] == 0
    assert calls["reason"] is None


def test_deny_goes_to_the_human_gate_and_stops_when_rejected(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.DENY, "medium", "pre-existing data"), approved=False,
    )
    assert calls["approval"] == 1
    assert calls["reason"] == "pre-existing data"
    assert error_type == "auto_review_denied"
    assert body["error"] == "auto_review_denied"
    assert "rejected" in body["message"]


def test_escalation_carries_the_call_and_the_reviewer_reason(monkeypatch):
    # The approval card must show what would actually run, not the synthetic
    # "<tool> (plugin approval rule)" label, and the reviewer's reason is the
    # justification the user decides on.
    _body, _error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.DENY, "medium", "This deletes pre-existing build output."),
        approved=False,
    )

    assert calls["command"].startswith('terminal {"command": "rm -rf build"}')
    assert calls["justification"] == "This deletes pre-existing build output."


def test_deny_approved_by_a_human_proceeds(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.DENY, "medium", "needs approval"), approved=True,
    )
    assert (body, error_type) == (None, None)
    assert calls["approval"] == 1


def test_high_risk_never_asks_a_human(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.HIGH_RISK, "high", "exfiltration"),
    )
    assert calls["approval"] == 0
    assert error_type == "auto_review_high_risk"
    assert body["error"] == "auto_review_high_risk"
    assert "Do NOT retry" in body["message"]


def test_unusable_verdict_escalates_and_reports_the_failure(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.FAILED, "unknown", "empty response"), approved=False,
    )
    assert calls["approval"] == 1
    assert "could not produce a valid verdict" in calls["reason"]
    assert error_type == "AUTO_REVIEW_FAILED"
    assert body["error"] == "AUTO_REVIEW_FAILED"


def test_manual_mode_does_not_review(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.DENY, "medium"), mode="manual",
    )
    assert (body, error_type) == (None, None)
    assert calls["review"] == 0


def test_read_only_tools_are_not_reviewed(monkeypatch):
    body, error_type, calls = _decide(
        monkeypatch, ar.AutoReviewDecision(ar.DENY, "medium"), tool="read_file",
    )
    assert (body, error_type) == (None, None)
    assert calls["review"] == 0
