"""
percept_filter.py — 信息不对称核心实现。

世界 Agent 输出三层结构（public / traces / hidden）。
感知过滤器决定角色 Agent 能看到哪些层：
  - public: 无条件可见
  - traces: 满足感知条件时可见（地点/变异阶段/关系）
  - hidden: 永远不可见（仅 Narrator 使用）
"""

from __future__ import annotations
from typing import Optional
from .agent_base import AgentState


class PerceptionFilter:
    """基于规则的感知过滤器。不调用 LLM。"""

    # 默认感知阶段顺序，可在实例化时覆盖
    DEFAULT_STAGE_ORDER = [
        "", "初现", "怀疑", "确认期",
        "紧迫生存意识", "全面变异", "超越",
    ]

    def __init__(self, stage_order: Optional[list[str]] = None):
        self.stage_order = stage_order or list(self.DEFAULT_STAGE_ORDER)

    def filter(self, world_output: dict, agent: AgentState) -> dict:
        """返回该 agent 能看到的 filtered world。"""
        visible = {
            "public": world_output.get("public", {}),
            "traces": [],
            "hidden": [],  # 永远空——agent 看不到 hidden
        }

        for trace in world_output.get("traces", []):
            if self._meets_condition(trace, agent):
                visible["traces"].append(trace)

        return visible

    def _meets_condition(self, trace: dict, agent: AgentState) -> bool:
        """判断一条 trace 是否可被该 agent 感知。"""
        # 地点条件
        trace_locations = trace.get("locations", [])
        if trace_locations and agent.location not in trace_locations:
            return False

        # 变异阶段条件（trace 要求的 min_mutation_phase）
        min_phase = trace.get("min_mutation_phase", 0)
        if agent.mutation_phase < min_phase:
            return False

        # 关系信任条件（作用于涉及特定角色的 traces）
        related_char = trace.get("requires_relationship_with")
        min_trust = trace.get("min_trust", 0.0)
        if related_char and min_trust > 0:
            rel = agent.relationships.get(related_char, {})
            trust = rel.get("trust", 0.0) if isinstance(rel, dict) else 0.0
            if trust < min_trust:
                return False

        # 感知阶段条件
        required_stage = trace.get("min_perception_stage")
        if required_stage:
            try:
                agent_idx = self.stage_order.index(agent.perception_stage)
                req_idx = self.stage_order.index(required_stage)
                if agent_idx < req_idx:
                    return False
            except ValueError:
                pass  # 未知阶段名，放行

        return True

    def format_for_agent(self, filtered: dict) -> str:
        """将过滤后的世界状态格式化为 prompt 文本块。"""
        parts = []

        pub = filtered.get("public", {})
        if pub:
            parts.append("## 你能看到的世界")
            for k, v in pub.items():
                if isinstance(v, list):
                    parts.append(f"- {k}: {'; '.join(str(x) for x in v)}")
                elif isinstance(v, dict):
                    parts.append(f"- {k}: {v}")
                else:
                    parts.append(f"- {k}: {v}")

        traces = filtered.get("traces", [])
        if traces:
            parts.append("\n## 你察觉到的一些异常")
            for t in traces:
                parts.append(f"- {t.get('description', str(t))}")

        return "\n".join(parts) if parts else "（无可见信息）"

    @staticmethod
    def meets_perception_condition(trace: dict, agent: AgentState) -> bool:
        """静态方法，便于外部直接调用。"""
        return PerceptionFilter()._meets_condition(trace, agent)
