"""异步事件驱动 Agent Loop（P1 交付，契约 v1.1.0）。

结构约束（P1 DoD，P2/P3 消费的前提）：
- 状态外置：一次运行的全部可恢复信息都在 AgentState（C1）。
- 纯函数转移：`advance(state) -> (AgentState, list[Event])`（F1），本模块不做任何
  直接 I/O，只调用注入的 Provider / ToolRegistry / assembler。
- 组装位点：发给 Provider 的消息经 ContextAssembler 产出（D1），
  工具列表经 tool_selector 产出（D3），P3 零修改本模块接入。
- pause 接入点：控制标志在步边界生效（F3），P2 只替换持久化层。
  pause_requested 是控制信号而非运行状态，不违反 C1。

终止语义（docs/p1-dod-checklist.md 第 0 节）：
- F2：max_steps 超限 -> error 事件 + FAILED。
- F4：done / error 恰好一个且为流末尾。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from stableagent.context.assembly import ContextAssembler, resolve_assembler
from stableagent.core.state import (
    TERMINAL_EVENTS,
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
    Message,
)
from stableagent.providers.base import Provider
from stableagent.tools.registry import Evidence, ToolRegistry

ToolSelector = Callable[[list[dict[str, Any]], AgentState], list[dict[str, Any]]]


def _select_all(specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    return list(specs)


class AgentLoop:
    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        config: Config,
        assembler: ContextAssembler | None = None,
        tool_selector: ToolSelector | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._config = config
        self._assembler = assembler or resolve_assembler(config.context)
        self._tool_selector = tool_selector or _select_all
        self.pause_requested = False

    async def advance(self, state: AgentState) -> tuple[AgentState, list[Event]]:
        """执行一步转移：读入快照，返回新快照与本步事件。不触碰外部状态。"""
        messages = self._assembler.assemble(state.messages)
        specs = self._tool_selector(self._registry.specs(), state)
        try:
            response = await self._provider.acomplete(messages, tools=specs)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            failed = state.model_copy(update={"status": AgentStatus.FAILED, "error": detail})
            event = Event(
                type=EventType.ERROR,
                data={"reason": "provider_error", "detail": detail},
                step=failed.step,
            )
            return failed, [event]

        step = state.step + 1
        usage = dict(state.usage)
        for key, value in response.usage.items():
            usage[key] = usage.get(key, 0) + value

        assistant = Message(
            role="assistant",
            content=response.content or "",
            tool_calls=response.tool_calls,
        )
        events = (
            [Event(type=EventType.THINKING, data={"text": assistant.content}, step=step)]
            if assistant.content
            else []
        )

        evidences: list[Evidence] = []
        if response.tool_calls:
            for tool_call in response.tool_calls:
                events.append(
                    Event(
                        type=EventType.TOOL_CALL,
                        data={"tool_call": tool_call.model_dump()},
                        step=step,
                    )
                )
                evidence = await self._registry.call(tool_call)
                evidences.append(evidence)
                events.append(
                    Event(
                        type=EventType.TOOL_RESULT,
                        data={"evidence": evidence.model_dump()},
                        step=step,
                    )
                )
        else:
            events.append(
                Event(
                    type=EventType.DONE,
                    data={"result": assistant.content, "steps": step},
                    step=step,
                )
            )

        tool_messages = [
            Message(
                role="tool",
                content=str(e.output if e.ok else e.error),
                tool_call_id=e.tool_call.id,
                name=e.tool_call.name,
            )
            for e in evidences
        ]
        new_state = state.model_copy(
            update={
                "step": step,
                "usage": usage,
                "messages": [*state.messages, assistant, *tool_messages],
                "status": (AgentStatus.RUNNING if response.tool_calls else AgentStatus.SUCCEEDED),
            }
        )
        return new_state, events

    async def drive(self, state: AgentState) -> AsyncIterator[tuple[Event, AgentState]]:
        """事件引擎：逐步产出 (事件, 事件后快照)。PAUSED 产出后终止，等待 resume。"""
        max_steps = self._config.control.max_steps
        while True:
            if self.pause_requested:  # F3：步边界消费
                self.pause_requested = False
                state = state.model_copy(update={"status": AgentStatus.PAUSED})
                yield (
                    Event(type=EventType.PAUSED, data={"step": state.step}, step=state.step),
                    state,
                )
                return
            if state.step >= max_steps:  # F2
                state = state.model_copy(
                    update={
                        "status": AgentStatus.FAILED,
                        "error": f"max_steps exceeded: {max_steps}",
                    }
                )
                yield (
                    Event(
                        type=EventType.ERROR,
                        data={"reason": "max_steps_exceeded", "limit": max_steps},
                        step=state.step,
                    ),
                    state,
                )
                return
            state, events = await self.advance(state)
            for event in events:
                yield event, state
            if events and events[-1].type in TERMINAL_EVENTS:  # F4
                return
