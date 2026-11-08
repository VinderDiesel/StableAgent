"""WP-6.2：只读轮询到 Trigger/Thread/Channel 的确定性映射。"""

from __future__ import annotations

import pathlib

import pytest

from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
    GitHubReadPage,
)
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    ThreadKind,
    ThreadStatus,
    TriggerKind,
)
from stableagent.supervisor.source import (
    FakeChannelWriter,
    GitHubSource,
    SourceAuthError,
    SourceGoneError,
    SourceSchemaError,
    SourceTransientError,
    issue_thread_key,
    pull_request_thread_key,
    repository_thread_key,
    review_thread_key,
    stable_channel_id,
    stable_trigger_id,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


def _item(number: int = 1, *, occurred_at: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE, occurred_at=occurred_at, resource={"number": number}
    )


async def test_read_fake_pages_with_offset_cursors_and_records_calls() -> None:
    client = FakeGitHubReadClient([_item(1), _item(2), _item(3)], page_size=2)
    assert isinstance(client, GitHubReadClient)

    first = await client.list_events(repository="owner/repo", cursor=None, limit=10)
    second = await client.list_events(repository="owner/repo", cursor=first.next_cursor, limit=10)

    assert [item.resource["number"] for item in first.items] == [1, 2]
    assert first.next_cursor == "offset:2"
    assert [item.resource["number"] for item in second.items] == [3]
    assert second.next_cursor is None
    assert client.calls == [
        {"repository": "owner/repo", "cursor": None, "limit": 10},
        {"repository": "owner/repo", "cursor": "offset:2", "limit": 10},
    ]


async def test_read_fake_fail_next_raises_once_then_clears() -> None:
    client = FakeGitHubReadClient([])
    client.fail_next = SourceTransientError("503")
    with pytest.raises(SourceTransientError):
        await client.list_events(repository="owner/repo", cursor=None, limit=1)
    empty = await client.list_events(repository="owner/repo", cursor=None, limit=1)
    assert empty == GitHubReadPage()
    assert client.fail_next is None


def test_source_errors_carry_stable_codes() -> None:
    assert SourceTransientError("503").code == "source.transient"
    assert SourceAuthError("403").code == "source.auth"
    assert SourceGoneError("404").code == "source.gone"
    assert SourceSchemaError("bad").code == "source.schema"


async def test_channel_writer_fake_records_and_fails_once() -> None:
    writer = FakeChannelWriter()
    channel = Channel(
        id="ch-1",
        thread_key="issue:owner/repo#1",
        kind=ChannelKind.ISSUE_COMMENT,
        external_ref="#1",
    )
    assert await writer.write(channel=channel, body="hi") == {"id": "fake-1"}
    writer.fail_next = SourceTransientError("boom")
    with pytest.raises(SourceTransientError):
        await writer.write(channel=channel, body="again")
    assert [body for _, body in writer.calls] == ["hi", "again"]
    assert writer.fail_next is None


def test_thread_keys_and_ids_are_stable() -> None:
    assert issue_thread_key("owner/repo", 7) == "issue:owner/repo#7"
    assert pull_request_thread_key("owner/repo", 7) == "pr:owner/repo#7"
    assert review_thread_key("owner/repo", 7, 42) == "review:owner/repo#7:42"
    assert repository_thread_key("owner/repo") == "repo:owner/repo"
    assert stable_trigger_id("github:owner/repo:issue:7") == stable_trigger_id(
        "github:owner/repo:issue:7"
    )
    assert stable_trigger_id("a") != stable_trigger_id("b")
    assert stable_trigger_id("a").startswith("tg-")
    first = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    second = stable_channel_id("issue:owner/repo#7", ChannelKind.ISSUE_COMMENT, "#7")
    assert first == second and first.startswith("ch-")


def _source(tmp_path: pathlib.Path):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient([])
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, clock


def _read_item(
    kind: GitHubReadKind,
    resource: dict[str, object],
    *,
    occurred_at: int = 10,
    deleted: bool = False,
) -> GitHubReadItem:
    return GitHubReadItem(kind=kind, occurred_at=occurred_at, resource=resource, deleted=deleted)


def test_issue_normalizes_to_trigger_thread_and_digest(tmp_path: pathlib.Path) -> None:
    source, _, clock = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "octocat"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.thread.external_ref == "#7"
    assert event.thread.kind is ThreadKind.ISSUE
    assert event.thread.status is ThreadStatus.OPEN
    assert event.channel is None
    assert event.trigger.kind is TriggerKind.ISSUE
    assert event.trigger.external_id == "github:owner/repo:issue:7"
    assert event.trigger.id.startswith("tg-")
    assert event.trigger.thread_keys == ["issue:owner/repo#7"]
    assert event.trigger.actor == "octocat"
    assert event.trigger.occurred_at == 10
    assert event.trigger.received_at == clock.now_ms()
    assert event.trigger.payload == {
        "resource": {"number": 7, "state": "open", "user": {"login": "octocat"}},
        "deleted": False,
    }
    assert event.trigger.digest


def test_closed_pull_request_maps_to_a_closed_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.PULL_REQUEST,
            {"number": 9, "state": "closed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.thread.status is ThreadStatus.CLOSED
    assert event.trigger.kind is TriggerKind.PULL_REQUEST
    assert event.trigger.external_id == "github:owner/repo:pull_request:9"


def test_bot_identity_is_filtered_case_insensitively(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 7, "state": "open", "user": {"login": "SA-Agent[BOT]"}},
        )
    )
    assert event is None


@pytest.mark.parametrize(
    "resource",
    [
        {"state": "open", "user": {"login": "alice"}},
        {"number": "7", "state": "open", "user": {"login": "alice"}},
        {"number": 7, "state": "open"},
    ],
)
def test_malformed_issue_is_a_schema_error(
    tmp_path: pathlib.Path, resource: dict[str, object]
) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError):
        source.normalize(_read_item(GitHubReadKind.ISSUE, resource))
