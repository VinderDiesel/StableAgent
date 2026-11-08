"""GitHub Source 契约：把只读轮询结果标准化为 Trigger / Thread / Channel（规格 WP-6.2）。

方向锁：本模块只 import `integrations` 与 `supervisor.model`；读取只产生候选事件，
Channel 只是回写目的地（ADR-06 决策 8），不携带授权。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.integrations.github import GitHubReadClient, GitHubReadItem
from stableagent.orchestration.model import Clock
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore


class SourceError(Exception):
    """Source 层稳定错误：四分类决定 cursor health 与是否可重试。"""

    code = "source.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class SourceTransientError(SourceError):
    """网络抖动、限流、5xx：cursor 记 degraded，下一轮可重试。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.transient", message)


class SourceAuthError(SourceError):
    """403：读权限不足或 token 失效，需要人工处理。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.auth", message)


class SourceGoneError(SourceError):
    """404：仓库不可见或事件已不存在，继续轮询不会恢复。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.gone", message)


class SourceSchemaError(SourceError):
    """载荷形状不合约：整批不落库，避免写进半个事实。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.schema", message)


class SourceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread
    channel: Channel | None = None


class PollBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[SourceEvent] = Field(default_factory=list)
    next_cursor: str | None = None
    duplicates: int = Field(default=0, ge=0)


@runtime_checkable
class Source(Protocol):
    async def poll(self, *, limit: int = 100) -> PollBatch: ...


@runtime_checkable
class ChannelWriter(Protocol):
    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]: ...


class FakeChannelWriter:
    """记录 `(channel, body)`；`fail_next` 抛一次后清空。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Channel, str]] = []
        self.fail_next: Exception | None = None

    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]:
        self.calls.append((channel, body))
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        return {"id": f"fake-{len(self.calls)}"}


def stable_trigger_id(external_id: str) -> str:
    return "tg-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]


def stable_channel_id(thread_key: str, kind: ChannelKind, external_ref: str) -> str:
    seed = f"{thread_key}|{kind.value}|{external_ref}"
    return "ch-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def issue_thread_key(repository: str, number: int) -> str:
    return f"issue:{repository}#{number}"


def pull_request_thread_key(repository: str, number: int) -> str:
    return f"pr:{repository}#{number}"


def review_thread_key(repository: str, pull_number: int, review_id: int) -> str:
    return f"review:{repository}#{pull_number}:{review_id}"


def repository_thread_key(repository: str) -> str:
    return f"repo:{repository}"


class GitHubSource:
    """单仓库只读 Source：`normalize` 无副作用；写库只发生在 `poll`。"""

    def __init__(
        self,
        store: SupervisorStore,
        client: GitHubReadClient,
        *,
        repository: str,
        bot_login: str,
        clock: Clock,
    ) -> None:
        self._store = store
        self._client = client
        self._repository = repository
        self._bot_login = bot_login
        self._clock = clock

    def normalize(self, item: GitHubReadItem) -> SourceEvent | None:
        kind = TriggerKind(item.kind.value)
        if kind is TriggerKind.ISSUE:
            return self._issue(item)
        if kind is TriggerKind.PULL_REQUEST:
            return self._pull_request(item)
        if kind is TriggerKind.ISSUE_COMMENT:
            return self._issue_comment(item)
        if kind is TriggerKind.REVIEW_COMMENT:
            return self._review_comment(item)
        if kind is TriggerKind.REVIEW:
            return self._review(item)
        if kind is TriggerKind.CI_STATUS:
            return self._ci_status(item)
        raise SourceSchemaError(f"unsupported kind {item.kind.value}")

    def _issue(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = issue_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.ISSUE,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _pull_request(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        number = _int_field(resource, "number")
        key = pull_request_thread_key(self._repository, number)
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.PULL_REQUEST, number, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{number}",
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
        )

    def _trigger(
        self,
        item: GitHubReadItem,
        kind: TriggerKind,
        subject: int,
        actor: str,
        thread_key: str,
    ) -> Trigger:
        external_id = f"github:{self._repository}:{kind.value}:{subject}"
        return Trigger(
            id=stable_trigger_id(external_id),
            external_id=external_id,
            kind=kind,
            actor=actor,
            thread_keys=[thread_key],
            occurred_at=item.occurred_at,
            received_at=self._clock.now_ms(),
            payload={"resource": item.resource, "deleted": item.deleted},
        )

    def _is_self(self, actor: str) -> bool:
        return actor.casefold() == self._bot_login.casefold()

    def _issue_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        number = _int_field(resource, "issue_number")
        if bool(resource.get("is_pull_request")):
            key = pull_request_thread_key(self._repository, number)
            thread_kind = ThreadKind.PULL_REQUEST
            channel_kind = ChannelKind.PULL_REQUEST_COMMENT
        else:
            key = issue_thread_key(self._repository, number)
            thread_kind = ThreadKind.ISSUE
            channel_kind = ChannelKind.ISSUE_COMMENT
        external_ref = f"#{number}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.ISSUE_COMMENT, comment_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=thread_kind,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, channel_kind, external_ref),
                thread_key=key,
                kind=channel_kind,
                external_ref=external_ref,
            ),
        )

    def _review_comment(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        comment_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        review_id = _int_field(resource, "review_id")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.REVIEW_COMMENT, comment_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )

    def _review(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = _actor(resource)
        if self._is_self(actor):
            return None
        review_id = _int_field(resource, "id")
        pull_number = _int_field(resource, "pull_number")
        key = review_thread_key(self._repository, pull_number, review_id)
        external_ref = f"review:{review_id}"
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.REVIEW, review_id, actor, key),
            thread=Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REVIEW,
                external_ref=external_ref,
                last_trigger_at=item.occurred_at,
                status=_thread_status(item, resource, closed_state="dismissed"),
            ),
            channel=Channel(
                id=stable_channel_id(key, ChannelKind.REVIEW_REPLY, external_ref),
                thread_key=key,
                kind=ChannelKind.REVIEW_REPLY,
                external_ref=external_ref,
            ),
        )

    def _ci_status(self, item: GitHubReadItem) -> SourceEvent | None:
        resource = item.resource
        actor = resource.get("actor", "github")
        if not isinstance(actor, str) or not actor:
            raise SourceSchemaError("actor 必须是非空字符串")
        if self._is_self(actor):
            return None
        status_id = _int_field(resource, "id")
        _str_field(resource, "sha")
        state = _str_field(resource, "state")
        if state not in {"pending", "success", "failure", "error"}:
            raise SourceSchemaError(f"未知 CI state：{state}")
        pull_number = resource.get("pull_number")
        if pull_number is not None and (
            not isinstance(pull_number, int) or isinstance(pull_number, bool)
        ):
            raise SourceSchemaError("pull_number 必须是整数或 null")
        if pull_number is not None:
            key = pull_request_thread_key(self._repository, pull_number)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.PULL_REQUEST,
                external_ref=f"#{pull_number}",
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        else:
            key = repository_thread_key(self._repository)
            thread = Thread(
                key=key,
                repository=self._repository,
                kind=ThreadKind.REPOSITORY,
                external_ref=self._repository,
                last_trigger_at=item.occurred_at,
                status=ThreadStatus.OPEN,
            )
        return SourceEvent(
            trigger=self._trigger(item, TriggerKind.CI_STATUS, status_id, actor, key),
            thread=thread,
        )


def _int_field(resource: dict[str, Any], field: str) -> int:
    value = resource.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceSchemaError(f"{field} 必须是整数")
    return value


def _str_field(resource: dict[str, Any], field: str) -> str:
    value = resource.get(field)
    if not isinstance(value, str) or not value:
        raise SourceSchemaError(f"{field} 必须是非空字符串")
    return value


def _actor(resource: dict[str, Any]) -> str:
    user = resource.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if isinstance(login, str) and login:
        return login
    actor = resource.get("actor")
    if isinstance(actor, str) and actor:
        return actor
    raise SourceSchemaError("缺少 actor / user.login")


def _thread_status(
    item: GitHubReadItem, resource: dict[str, Any], *, closed_state: str = "closed"
) -> ThreadStatus:
    if item.deleted or resource.get("state") == closed_state:
        return ThreadStatus.CLOSED
    return ThreadStatus.OPEN
