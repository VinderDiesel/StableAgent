"""P3 L3/L6：依赖与兼容扩展保持可验证。"""

import re
from pathlib import Path

import stableagent
from stableagent.core.state import ContextConfig
from stableagent.tools.evidence import Evidence


def test_l3_runtime_dependencies_remain_the_three_declared_packages() -> None:
    config_path = Path(__file__).parents[2] / "pyproject.toml"
    text = config_path.read_text(encoding="utf-8")
    dependencies_block = re.search(
        r"^dependencies = \[(.*?)^\]", text, flags=re.DOTALL | re.MULTILINE
    )
    assert dependencies_block is not None
    dependencies = re.findall(r'"([^\"]+)"', dependencies_block.group(1))
    assert dependencies == ["pydantic>=2.7", "anyio>=4.3", "httpx>=0.27"]
    assert stableagent.Agent is not None


def test_l6_p3_extensions_keep_default_construction_compatible() -> None:
    config = ContextConfig()
    assert (
        config.compression,
        config.capability_filter,
        config.max_context_tokens,
        config.keep_recent_messages,
        config.max_summary_chars,
        config.tool_result_excerpt_chars,
        config.max_visible_tools,
    ) == ("none", "static", None, 8, 1200, 200, 8)
    evidence = Evidence.model_validate_json(
        '{"tool_call":{"id":"c","name":"echo","arguments":{}},"ok":true,"output":"ok"}'
    )
    assert set(evidence.model_dump()) == {"tool_call", "ok", "output", "error"}
