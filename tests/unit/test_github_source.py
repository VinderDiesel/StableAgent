"""WP-6.2：只读轮询到 Trigger/Thread/Channel 的确定性映射。"""

from __future__ import annotations

import contextlib
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
    SourceHealth,
    ThreadKind,
    ThreadStatus,
    TriggerKind,
)
from stableagent.supervisor.source import (
    FakeChannelWriter,
    GitHubSource,
    Source,
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


def test_issue_comment_maps_to_issue_thread_and_channel(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 11, "issue_number": 7, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "issue:owner/repo#7"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.ISSUE_COMMENT
    assert event.channel.external_ref == "#7"
    assert event.channel.thread_key == event.thread.key
    assert event.trigger.kind is TriggerKind.ISSUE_COMMENT
    assert event.trigger.external_id == "github:owner/repo:issue_comment:11"


def test_comment_on_pull_request_uses_the_pr_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {
                "id": 12,
                "issue_number": 9,
                "is_pull_request": True,
                "user": {"login": "alice"},
            },
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.PULL_REQUEST_COMMENT


def test_review_comment_creates_review_thread_and_reply_channel(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {"id": 13, "pull_number": 9, "review_id": 42, "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.key == "review:owner/repo#9:42"
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.external_ref == "review:42"
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY
    assert event.channel.external_ref == "review:42"
    assert event.trigger.kind is TriggerKind.REVIEW_COMMENT
    assert event.trigger.external_id == "github:owner/repo:review_comment:13"


def test_review_submission_maps_to_an_open_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "submitted", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.kind is ThreadKind.REVIEW
    assert event.thread.status is ThreadStatus.OPEN
    assert event.trigger.kind is TriggerKind.REVIEW
    assert event.channel is not None
    assert event.channel.kind is ChannelKind.REVIEW_REPLY


def test_dismissed_review_closes_the_review_thread(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW,
            {"id": 42, "pull_number": 9, "state": "dismissed", "user": {"login": "alice"}},
        )
    )
    assert event is not None
    assert event.thread.status is ThreadStatus.CLOSED


def test_bot_review_comment_is_not_a_candidate(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.REVIEW_COMMENT,
            {
                "id": 13,
                "pull_number": 9,
                "review_id": 42,
                "user": {"login": "sa-agent[bot]"},
            },
        )
    )
    assert event is None


def test_review_comment_without_review_id_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError, match="review_id"):
        source.normalize(
            _read_item(
                GitHubReadKind.REVIEW_COMMENT,
                {"id": 13, "pull_number": 9, "user": {"login": "alice"}},
            )
        )


def test_ci_status_with_pull_request_attaches_to_the_pr_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 501, "sha": "abc123", "state": "success", "pull_number": 9},
        )
    )
    assert event is not None
    assert event.thread.key == "pr:owner/repo#9"
    assert event.thread.kind is ThreadKind.PULL_REQUEST
    assert event.trigger.kind is TriggerKind.CI_STATUS
    assert event.trigger.external_id == "github:owner/repo:ci_status:501"
    assert event.trigger.actor == "github"
    assert event.channel is None


def test_ci_status_without_pull_request_uses_the_repository_thread(
    tmp_path: pathlib.Path,
) -> None:
    source, _, _ = _source(tmp_path)
    event = source.normalize(
        _read_item(
            GitHubReadKind.CI_STATUS,
            {"id": 502, "sha": "abc123", "state": "failure"},
        )
    )
    assert event is not None
    assert event.thread.key == "repo:owner/repo"
    assert event.thread.kind is ThreadKind.REPOSITORY
    assert event.thread.external_ref == "owner/repo"
    assert event.channel is None


def test_unknown_ci_state_is_a_schema_error(tmp_path: pathlib.Path) -> None:
    source, _, _ = _source(tmp_path)
    with pytest.raises(SourceSchemaError, match="CI state"):
        source.normalize(
            _read_item(
                GitHubReadKind.CI_STATUS,
                {"id": 503, "sha": "abc123", "state": "exploded"},
            )
        )


def _poll_source(
    tmp_path: pathlib.Path,
    items: list[GitHubReadItem],
    *,
    page_size: int | None = None,
):
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    client = FakeGitHubReadClient(items, page_size=page_size)
    source = GitHubSource(
        store, client, repository="owner/repo", bot_login="sa-agent[bot]", clock=clock
    )
    return source, store, client


async def test_poll_pages_sorts_dedupes_and_writes_in_one_transaction(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            occurred_at=100,
        ),
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 2, "state": "open", "user": {"login": "alice"}},
            occurred_at=200,
        ),
    ]
    source, store, client = _poll_source(tmp_path, items, page_size=2)
    assert isinstance(source, Source)

    batch = await source.poll(limit=10)

    assert [event.thread.external_ref for event in batch.events] == ["#1", "#2"]
    assert batch.duplicates == 1
    assert batch.next_cursor is None
    assert [call["cursor"] for call in client.calls] == [None, "offset:2"]
    assert store.get_trigger_by_external("github", "github:owner/repo:issue:1") is not None
    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.cursor is None
    assert cursor.health is SourceHealth.OK
    assert cursor.consecutive_failures == 0


async def test_second_poll_skips_triggers_already_in_the_store(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        )
    ]
    source, _, _ = _poll_source(tmp_path, items)
    assert len((await source.poll(limit=10)).events) == 1

    again = await source.poll(limit=10)

    assert again.events == []
    assert again.duplicates == 1


async def test_poll_stops_at_max_pages_and_resumes_from_cursor(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": number, "state": "open", "user": {"login": "alice"}},
            occurred_at=number,
        )
        for number in (1, 2, 3)
    ]
    source, store, _ = _poll_source(tmp_path, items, page_size=1)

    first = await source.poll(limit=10, max_pages=1)
    assert [event.thread.external_ref for event in first.events] == ["#1"]
    assert first.next_cursor == "offset:1"
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor == "offset:1"

    second = await source.poll(limit=10, max_pages=2)
    assert [event.thread.external_ref for event in second.events] == ["#2", "#3"]
    assert second.next_cursor is None
    stored = store.get_source_cursor("github")
    assert stored is not None and stored.cursor is None


@pytest.mark.parametrize(
    ("error", "health"),
    [
        (SourceTransientError("503"), SourceHealth.DEGRADED),
        (SourceAuthError("403"), SourceHealth.FAILED),
        (SourceGoneError("404"), SourceHealth.FAILED),
        (SourceSchemaError("bad payload"), SourceHealth.FAILED),
    ],
)
async def test_poll_records_cursor_health_before_reraising(
    tmp_path: pathlib.Path, error: Exception, health: SourceHealth
) -> None:
    source, store, client = _poll_source(tmp_path, [])
    client.fail_next = error

    with pytest.raises(type(error)):
        await source.poll(limit=10)

    cursor = store.get_source_cursor("github")
    assert cursor is not None
    assert cursor.health is health
    assert cursor.consecutive_failures == 1


async def test_malformed_item_fails_the_whole_batch_without_writing(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
        ),
        _read_item(GitHubReadKind.ISSUE, {"state": "open", "user": {"login": "alice"}}),
    ]
    source, store, _ = _poll_source(tmp_path, items)

    with pytest.raises(SourceSchemaError):
        await source.poll(limit=10)

    assert store.list_triggers() == []
    assert store.list_threads() == []
    cursor = store.get_source_cursor("github")
    assert cursor is not None and cursor.health is SourceHealth.FAILED


async def test_deleted_event_closes_the_thread_and_still_records_the_trigger(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE,
            {"number": 1, "state": "open", "user": {"login": "alice"}},
            deleted=True,
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert len(batch.events) == 1
    assert batch.events[0].thread.status is ThreadStatus.CLOSED
    thread = store.get_thread("issue:owner/repo#1")
    assert thread is not None and thread.status is ThreadStatus.CLOSED


async def test_poll_drops_our_own_bot_events_but_still_advances(
    tmp_path: pathlib.Path,
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "sa-agent[bot]"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)

    batch = await source.poll(limit=10)

    assert batch.events == []
    assert batch.duplicates == 0
    assert store.get_source_cursor("github") is not None


async def test_poll_writes_state_in_a_single_transaction(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        _read_item(
            GitHubReadKind.ISSUE_COMMENT,
            {"id": 1, "issue_number": 7, "user": {"login": "alice"}},
        )
    ]
    source, store, _ = _poll_source(tmp_path, items)
    seen: list[bool] = []
    original = store.transaction

    @contextlib.contextmanager
    def watched():
        with original():
            seen.append(store.in_transaction)
            yield

    monkeypatch.setattr(store, "transaction", watched)

    await source.poll(limit=10)

    assert seen == [True]
