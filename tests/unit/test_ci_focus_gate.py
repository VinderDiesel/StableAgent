"""M3.3：CI 心脏覆盖闸门的离线契约——防止后续误删 FOCUS 项或退回通配兜底。"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
NEW_FILES = [
    "orchestration/model.py",
    "orchestration/store.py",
    "orchestration/outbox.py",
    "orchestration/executor.py",
    "orchestration/scheduler.py",
    "orchestration/service.py",
]


def test_focus_covers_the_eighteen_cardiac_files() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert all(f'"{name}"' in text for name in NEW_FILES)
    assert "18 文件" in text  # 步骤名与注释同步
    assert text.count('".py"') == 0  # 不许用通配兜底
