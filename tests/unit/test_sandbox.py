"""D1（步骤 2.8）：`local-restricted` 的叙事纪律——文档、拒绝消息、Report summary 都不称沙箱。

约束来自规格 §1（"不宣称本地受限模式能隔离恶意代码"）与 ADR-05 决策 7：`LocalRestrictedRunner`
的类文档、被拒时产生的消息、以及进 Report 的一行 summary 三处都不得出现 "sandbox" 一词，
且必须写明 `local-restricted`。断言的是文案，不是行为。

Task 8 只落"协议 + 本地实现 + summary 形状"；真正的 Docker 边界（D2）与 Docker 的双条件
门禁（步骤 2.11）在 Task 10/11。
"""

from __future__ import annotations

import inspect
import pathlib
from collections.abc import Sequence

from stableagent.execution.command import (
    CommandContext,
    CommandRequest,
    CommandResult,
    CommandRunner,
)
from stableagent.execution.sandbox import LocalRestrictedRunner, SandboxRunner
from tests.support import FixedClock

TICKET_ID = "tk-0001"
ASSIGNMENT_ID = "as-0001"

#: stdout 里出现的敏感串；它一旦进了 summary，就说明长期记录会带走命令正文。
STREAM_MARKER = "MARKER-FROM-STDOUT-STREAM"


def _context() -> CommandContext:
    return CommandContext(
        ticket_id=TICKET_ID,
        assignment_id=ASSIGNMENT_ID,
        repository="acme/widgets",
        base_ref="main",
    )


def _worktree(tmp_path: pathlib.Path) -> pathlib.Path:
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"
    worktree.mkdir(parents=True)
    return worktree


def _request(
    worktree: pathlib.Path,
    *argv: str,
    cwd: pathlib.Path | None = None,
    timeout_s: float | None = None,
) -> CommandRequest:
    return CommandRequest(
        argv=tuple(argv), cwd=cwd or worktree, context=_context(), timeout_s=timeout_s
    )


def _runner(tmp_path: pathlib.Path, allowlist: Sequence[str], **kwargs: object) -> CommandRunner:
    return CommandRunner(
        frozenset(allowlist),
        worktree_root=tmp_path / "worktrees",
        clock=FixedClock(),
        **kwargs,
    )


def _result(**changes: object) -> CommandResult:
    fields: dict[str, object] = {
        "argv": ("python3", "-c", "pass"),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "started_at": 1_700_000_000_000,
        "ended_at": 1_700_000_000_000,
        "truncated": False,
        "timed_out": False,
        "denied_reason": None,
    }
    fields.update(changes)
    return CommandResult(**fields)  # type: ignore[arg-type]


class RecordingRunner:
    """`CommandRunner` 的替身：记下收到的 request，原样返回一个固定结果。"""

    def __init__(self, result: CommandResult) -> None:
        self.calls: list[CommandRequest] = []
        self._result = result

    def run(self, request: CommandRequest) -> CommandResult:
        self.calls.append(request)
        return self._result


def test_local_restricted_never_says_sandbox(tmp_path: pathlib.Path) -> None:
    """D1 的三处文案：类文档 + 两条拒绝消息；不称沙箱，且写明 local-restricted。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    denied_command = runner.run(_request(worktree, "rm", "-rf", "/"))
    denied_cwd = runner.run(_request(worktree, "python3", cwd=tmp_path / "outside"))
    # 语料必须真的是这两条拒绝，否则文案断言会空转。
    assert denied_command.denied_reason == "command_not_allowed"
    assert denied_cwd.denied_reason == "cwd_escape"

    corpus = {
        "类文档": inspect.getdoc(LocalRestrictedRunner) or "",
        "拒绝消息/命令不在名单": denied_command.summary(),
        "拒绝消息/cwd 越界": denied_cwd.summary(),
    }
    for label, text in corpus.items():
        lowered = text.lower()
        assert "sandbox" not in lowered, f"{label} 把本地受限模式称成了沙箱：{text!r}"
        assert "local-restricted" in lowered, f"{label} 没写明 local-restricted：{text!r}"


async def test_runner_delegates_to_command_runner(tmp_path: pathlib.Path) -> None:
    """本地实现只做一件事：把 request 交给 `CommandRunner`，把它的结果原样交回去。"""
    worktree = _worktree(tmp_path)
    sentinel = _result()
    inner = RecordingRunner(sentinel)
    local = LocalRestrictedRunner(inner)  # type: ignore[arg-type]
    request = _request(worktree, "python3", "-c", "pass")

    result = await local.run(request)

    assert result is sentinel
    assert inner.calls == [request]


def test_local_restricted_matches_the_protocol_without_inheriting_it() -> None:
    """协议靠结构匹配（M2 取向 ①）：`LocalRestrictedRunner` 不继承 `SandboxRunner`。"""
    assert LocalRestrictedRunner.__mro__[1] is object
    assert isinstance(LocalRestrictedRunner(RecordingRunner(_result())), SandboxRunner)  # type: ignore[arg-type]


def test_summary_names_mode_exit_code_and_truncation(tmp_path: pathlib.Path) -> None:
    """summary 的三个必含项：模式、退出码、是否截断——被截的那一份要真的被截。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])
    capped = _runner(tmp_path, ["python3"], max_output_bytes=200)

    intact = runner.run(_request(worktree, "python3", "-c", "print('ok')"))
    big = capped.run(_request(worktree, "python3", "-c", f"print('{STREAM_MARKER}' + 'x' * 5000)"))

    assert big.truncated  # 没截就没有断到"是否截断"这一项
    assert "local-restricted" in intact.summary()
    assert "exit=0" in intact.summary()
    assert "intact" in intact.summary()
    assert "local-restricted" in big.summary()
    assert "truncated" in big.summary()


def test_summary_carries_no_stream_text(tmp_path: pathlib.Path) -> None:
    """正文再短也不进 summary：Report 长期留存，stdout 里可能有密钥，也可能根本不是我们的文本。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    result = runner.run(_request(worktree, "python3", "-c", f"print('{STREAM_MARKER}')"))

    assert STREAM_MARKER in result.stdout
    assert STREAM_MARKER not in result.summary()


def test_denied_summary_names_the_stable_code(tmp_path: pathlib.Path) -> None:
    """被拒的一行写稳定码，不写文案：调用方按码分派，不解析句子。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    denied = runner.run(_request(worktree, "curl", "http://example.invalid"))

    assert "denied=command_not_allowed" in denied.summary()
    assert "exit=" not in denied.summary()


def test_timeout_summary_names_the_missing_exit_code(tmp_path: pathlib.Path) -> None:
    """超时那一档不写 `exit=None`：事实是"没有退出码"，不是"退出码是空"。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    timed_out = runner.run(
        _request(worktree, "python3", "-c", "__import__('time').sleep(5)", timeout_s=0.05)
    )

    assert timed_out.timed_out and timed_out.exit_code is None
    summary = timed_out.summary()
    assert "timed_out" in summary and "exit=" not in summary
    assert "local-restricted" in summary
