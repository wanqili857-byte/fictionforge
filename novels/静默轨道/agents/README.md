# agents/ — 静默轨道专属 agent（待写）

当前适配实证只验证 gen.py 单管线（spec → 正文）。
引擎多 agent 路径需要写 Tier1 主角 agent：

```python
# agents/agent_luli.py
from framework.agent_base import Agent
from framework.percept_filter import PerceptionFilter
from framework.api import call_structured


class LuliAgent(Agent):
    @classmethod
    def from_vault_state(cls, vault_state, vault_reader, memory_store):
        ...  # 从 luli_state 构造，参考 novels/示例/agents/agent_linmo.py
```

Tier2（零号）用 `framework.agent_lite`，novel_config 已配置，无需代码。
