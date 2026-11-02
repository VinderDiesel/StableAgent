"""B 节：历史压缩三档。配对不拆散 / 摘要占位 / 顺序锁死 / 幂等 / 旧快照兼容。"""

from pathlib import Path
from typing import Any

import pytest

from stableagent.context.assembly import CompressedAssembler, resolve_assembler
from stableagent.context.compression import (
    COMPRESSIONS,
    SUMMARY_NAME,
    ExtractiveSummariser,
    HybridCompressor,
    SummaryCompressor,
    TruncationCompressor,
    is_summary_message,
    partition,
    resolve_compression,
    split_summary,
)
from stableagent.core.checkpoint.recovery import migrate_state
from stableagent.core.state import AgentState, Config, ContextConfig, Message, ToolCall
from tests.support import FakeProvider, final_response, tool_response
from tests.unit.test_loop_branches import collect, make_agent

FACT_CHARS = 400
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def corpus(turns: int = 6) -> list[Message]:
    """定序语料：每轮一个工具调用，工具结果是 token 大头，事实埋在开头。"""
    messages = [Message(role="user", content="原始任务：统计三个季度的营收")]
    for index in range(turns):
        call = ToolCall(id=f"c{index}", name="search", arguments={"q": f"q{index}"})
        messages.append(Message(role="assistant", content=f"第{index}轮思考", tool_calls=[call]))
        messages.append(
            Message(
                role="tool",
                content=f"FACT-{index} " + "结果" * FACT_CHARS,
                tool_call_id=call.id,
                name="search",
            )
        )
    return messages


def assert_pairs_intact(messages: list[Message]) -> None:
    """B1 不变式：带 tool_calls 的 assistant 之后紧跟其全部 tool 结果，无一被切走。"""
    for index, message in enumerate(messages):
        if not message.tool_calls:
            continue
        following = messages[index + 1 : index + 1 + len(message.tool_calls)]
        assert [m.tool_call_id for m in following] == [c.id for c in message.tool_calls]
        assert all(m.role == "tool" for m in following)


def summaries(messages: list[Message]) -> list[Message]:
    return [m for m in messages if is_summary_message(m)]


# ------------------------------------------------------------------ B1 truncation


def test_b1_truncation_drops_oldest_and_keeps_pinned() -> None:
    result = TruncationCompressor(ContextConfig(keep_recent_messages=4)).compress(corpus())
    assert result.folded == 8  # 四轮工具往返（assistant+tool 各一条）整体折叠
    assert result.kept[0].role == "user"  # 任务本体常驻
    assert result.summary == ""  # 截断不生成摘要，信息真丢——这是它的取舍
    assert [m.content for m in result.kept[1:]] == [m.content for m in corpus()[-4:]]


@pytest.mark.parametrize("keep", list(range(0, 13)))
def test_b1_truncation_never_splits_tool_pairs(keep: int) -> None:
    result = TruncationCompressor(ContextConfig(keep_recent_messages=keep)).compress(corpus())
    assert_pairs_intact(result.kept)


@pytest.mark.parametrize("keep", list(range(0, 13)))
def test_b1_boundary_rule_holds_for_all_strategies(keep: int) -> None:
    for strategy in COMPRESSIONS.values():
        result = strategy(ContextConfig(keep_recent_messages=keep)).compress(corpus())
        assert_pairs_intact(result.kept)


def test_b1_negative_keep_is_rejected_loudly() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        partition(corpus(), -1)


# ------------------------------------------------------------------------ B2 summary


def test_b2_folded_prefix_becomes_one_summary_message() -> None:
    result = SummaryCompressor(ContextConfig(keep_recent_messages=4)).compress(corpus())
    assert len(summaries(result.kept)) == 1
    assert result.kept[0].name == SUMMARY_NAME
    assert "FACT-0" in result.kept[0].content  # 抽取式摘录保留被折叠段的事实标识
    assert result.levels == ("summary",)


def test_b2_summary_is_deterministic_offline() -> None:
    config = ContextConfig(keep_recent_messages=4)
    first = SummaryCompressor(config).compress(corpus())
    second = SummaryCompressor(config).compress(corpus())
    assert first.summary == second.summary
    assert first.kept == second.kept


def test_b2_injected_summariser_replaces_default() -> None:
    seen: list[list[Message]] = []

    class Recorder:
        def summarise(self, messages: list[Message]) -> str:
            seen.append(list(messages))
            return "MODEL-SUMMARY"

    result = SummaryCompressor(ContextConfig(keep_recent_messages=4), Recorder()).compress(corpus())
    assert result.kept[0].content == "MODEL-SUMMARY"
    assert seen[0] == corpus()[1:9]  # 摘要器收到的正是被折叠的那一段，不多不少


def test_b2_existing_summary_is_reused_not_multiplied() -> None:
    config = ContextConfig(keep_recent_messages=4)
    compressor = SummaryCompressor(config)
    once = compressor.compress(corpus()).kept
    twice = compressor.compress(once).kept
    assert len(summaries(twice)) == 1
    assert split_summary(once)[1] == split_summary(twice)[1]


def test_b2_summary_length_is_capped() -> None:
    config = ContextConfig(keep_recent_messages=4, max_summary_chars=40)
    result = SummaryCompressor(config).compress(corpus())
    assert len(result.summary) <= 40


def test_b2_second_fold_appends_to_the_cached_summary() -> None:
    """历史继续变长时：旧摘要不重算、新折叠段并进来，且受同一长度上限约束。"""
    config = ContextConfig(keep_recent_messages=4, max_summary_chars=4000)
    compressor = SummaryCompressor(config)
    stage_one = compressor.compress(corpus(turns=4))
    grown = [*stage_one.kept, *corpus(turns=8)[-4:]]
    stage_two = compressor.compress(grown)
    assert stage_two.folded == 4
    assert stage_one.summary.splitlines()[0] in stage_two.summary  # 旧摘要原样保留
    assert "FACT-3" in stage_two.summary and len(stage_two.summary) > len(stage_one.summary)
    assert len(summaries(stage_two.kept)) == 1


def test_b2_merge_respects_the_cap_when_growing() -> None:
    config = ContextConfig(keep_recent_messages=2, max_summary_chars=50)
    compressor = SummaryCompressor(config)
    first = compressor.compress(corpus(turns=2))
    second = compressor.compress([*first.kept, *corpus(turns=6)[-4:]])
    assert len(second.summary) <= 50


def test_b2_default_summariser_is_extractive() -> None:
    text = ExtractiveSummariser(per_message_chars=10).summarise(corpus(turns=1))
    assert "调用工具: search" in text and "- user: 原始任务" in text


# -------------------------------------------------------------------------- B3 hybrid


def test_b3_hybrid_level_order_is_locked() -> None:
    result = HybridCompressor(ContextConfig(keep_recent_messages=4)).compress(corpus())
    assert result.levels == ("tool_excerpt", "summary", "truncation")


def test_b3_summary_sees_excerpted_tool_results_not_originals() -> None:
    """顺序证据：摘要器的输入里工具结果已被摘录，说明"先折工具、再摘要"。"""
    seen: list[list[Message]] = []

    class Recorder:
        def summarise(self, messages: list[Message]) -> str:
            seen.append(list(messages))
            return "s"

    config = ContextConfig(keep_recent_messages=4, tool_result_excerpt_chars=12)
    HybridCompressor(config, Recorder()).compress(corpus())
    tool_messages = [m for m in seen[0] if m.role == "tool"]
    assert tool_messages and all(len(m.content) <= 12 for m in tool_messages)


def test_b3_recent_window_stays_verbatim() -> None:
    config = ContextConfig(keep_recent_messages=4, tool_result_excerpt_chars=12)
    result = HybridCompressor(config).compress(corpus())
    tail = [m for m in result.kept if m.role == "tool"]
    assert all(len(m.content) > 12 for m in tail)  # 保留段不动：最近的证据保持原样


def test_b3_all_three_levels_run_when_nothing_can_be_kept() -> None:
    config = ContextConfig(keep_recent_messages=1, tool_result_excerpt_chars=20)
    assert HybridCompressor(config).compress(corpus(turns=2)).levels == (
        "tool_excerpt",
        "summary",
        "truncation",
    )


def test_b3_no_fold_means_no_levels_recorded() -> None:
    result = HybridCompressor(ContextConfig(keep_recent_messages=40)).compress(corpus())
    assert result.levels == () and result.folded == 0


# ------------------------------------------------------------------- B4 registry wiring


def test_b4_registry_covers_three_strategies() -> None:
    assert sorted(COMPRESSIONS) == ["hybrid", "summary", "truncation"]


def test_b4_unknown_compression_names_registered_alternatives() -> None:
    with pytest.raises(KeyError, match=r"unknown compression 'nope'.*hybrid"):
        resolve_compression(ContextConfig(compression="nope"))


def test_b4_default_config_short_circuits_compression() -> None:
    assert resolve_compression(ContextConfig()) is None  # L4：默认不压缩
    assembler = resolve_assembler(ContextConfig())
    assert not isinstance(assembler, CompressedAssembler)
    assert assembler.assemble(corpus()) == corpus()


def test_b4_compression_applies_whatever_assembly_is_chosen() -> None:
    """单一开关：assembly 保持默认，compression 一开即生效（不配"配了却不生效"的坑）。"""
    assembler = resolve_assembler(ContextConfig(compression="truncation", keep_recent_messages=4))
    assert isinstance(assembler, CompressedAssembler)
    assert len(assembler.assemble(corpus())) < len(corpus())


def test_b4_unknown_assembly_error_text_unchanged() -> None:
    with pytest.raises(KeyError, match="unknown assembly"):
        resolve_assembler(ContextConfig(assembly="nope", compression="summary"))


async def test_b4_compression_reaches_the_provider_without_touching_loop() -> None:
    provider = FakeProvider(
        [tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")]
    )
    config = Config(context=ContextConfig(compression="summary", keep_recent_messages=1))
    agent = make_agent(provider, config=config)
    await collect(agent)
    baseline = FakeProvider(
        [tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")]
    )
    await collect(make_agent(baseline))
    compressed, plain = provider.calls[-1][0], baseline.calls[-1][0]
    assert len(compressed) < len(plain)  # 历史确实被折叠后才发给模型
    assert summaries(compressed) and not summaries(plain)


# --------------------------------------------------------------------------- B6 幂等


@pytest.mark.parametrize("name", sorted(COMPRESSIONS))
def test_b6_compress_is_idempotent(name: str) -> None:
    """再压一次不改变输出：压缩是快照的纯函数，没有"第二次多花一次模型调用"这回事。"""
    config = ContextConfig(keep_recent_messages=4)
    compressor = COMPRESSIONS[name](config)
    once = compressor.compress(corpus())
    twice = compressor.compress(once.kept)
    assert twice.kept == once.kept
    assert twice.summary == once.summary
    assert twice.folded == 0


@pytest.mark.parametrize("name", sorted(COMPRESSIONS))
def test_b6_repeated_assemble_returns_identical_context(name: str) -> None:
    assembler = resolve_assembler(ContextConfig(compression=name))
    assert assembler.assemble(corpus()) == assembler.assemble(corpus())


# ------------------------------------------------------------------ B7 旧快照兼容


def test_b7_snapshot_without_summary_marker_still_loads() -> None:
    payload: dict[str, Any] = {
        "run_id": "r1",
        "agent_name": "a",
        "contract_version": "1.1.0",
        "messages": [{"role": "user", "content": "旧快照里没有摘要标记"}],
    }
    state = AgentState.model_validate(payload)
    assert not any(is_summary_message(m) for m in state.messages)
    assert AgentState.loads(state.snapshot()) == state  # E3 往返无损在 P3 仍成立


def test_b7_summary_travels_in_messages_not_in_a_second_field() -> None:
    """摘要的唯一载体是 messages 里的标记消息；再加一个字段就是两份会漂移的事实源。"""
    kept = COMPRESSIONS["summary"](ContextConfig(keep_recent_messages=4)).compress(corpus()).kept
    state = AgentState(run_id="r", agent_name="a", messages=kept)
    dumped = state.model_dump()
    assert "context_summary" not in dumped
    assert any(m.get("name") == SUMMARY_NAME for m in dumped["messages"])
    assert AgentState.loads(state.snapshot()) == state


def test_b7_v1_0_0_fixture_migrates_under_p3_fields() -> None:
    fixture = FIXTURE_DIR / "state_v1_0_0.json"
    state = migrate_state(fixture.read_text())
    assert state.messages and all(m.tool_calls == [] for m in state.messages)


# --------------------------------------------------------------------------- B5 事实保留


def test_b5_retention_pairs_with_savings_on_fixed_corpus() -> None:
    """节省率与保留率成对断言：只报节省率的压缩等于没测。"""
    from stableagent.context.tokens import HeuristicTokenCounter, count_context

    counter = HeuristicTokenCounter()
    original = count_context(counter, corpus())
    rows: dict[str, tuple[float, float]] = {}
    for name, strategy in sorted(COMPRESSIONS.items()):
        result = strategy(ContextConfig(keep_recent_messages=4)).compress(corpus())
        kept_tokens = count_context(counter, result.kept)
        retained = sum(
            1 for index in range(6) if f"FACT-{index}" in " ".join(m.content for m in result.kept)
        )
        rows[name] = (1 - kept_tokens / original, retained / 6)
    # 截断只留窗口内的两条事实；摘要用一点节省换回全部早期事实——顺序与取舍都必须可断言
    assert rows["truncation"][0] > rows["summary"][0]
    assert rows["truncation"][1] == 2 / 6 and rows["summary"][1] == 1
    assert rows["hybrid"][1] == 1 and rows["hybrid"][0] >= rows["summary"][0]
    assert {name: round(value[0], 3) for name, value in rows.items()}  # 数值进 benchmark（F 节）
