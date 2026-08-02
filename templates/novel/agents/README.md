# agents/ — 小说专属 Agent 实现

框架只定义接口（`framework.agent_base.Agent`），本目录放这部小说的角色实现。

## Tier1（主角等戏份重角色）—— 自己写

```python
# agents/agent_主角.py
from framework.agent_base import Agent, AgentState, Memory
from framework.percept_filter import PerceptionFilter
from framework.api import call_structured


class ProtagonistAgent(Agent):
    def __init__(self, name, state, vault_reader, retriever):
        super().__init__(name, state, vault_reader, retriever)
        self.percept_filter = PerceptionFilter(...)

    @classmethod
    def from_vault_state(cls, vault_state, vault_reader, memory_store):
        # 从 vault 状态构造：state_key 对应 novel_config 里 cast 的 state_key
        ...

    def perceive(self, world_structured): ...
    def decide(self, perceived, arc_config): ...
```

## Tier2（配角）—— 不用写代码

novel_config cast 里 `impl: "framework.agent_lite", class: "LiteAgent"` 即可。
LiteAgent 是配置式轻量角色：有信念 + 目标 + 行动线，无持久记忆流。

## 参考

- `framework/agent_base.py` — Agent 基类：记忆检索 + 信念修正 + 弧末反思
- `framework/percept_filter.py` — 感知过滤（不同角色看到不同世界）
