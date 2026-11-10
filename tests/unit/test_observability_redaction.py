"""P7 M1：脱敏红线——secret 键、过长文本、本地路径不得明文入库形态。"""

from __future__ import annotations

from stableagent.observability.redaction import REDACTED, redact_mapping


def test_secret_keys_are_redacted() -> None:
    raw = {
        "api_key": "sk-live-abc",
        "Authorization": "Bearer xyz",
        "nested": {"github_token": "ghp_secret", "ok": "value"},
    }
    out = redact_mapping(raw)
    assert out["api_key"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["nested"]["github_token"] == REDACTED
    assert out["nested"]["ok"] == "value"


def test_long_strings_are_truncated() -> None:
    out = redact_mapping({"body": "x" * 500}, max_chars=40)
    assert len(out["body"]) == 40
    assert out["body"].endswith("…")


def test_lists_are_redacted_recursively() -> None:
    out = redact_mapping({"items": [{"token": "t"}, "ok"]})
    assert out["items"][0]["token"] == REDACTED
    assert out["items"][1] == "ok"


def test_home_paths_are_redacted() -> None:
    out = redact_mapping({"cwd": "worktree at /Users/alice/code/repo"})
    assert "[REDACTED_PATH]" in out["cwd"]
    assert "alice" not in out["cwd"]
