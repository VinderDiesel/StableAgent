# StableAgent

**A lightweight, production-grade Agent runtime for Python.**
Two things it optimises for that most Agent frameworks don't: *reliable execution* (a long task
survives a crash) and *context engineering* (token efficiency you can measure, not hope for).

[![CI](https://github.com/VinderDiesel/StableAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/VinderDiesel/StableAgent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![Contract](https://img.shields.io/badge/public_contract-v1.1.0-orange)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-3-green)
![Coverage](https://img.shields.io/badge/coverage-98.2%25-brightgreen)

A demo proves an Agent *can run*. Production needs an Agent that *finishes* — after a rate limit,
after a pod eviction, after `kill -9`. StableAgent makes that a property of the runtime instead of
something every application re-implements badly:

- the step-transition loop is a **pure function** over a serialisable snapshot — no hidden state in
  the interpreter loop;
- snapshots are written at **step boundaries** with a versioned, migratable contract;
- recovery is a first-class entry point (`recover` / `stream_recover`), not a try/except you write.

```console
$ python examples/03_checkpoint_resume.py --phase selftest --dir .demo-checkpoint --delay 0.2
...
RESUME_RESULT status=succeeded steps=4 provider_calls=2
SELFTEST ok=True killed=True final=succeeded
```

The process was SIGKILLed after step 2. The restart resumed from the SQLite snapshot and needed
exactly 2 provider calls — one per remaining step, zero re-execution of finished work. That command
is also a CI test (`tests/integration/test_crash_recovery.py`), so the headline claim can't rot.

---

## Contents

- [Why these defaults](#why-these-defaults)
- [Install](#install)
- [Quickstart](#quickstart)
- [Add crash recovery to an Agent](#add-crash-recovery-to-an-agent)
- [Real models](#real-models)
- [How recovery decides](#how-recovery-decides)
- [Architecture](#architecture)
- [Measured overhead](#measured-overhead)
- [Development](#development)
- [Roadmap](#roadmap)
- [Design documentation](#design-documentation)
- [License](#license)

---

## Why these defaults

| | |
|---|---|
| **Crash-safe checkpoints** | Memory / SQLite / Redis behind one 4-method protocol; per-run write lock, idempotent `(run_id, step)` overwrite, history pruning. The Redis client is injected — no hard `redis` dependency. |
| **Failure self-healing** | Retryable / non-retryable / needs-a-human classification, capped geometric backoff, fallback Provider rotation. Needs-a-human parks the run as `PAUSED` instead of burning retries. |
| **Deterministic event stream** | `thinking` / `tool_call` / `tool_result` / `checkpoint` / `retry` / `paused` / `error` / `done` — exactly one terminal event, always last, even after injected ones. |
| **Versioned state** | `AgentState` is the only persistence unit and carries a contract version; a migration chain reads old snapshots, unknown or future versions fail loudly. |
| **Clean pause/resume** | `pause()` takes effect at a step boundary and is a no-op on terminal runs; `resume()` on a non-paused run raises instead of inventing behaviour. |
| **Small core** | 3 runtime dependencies (`pydantic`, `anyio`, `httpx`), ~1.9k lines of runtime, no telemetry framework, no DI container, no plugin registry to learn first. |

## Install

```bash
git clone https://github.com/VinderDiesel/StableAgent.git
cd StableAgent
pip install -e ".[dev]"          # tests, ruff, mypy
```

Not on PyPI yet — install from source until the first release is published.

## Quickstart

Runnable on any machine with no API key: the Provider below is scripted, so you see the exact
event stream a real run produces.

```python
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from stableagent import (
    Agent, Config, Message, Provider, ProviderChunk, ProviderResponse,
    Tool, ToolCall, ToolRegistry, ToolResult,
)


class WordCount(Tool):
    name = "word_count"
    description = "count characters in a corpus directory"
    parameters = {"type": "object", "properties": {"dir": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=f"{arguments['dir']}: 8612 chars")


class ScriptedProvider(Provider):
    """No API key required: one tool call, then a final answer."""

    def __init__(self) -> None:
        self._script = [
            ProviderResponse(
                tool_calls=[ToolCall(id="c1", name="word_count", arguments={"dir": "corpus"})]
            ),
            ProviderResponse(content="Done: the corpus holds 8612 characters."),
        ]
        self._index = 0

    def complete(self, messages, tools=None, **options) -> ProviderResponse:
        self._index += 1
        return self._script[self._index - 1]

    async def acomplete(self, messages, tools=None, **options) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(self, messages, tools=None, **options) -> AsyncIterator[ProviderChunk]:
        response = self.complete(messages, tools, **options)
        yield ProviderChunk(text=response.content or "", done=True)


async def main() -> None:
    registry = ToolRegistry()
    registry.register(WordCount())

    agent = Agent(name="researcher", provider=ScriptedProvider(), config=Config(), registry=registry)

    async for event in agent.stream("count the corpus"):
        print(event.type.value, event.step, event.data)


asyncio.run(main())
```

Output:

```
tool_call 1 {'tool_call': {'id': 'c1', 'name': 'word_count', 'arguments': {'dir': 'corpus'}}}
tool_result 1 {'evidence': {'tool_call': {...}, 'ok': True, 'output': 'corpus: 8612 chars', 'error': None}}
thinking 2 {'text': 'Done: the corpus holds 8612 characters.'}
done 2 {'result': 'Done: the corpus holds 8612 characters.', 'steps': 2}
```

Three call shapes on `Agent`, all backed by the same loop: `run()` / `arun()` return an
`AgentState`, `stream()` yields `Event`s.

## Add crash recovery to an Agent

```python
from stableagent import Agent, Config

agent = Agent(
    name="researcher",
    provider=my_provider,
    registry=my_registry,
    config=Config(checkpoint={
        "backend": "sqlite",
        "sqlite_path": "checkpoints.sqlite",
        "auto_save_every": 1,        # persist every step: lose at most one on crash
        "recovery_strategy": "resume",
    }),
)

state = await agent.arun("long research task")

# another process, later, after the crash:
history = await agent.checkpoint_history(run_id)   # list[CheckpointRecord]
state = await agent.arecover(run_id)               # run_id=None -> latest unfinished run
```

Verified end to end (same `ScriptedProvider` as above, SQLite backend):

```
first run: succeeded steps: 3 run_id: f20f7b60
snapshots: [(1, '1.1.0'), (2, '1.1.0'), (3, '1.1.0')]
recovered: succeeded steps: 3
```

Recovery streams too — `stream_recover()` replays the remaining steps as events, and the
concatenation of "crashed segment" + "recovered segment" is asserted event-for-event equal to an
uninterrupted run
(`tests/unit/test_agent_recovery.py::test_d4_recovered_stream_matches_uninterrupted_stream_event_by_event`).

## Real models

```python
import os
from stableagent import Agent, Config, OpenAIProvider
from stableagent.providers.openai import DEFAULT_BASE_URL

agent = Agent(
    name="researcher",
    provider=OpenAIProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),  # proxy / compatible gateway
    ),
    config=Config(),
    registry=my_registry,
)
```

`OpenAIProvider.from_config(Config().provider)` does the same from the `provider` section of
`Config` and threads `request_timeout_seconds` into the httpx client. `stream()` yields function
calls as they arrive, so tool calls are not hidden behind a blocking completion.

Adding a Provider means implementing three methods (`complete` / `acomplete` / `stream`) against
`Provider` — see `stableagent/providers/base.py`.

## How recovery decides

`recovery_strategy` decides how much of the reliability layer is switched on:

| Value | Behaviour |
|---|---|
| `none` (default) | No store is built. Errors are terminal — the Agent behaves exactly as it did before P2. |
| `resume` | Snapshots at step boundaries; `recover` continues a crashed run from the next step, and a `needs_human` failure parks the run as `PAUSED`. No automatic retries. |
| `retry_with_fallback` | Everything above, plus bounded retries with backoff and Provider rotation. |

`classify_error` decides the *shape* of the response to a failure, and status codes beat type names
beat message markers:

| Class | Typical cause | Action under `retry_with_fallback` |
|---|---|---|
| `retryable` | timeouts, connection resets, 429 / 5xx | backoff `base × factor^n`, capped at `max_delay_seconds`, up to `retry_max_attempts` calls in total |
| `needs_human` | 403 / permission errors / 451 | snapshot the run, emit `paused`, stop — no retry storm |
| `non_retryable` | 4xx schema errors, unrecognised failures | `error` event + `FAILED` state, terminal and last |

Retries rotate Providers by `retry_no % provider_count`, so with a `fallback=` Provider configured
the first retry already lands on the fallback. Backoff sleeps are injectable (`sleep=`), so the whole
recovery path is tested without wall-clock waiting.

## Architecture

```
stableagent/
  core/
    state.py         Event / Message / AgentState / Config   (contract, versioned)
    loop.py          step-transition engine                  (pure; P2 added nothing here)
    agent.py         Agent facade + reliability outer layer  (checkpoint & recovery wiring)
    checkpoint/      manager.py · storage.py · recovery.py
    errors.py        error taxonomy
  providers/         base.py (3 shapes) · openai.py
  tools/             registry.py (Tool → ToolResult → Evidence)
  context/           assembly seam (P3 lives here)
  planning/ memory/ sandbox/ transport/   (later phases)
```

Layering rules the code actually enforces, each with a test behind it:

1. `core/loop.py` never learns that checkpoints exist — P2 shipped with a zero-line diff there.
2. Recovery, retries and `checkpoint`/`retry` events are injected by the `Agent` outer layer.
3. Exactly one terminal event (`done` or `error`) and it is last.
4. A store implements `save` / `load` / `records` / `delete` — nothing more.
5. Optional infrastructure (Redis) arrives by injection, never as a hard dependency.

## Measured overhead

`benchmarks/recovery_time_benchmark.py` — offline, SQLite, default 20 steps × 3 rounds. These are
wall-clock numbers on a laptop; they move with the disk, so run the benchmark rather than trusting
the digits:

| `auto_save_every` | writes per run | end-to-end vs. checkpointing disabled |
|---|---|---|
| `0` (terminal save only) | 1 | 1.0× |
| `5` | 4 | ~1.5× |
| `2` | 10 | ~3.2× |
| `1` | 20 | ~5.6–7.8× |

A single write costs ~0.5 ms; serialising the very same snapshot costs 0.005 ms — **the cost is the
fsync, not the encoding.** Reading the newest snapshot back (`load` → migrate → validate) costs
45–120 µs: it tracks how many snapshots the store still keeps, because history pruning touches
those rows, not how large the restored snapshot is — 40 snapshots of 8 KB pruned to 3 read back in
46 µs, the same as 5 snapshots of 1 KB (53 µs).

## Development

```bash
ruff check stableagent tests examples benchmarks
ruff format --check stableagent tests examples benchmarks
mypy                        # config in pyproject, python_version = 3.10
pytest                      # 126 passed, 1 skipped; fail_under = 70
```

CI runs that matrix on Python 3.10 and 3.12, plus a stricter gate: the heart modules
(`core/loop.py`, `core/checkpoint/*`, `core/errors.py`) must stay **≥90%** covered — currently
96.8–100%. The one skipped test needs a live Redis and only runs when `STABLEAGENT_REDIS_URL` is
set.

Before opening a PR: keep `core/loop.py` free of persistence concerns, add the contract change to
`CHANGELOG.md`, and if it touches a public type, follow the freeze rules in
`docs/design-decisions/00-contracts.md` (from P2 onwards: extensions only — defaulted fields, new
enum members, new config sections).

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| P0 | Contracts, package skeleton, CI | ✅ done |
| P1 | Executable core: loop, OpenAI provider, tool loop, pause/resume | ✅ done |
| P2 | Checkpoint & recovery: 3 backends, self-healing, snapshot versioning | ✅ done — 34/34, CI matrix green on 3.10 & 3.12 |
| P3 | Context engineering: compression, dynamic capability view, token budget | 🔜 next |
| P4 | Planning & SubAgent: decomposition, DAG, concurrent scheduling | ⏳ |
| P5 | Capability substrate: MCP, sandbox, memory | ⏳ |
| P6 | Control & hardening: multi-model, observability, docs site | ⏳ |

Per-phase acceptance evidence: `docs/p1-dod-checklist.md`, `docs/p2-dod-checklist.md`.

## Design documentation

- `docs/design-decisions/00-contracts.md` — the public contract, its invariants, and what is frozen
- `docs/design-decisions/01-loop-runtime.md` — ADR-01: why the loop is a pure step transition
- `docs/design-decisions/02-checkpoint.md` — ADR-02: consistency, concurrency, migration, retry boundaries
- `docs/blog/01-checkpoint-deep-dive.md` — the checkpoint design written up as an essay
- `docs/Roadmap.md` — phases, DoD gates, and the quality baseline that only ratchets up

Design docs are Chinese; code and this README are English.

## License

Apache License 2.0 — full text in [`LICENSE`](LICENSE).

```
Copyright 2026 yangxinlong

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Attribution and the patent grant work as in any Apache-2.0 project; keep this notice when you
redistribute.
