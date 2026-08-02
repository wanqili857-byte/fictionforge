"""
agent_lite.py — 轻量 Agent（Tier 2，配置式）。

用于戏份有限但需独立信念的小角色（配角等）。

特点：
  - 无持久 memory stream（每次激活从 vault snapshot 重建）
  - 有信念状态 + 目标
  - 单次调用生成整弧行为线
  - 用完即弃，不保留状态跨弧
"""

from __future__ import annotations
from typing import Optional
from .agent_base import Agent, AgentState, Belief
from .api import call_structured


_LITE_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "integer"},
                    "time": {"type": "string"},
                    "location": {"type": "string"},
                    "action": {"type": "string"},
                    "visible_to_protagonist": {
                        "type": "boolean",
                        "description": "false = 主角不知道这一幕（私密轨迹用）",
                    },
                },
                "required": ["day", "time", "location", "action"],
            }
        },
        "beliefs_at_end": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "proposition": {"type": "string"},
                    "confidence": {"type": "number"},
                }
            }
        },
    },
    "required": ["actions"],
}


_SYSTEM_TEMPLATE = """你扮演 {name}。{role_desc}

## 你的基本信息
{profile}

## 当前场景
{context}

## 你感知到的世界
{perceived_world}

## 写作规则
1. 禁止词：「{forbidden_words}」——绝对不能用
2. 信息只能通过合理渠道获取——你不能"恰好知道"某事
3. 叙事线性推进
4. 你的认知水平与设定一致——不要比主角知道更多
"""


# 禁词默认值（无 config 时用）——与 gen.py 质量检查同源
_DEFAULT_FORBIDDEN = ("突然", "忽然", "只见")


class LiteAgent(Agent):
    """轻量 Agent。实例化时传入 profile 完成配置。"""

    def __init__(self, name: str, profile: dict, vault_reader=None,
                 forbidden_words=None):
        state = AgentState(
            name=name,
            role=profile.get("role", ""),
            location=profile.get("current_location", ""),
            mutation_phase=profile.get("mutation_phase", 0),
            perception_stage=profile.get("perception_stage", ""),
            goals={
                "immediate": profile.get("goals", {}).get("immediate", ""),
                "arc": profile.get("goals", {}).get("arc", ""),
                "story": "",
            },
        )
        super().__init__(name, state, vault_reader)
        self.profile = profile
        self.forbidden_words = tuple(forbidden_words) if forbidden_words else _DEFAULT_FORBIDDEN

    def perceive(self, world_output: dict) -> dict:
        """Lite Agent 能看到全部 public 信息。"""
        return {"public": world_output.get("public", {}),
                "traces": world_output.get("traces", []),
                "hidden": []}

    def build_system_prompt(self, perceived: dict) -> str:
        profile_lines = []
        for k, v in self.profile.items():
            if k in ("goals", "state_history", "relationships"):
                continue
            if isinstance(v, list):
                profile_lines.append(f"- {k}: {'; '.join(str(x) for x in v)}")
            elif isinstance(v, dict):
                profile_lines.append(f"- {k}: {v}")
            else:
                profile_lines.append(f"- {k}: {v}")

        context = f"位置：{self.state.location}"

        # 构建感知到的世界
        perceived_text = ""
        pub = perceived.get("public", {})
        if pub:
            parts = []
            if pub.get("time"):
                parts.append(f"时间: {pub['time']}")
            if pub.get("weather"):
                parts.append(f"天气: {pub['weather']}")
            if pub.get("government_stance"):
                parts.append(f"政府表态: {pub['government_stance']}")
            if pub.get("media_reports"):
                parts.append(f"媒体报道: {'; '.join(pub['media_reports'][:3])}")
            if pub.get("public_behavior"):
                parts.append(f"群众行为: {'; '.join(pub['public_behavior'][:3])}")
            infra = pub.get("infrastructure", {})
            if isinstance(infra, dict):
                power_status = "; ".join(f"{k}={v}" for k, v in infra.get("power", {}).items())
                if power_status:
                    parts.append(f"供电: {power_status}")
            perceived_text = "\n".join(f"- {p}" for p in parts)

        return _SYSTEM_TEMPLATE.format(
            name=self.name,
            role_desc=self.profile.get("key_function", ""),
            profile="\n".join(profile_lines),
            context=context,
            perceived_world=perceived_text or "（你还没注意到什么异常）",
            forbidden_words="」「".join(self.forbidden_words),
        )

    def build_decision_prompt(self, arc_config: dict) -> str:
        # 主角行为提示：通用键 protagonist_hint，兼容旧键 linmo_arc_hint
        hint = (arc_config.get("protagonist_hint")
                or arc_config.get("linmo_arc_hint", "（未知）"))
        return (
            f"## 故事弧设定\n"
            f"时间范围：{arc_config.get('time_start', '?')}"
            f" → {arc_config.get('time_end', '?')}\n"
            f"主角在这个弧里：{hint}\n"
            f"\n"
            f"请输出你这个弧里做的事。如果你和主角有互动/对话，也写出来。"
        )

    def decide(self, perceived: dict, arc_config: dict,
               api_call_fn: callable = None) -> dict:
        api_fn = api_call_fn or call_structured
        system_prompt = self.build_system_prompt(perceived)
        user_prompt = self.build_decision_prompt(arc_config)
        return api_fn(system_prompt, user_prompt, _LITE_SCHEMA, route_key="long")
