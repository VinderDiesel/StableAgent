"""C 节：动态能力视图。召回护栏 / 兜底 / 定序 / 裁剪计量，全部经 tool_selector 位点。"""

from typing import Any

import pytest

from stableagent.context.capability import (
    CAPABILITY_FILTERS,
    CapabilityFilter,
    RelevanceFilter,
    StaticFilter,
    as_tool_selector,
    query_terms,
    resident_tool_names,
    resolve_capability_filter,
)
from stableagent.context.tokens import HeuristicTokenCounter, count_context
from stableagent.core.state import AgentState, Config, ContextConfig, Message, ToolCall
from stableagent.tools.registry import Tool, ToolResult
from tests.support import FakeProvider, final_response
from tests.unit.test_loop_branches import collect, make_agent

DESCRIPTIONS = {
    "query_sql": "run a SQL query against the analytics warehouse",
    "search_web": "search the public web for recent pages",
    "read_file": "read a file from the local workspace",
    "write_file": "write content into a file",
    "send_email": "send an email to a teammate",
    "render_chart": "render a chart from a table of numbers",
    "导出报表": "把查询结果导出为 csv 文件",
    "translate_text": "translate text between languages",
    "crop_image": "crop an image to a rectangle",
    "list_runs": "list recent agent runs",
}
ALL_NAMES = sorted(DESCRIPTIONS)


def specs(count: int = len(DESCRIPTIONS)) -> list[dict[str, Any]]:
    return [
        {"name": name, "description": DESCRIPTIONS[name], "parameters": {"type": "object"}}
        for name in sorted(DESCRIPTIONS)[:count]
    ]


async def _execute(self: Any, arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(output="ok")


def stub_types() -> list[type[Tool]]:
    """给 registry 用的工具类（带 name/description，spec 内容与 DESCRIPTIONS 对齐）。"""
    return [
        type(f"Stub_{name}", (Tool,), {"name": name, "description": text, "execute": _execute})
        for name, text in sorted(DESCRIPTIONS.items())
    ]


def build_state(
    task: str = "run a SQL query on the warehouse", used: tuple[str, ...] = ()
) -> AgentState:
    messages = [Message(role="user", content=task)]
    for name in used:
        call = ToolCall(id=f"c-{name}", name=name, arguments={})
        messages.append(Message(role="assistant", content="", tool_calls=[call]))
        messages.append(Message(role="tool", content="ok", tool_call_id=call.id, name=name))
    return AgentState(run_id="r", agent_name="a", messages=messages)


def names_of(kept: list[dict[str, Any]]) -> list[str]:
    return [str(spec["name"]) for spec in kept]


# ----------------------------------------------------------------------- C1 注册表


def test_c1_registry_has_static_and_at_least_one_real_filter() -> None:
    assert sorted(CAPABILITY_FILTERS) == ["relevance", "static"]


def test_c1_static_filter_returns_everything_in_original_order() -> None:
    shuffled = list(reversed(specs()))
    kept = StaticFilter(ContextConfig()).select(shuffled, build_state())
    assert kept == shuffled  # L4：连顺序都不动，默认路径与 P2 逐条一致


def test_c1_relevance_narrows_by_task_terms() -> None:
    kept = RelevanceFilter(ContextConfig()).select(specs(), build_state())
    # "run" 命中 query_sql 的描述，也命中 list_runs 的名字——名字算词面是有意的（同族工具该一起召回）
    assert names_of(kept) == ["list_runs", "query_sql"]
    assert "search_web" not in names_of(kept) and "crop_image" not in names_of(kept)


def test_c1_two_letter_words_are_not_task_terms() -> None:
    """ "on"/"an" 做子串匹配会命中 content、analytics 这类无关描述——相关性退化成噪声。"""
    assert query_terms(build_state("work on the plan")) == ["plan", "work"]


def test_c1_cjk_task_terms_match_cjk_descriptions() -> None:
    """中文任务不能因为"没分词"就匹配不到中文描述——单字成词就是为这条写的。"""
    kept = RelevanceFilter(ContextConfig()).select(specs(), build_state("把结果导出成报表"))
    assert "导出报表" in names_of(kept)


def test_c1_unknown_filter_names_registered_alternatives() -> None:
    with pytest.raises(KeyError, match=r"unknown capability_filter 'nope'.*relevance"):
        resolve_capability_filter(ContextConfig(capability_filter="nope"))


def test_c1_helper_functions_see_the_same_view() -> None:
    state = build_state(used=("send_email",))
    assert resident_tool_names(state.messages) == {"send_email"}
    assert "warehouse" in query_terms(state)


# -------------------------------------------------------------------- C2 位点接入


async def test_c2_config_driven_selector_reaches_the_provider() -> None:
    provider = FakeProvider([final_response("end")])
    agent = make_agent(
        provider,
        tools=stub_types(),
        config=Config(context=ContextConfig(capability_filter="relevance")),
    )
    await collect(agent, "run a SQL query on the warehouse")
    sent = provider.calls[0][1]
    assert sent is not None and len(sent) < len(ALL_NAMES)
    assert names_of(sent) == ["list_runs", "query_sql"]
    assert agent.capability_report is not None and agent.capability_report.dropped == 8


async def test_c2_explicit_tool_selector_wins_over_config() -> None:
    provider = FakeProvider([final_response("end")])

    def keep_all(specs_: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        return list(specs_)

    agent = make_agent(
        provider,
        tools=stub_types(),
        config=Config(context=ContextConfig(capability_filter="relevance")),
        tool_selector=keep_all,
    )
    await collect(agent, "run a SQL query on the warehouse")
    assert names_of(provider.calls[0][1] or []) == ALL_NAMES  # 配置的相关性筛选没有插手
    assert agent.capability_report is None  # 也就不会报一份"其实没生效"的筛选结果


def test_c3_tools_used_this_run_are_never_dropped() -> None:
    """本任务词面与 send_email 无关，但它已经被调用过——筛掉它模型下一步就找不到。"""
    kept = RelevanceFilter(ContextConfig()).select(
        specs(), build_state("run a SQL query on the warehouse", used=("send_email",))
    )
    assert "send_email" in names_of(kept)


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_c3_resident_beats_the_visible_cap(cap: int) -> None:
    config = ContextConfig(max_visible_tools=cap)
    kept = RelevanceFilter(config).select(
        specs(), build_state("run a SQL query", used=("send_email", "crop_image"))
    )
    assert {"send_email", "crop_image"} <= set(names_of(kept))


def test_c3_guard_holds_for_every_state_and_every_cap() -> None:
    filter_ = RelevanceFilter(ContextConfig(max_visible_tools=2))
    for task in ("run a SQL query", "把结果导出成报表", "zzz qqq"):
        for used in ((), ("list_runs",), ("query_sql", "render_chart", "crop_image")):
            state = build_state(task, used=used)
            assert set(used) <= set(names_of(filter_.select(specs(), state)))


# ----------------------------------------------------------------------- C4 兜底


def test_c4_no_match_falls_back_to_full_list() -> None:
    filter_ = RelevanceFilter(ContextConfig())
    kept = filter_.select(specs(), build_state("zzzqqq"))
    assert names_of(kept) == ALL_NAMES and filter_.last_report.fallback is True


def test_c4_empty_registry_stays_empty_without_panic() -> None:
    filter_ = RelevanceFilter(ContextConfig())
    assert filter_.select([], build_state()) == []


# ----------------------------------------------------------------------- C5 定序


def test_c5_output_order_is_lexicographic_not_insertion_or_hash() -> None:
    forward = RelevanceFilter(ContextConfig(max_visible_tools=4)).select(
        specs(), build_state("run a SQL query", used=("send_email", "crop_image"))
    )
    backward = RelevanceFilter(ContextConfig(max_visible_tools=4)).select(
        list(reversed(specs())), build_state("run a SQL query", used=("send_email", "crop_image"))
    )
    assert names_of(forward) == sorted(names_of(forward)) == names_of(backward)


def test_c5_repeated_selection_is_bitwise_stable() -> None:
    filter_ = RelevanceFilter(ContextConfig())
    state = build_state("read a file then write it")
    first = filter_.select(specs(), state)
    report = filter_.last_report
    second = filter_.select(specs(), state)
    assert first == second and report == filter_.last_report


# --------------------------------------------------------------------- C6 裁剪计量


def test_c6_dropped_tokens_are_measured_not_declared() -> None:
    filter_ = RelevanceFilter(ContextConfig(max_visible_tools=3))
    all_specs = specs()
    kept = filter_.select(all_specs, build_state("run a SQL query"))
    counter = HeuristicTokenCounter()
    dropped = [s for s in all_specs if s not in kept]
    assert filter_.last_report.dropped == len(dropped)
    assert filter_.last_report.kept == len(kept)
    assert filter_.last_report.dropped_tokens == count_context(counter, [], dropped) > 0


def test_c6_saving_never_exceeds_the_full_tool_list() -> None:
    full = count_context(HeuristicTokenCounter(), [], specs())
    filter_ = RelevanceFilter(ContextConfig(max_visible_tools=2))
    filter_.select(specs(), build_state("run a SQL query"))
    assert 0 < filter_.last_report.dropped_tokens < full


def test_c6_static_filter_reports_zero_savings() -> None:
    filter_ = StaticFilter(ContextConfig())
    filter_.select(specs(), build_state())
    assert (filter_.last_report.dropped, filter_.last_report.dropped_tokens) == (0, 0)


def test_default_config_short_circuits_the_filter() -> None:
    assert resolve_capability_filter(ContextConfig()).last_report.kept == 0
    assert isinstance(resolve_capability_filter(ContextConfig()), StaticFilter)


def test_as_tool_selector_matches_the_loop_shape() -> None:
    selector = as_tool_selector(RelevanceFilter(ContextConfig()))
    kept = selector(specs(), build_state("run a SQL query"))
    assert isinstance(kept, list) and isinstance(kept[0], dict)
    assert isinstance(RelevanceFilter(ContextConfig()), CapabilityFilter)
