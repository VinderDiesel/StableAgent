"""L4：默认配置零回归——拿 P2 收口提交跑出来的金标准逐字节比对。

`tests/fixtures/l4_p2_default_stream.json` 是在 `46441f0`（P2 收口）的工作树里跑
`tests/l4_default_stream_dump.py` 生成的：事件流、每次发给 Provider 的 messages/tools、
终态快照、`CONTRACT_VERSION`、`EventType` 全集都在里面。当前代码在默认 `Config()` 下
必须复现同一份输出，P3 的三条路径（压缩 / 能力视图 / 预算）才算真的短路。

比对失败时有两种可能：P3 代码在默认配置下漏了路，或者这次改动是**有意**改变默认行为——
后者属于契约决策，得进 `docs/design-decisions/00-contracts.md`，不能靠更新金标准蒙过去。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.l4_default_stream_dump import dump

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "l4_p2_default_stream.json"


async def test_p3_default_config_stream_identical_to_p2() -> None:
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert await dump() == golden


async def test_l4_golden_covers_the_three_p3_switches() -> None:
    """金标准里必须真的含着三档开关的默认值，否则"比对通过"证明不了任何事。"""
    from stableagent.core.state import ContextConfig

    context = ContextConfig()
    assert (context.assembly, context.compression, context.capability_filter) == (
        "passthrough",
        "none",
        "static",
    )
    assert context.max_context_tokens is None
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["contract_version"] == "1.1.0"
