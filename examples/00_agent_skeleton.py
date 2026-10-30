"""P0 空转示例：包可安装、可导入，Agent 可构造（不执行）。

运行：python examples/00_agent_skeleton.py
"""

from stableagent import Agent, Config, EventType
from stableagent.core.state import CONTRACT_VERSION, Event

agent = Agent(name="demo", provider="openai:gpt-4o", config=Config())

event = Event(type=EventType.THINKING, data={"text": "契约自检"}, step=0)

print(f"Agent 构造成功: name={agent.name} provider={agent.provider}")
print(f"contract={CONTRACT_VERSION} 事件样例={event.model_dump_json()}")
