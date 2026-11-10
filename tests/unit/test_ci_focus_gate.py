"""M3.3/M4.6：CI 心脏覆盖闸门的离线契约——防止后续误删 FOCUS 项或退回通配兜底。"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
FOCUS_FILES = [
    "core/loop.py",
    "core/checkpoint/manager.py",
    "core/checkpoint/recovery.py",
    "core/checkpoint/storage.py",
    "core/errors.py",
    "context/assembly.py",
    "context/tokens.py",
    "context/compression.py",
    "context/capability.py",
    "context/budget.py",
    "context/engine.py",
    "tools/evidence.py",
    "orchestration/model.py",
    "orchestration/store.py",
    "orchestration/outbox.py",
    "orchestration/executor.py",
    "orchestration/scheduler.py",
    "orchestration/service.py",
    "control/policy.py",
    "control/approval.py",
    "execution/worktree.py",
    "execution/command.py",
    "execution/sandbox.py",
    "integrations/github.py",
    "integrations/mcp.py",
    "supervisor/model.py",
    "supervisor/store.py",
    "supervisor/views.py",
    "supervisor/source.py",
    "supervisor/assessor.py",
    "supervisor/router.py",
    "supervisor/service.py",
    "supervisor/runtime.py",
    "supervisor/notifications.py",
    "supervisor/console.py",
    "observability/events.py",
    "observability/redaction.py",
    "observability/emitter.py",
    "observability/store.py",
    "observability/metrics.py",
    "control/cost.py",
    "providers/routing.py",
    "integrations/mcp_registry.py",
    "supervisor/sources/slack.py",
    "supervisor/channels/slack.py",
]


def test_focus_covers_the_forty_five_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in FOCUS_FILES)
    assert len(FOCUS_FILES) == 45
    assert "45 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
