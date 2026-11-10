"""多 Source 实现包（P7 M3）：GitHub / Slack。契约在 ``supervisor.source``。"""

from __future__ import annotations

from typing import Any

__all__ = ["GitHubSource", "SlackFakeSource"]


def __getattr__(name: str) -> Any:
    if name == "GitHubSource":
        from stableagent.supervisor.sources.github import GitHubSource

        return GitHubSource
    if name == "SlackFakeSource":
        from stableagent.supervisor.sources.slack import SlackFakeSource

        return SlackFakeSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
