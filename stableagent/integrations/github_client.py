"""GitHub 写客户端：协议、可注入 fake 与 HTTP 错误三分类（步骤 3.9）。

分类只在 `classify_github_error` 一处：effect handler 按 `category` 决定退避重试还是永久失败，
按 `status_code` 填 Report details——状态码绝不吞掉。凭据不是协议参数（L7）：调用方持有的
token 只存在于真实实现内部，fake 与 payload 都不接收它。

方向锁：本模块只依赖 `integrations.errors`，不 import control / execution / orchestration。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from stableagent.integrations.errors import GitHubApiError

RETRYABLE_STATUS_CODES = frozenset({408, 429})
NEEDS_HUMAN_STATUS_CODES = frozenset({401, 403})


def classify_github_error(status_code: int | None, message: str) -> GitHubApiError:
    """三分类：无状态码（网络）/ 限流 / 5xx 可重试；401、403 需人工；其余永久失败。"""
    if status_code is None or status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
        category = "retryable"
    elif status_code in NEEDS_HUMAN_STATUS_CODES:
        category = "needs_human"
    else:
        category = "non_retryable"
    return GitHubApiError(status_code, message, category)


@runtime_checkable
class GitHubClient(Protocol):
    async def create_comment(self, *, repository: str, url: str, body: str) -> dict[str, Any]: ...

    async def create_pull_request(
        self, *, repository: str, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]: ...

    async def request_review(
        self, *, repository: str, url: str, reviewers: list[str]
    ) -> dict[str, Any]: ...


class FakeGitHubClient:
    """记录 `(操作名, kwargs)`；`fail_next` 抛一次后清空；`results` 按操作名给返回值。

    失败的那次调用也会进 `calls`：调用确实发出去了，重放不重复写的证据要能数到它。
    """

    def __init__(self, results: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next: Exception | None = None
        self.results = dict(results or {})

    async def _run(self, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, kwargs))
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        return dict(self.results.get(operation, {}))

    async def create_comment(self, *, repository: str, url: str, body: str) -> dict[str, Any]:
        return await self._run(
            "create_comment", {"repository": repository, "url": url, "body": body}
        )

    async def create_pull_request(
        self, *, repository: str, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        return await self._run(
            "create_pull_request",
            {"repository": repository, "head": head, "base": base, "title": title, "body": body},
        )

    async def request_review(
        self, *, repository: str, url: str, reviewers: list[str]
    ) -> dict[str, Any]:
        return await self._run(
            "request_review", {"repository": repository, "url": url, "reviewers": reviewers}
        )
