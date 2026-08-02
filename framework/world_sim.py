"""
world_sim.py — 世界模拟器（Tier 3，多模块）。

模块：
  - government: 政府决策（公开立场、实际动作、内部认知）
  - media: 媒体报道 vs 压制
  - public_sentiment: 恐慌扩散、谣言、群体行为
  - infrastructure: 电力/通信/交通状态
  - religion_marginal: 宗教/边缘群体对异常的解释

初始实现：用一个 LLM 调用输出全部模块（便宜）。
后续可拆分为独立调用（当某模块需要更细粒度状态时）。
"""

from __future__ import annotations
import os
from typing import Optional
from .api import call_structured
from . import novel_config as novel_config_mod

# sys.path 由 engine.__init__.py 统一注册，本模块无需再操作


_WORLD_SCHEMA = {
    "type": "object",
    "properties": {
        "government": {
            "type": "object",
            "properties": {
                "public_stance": {"type": "string"},
                "actions_taken": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "internal_knowledge": {
                    "type": "string",
                    "description": "政府内部实际知道什么",
                },
                "next_expected_move": {"type": "string"},
            },
            "required": ["public_stance", "actions_taken"],
        },
        "media": {
            "type": "object",
            "properties": {
                "reported": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "媒体报道了什么",
                },
                "suppressed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "什么被压下/不报道",
                },
                "narrative_framing": {
                    "type": "string",
                    "description": "报道框架——怎么解释这些事",
                },
            },
            "required": ["reported", "narrative_framing"],
        },
        "public_sentiment": {
            "type": "object",
            "properties": {
                "panic_level": {"type": "integer", "minimum": 0, "maximum": 10},
                "rumors": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "collective_behavior": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "群众行为（囤货/提前放假/逃离等）",
                },
                "trust_in_government": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10,
                },
            },
            "required": ["panic_level", "collective_behavior"],
        },
        "infrastructure": {
            "type": "object",
            "properties": {
                "power": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "各区域供电状态: normal/flckr/down",
                },
                "communications": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "各区域通信状态: normal/delay/down",
                },
                "transport": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "交通状态",
                },
            },
        },
        "religion_marginal": {
            "type": "object",
            "properties": {
                "interpretations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "宗教/边缘群体对事件的解释",
                },
                "activity_level": {"type": "integer", "minimum": 0, "maximum": 10},
            },
        },
        "public_traces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "locations": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "min_mutation_phase": {"type": "integer"},
                    "min_perception_stage": {"type": "string"},
                },
            },
            "description": "角色可能感知到的蛛丝马迹（用于 percept_filter）",
        },
        "hidden_truths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "这个弧里发生的、无人知晓的真相",
        },
    },
    "required": ["government", "public_sentiment", "infrastructure"],
}


_SYSTEM_TEMPLATE = """你是一个小说的世界模拟器。

你的任务是模拟当故事的核心异常发生时，世界的各个层面如何反应。

## 当前世界状态
{world_state}

## 时间范围
{time_range}

## 当前故事阶段
{story_stage}

## 张力目标
本弧张力目标 {tension_target}/10。恐慌等级、政府动作、媒体调门、基础设施损耗都应大致匹配这个强度：
- 3-4：有人注意到了，官方否认
- 5-6：抢购/停课/局部撤离，政府开始内部动作
- 7-8：大规模逃离、电网不稳、媒体开始压不住
- 9-10：秩序瓦解边缘

## 已知事件/异常
{known_events}

## 物理约束
1. 未发生异常的区域遵循现实物理定律
2. 异常只在设定发生异常的区域发生
3. 政府/媒体/民众的反应应符合现实中类似规模的灾难/恐慌中的行为模式

## 输出要求
1. 每个模块的输出要有具体细节——不要笼统说"政府否认了"
   说具体：谁在什么场合说了什么、做了什么具体决策
2. public_traces 是普通人可能察觉到的蛛丝马迹
   每个 trace 标注需要什么条件才能感知到
3. hidden_truths 是这个弧里真正发生的但无人知晓的事
"""


class WorldSimulator:
    """世界模拟器。每次 tick 调用一次 LLM 输出全体模块。"""

    def __init__(self, vault_reader=None):
        self.vault_reader = vault_reader
        # 从 vault 推导小说目录 + novel_config（世界观注入的唯一来源）
        self.novel_dir = novel_config_mod.novel_dir_from_vault(vault_reader)
        self.novel_config = (novel_config_mod.load(self.novel_dir)
                             if self.novel_dir else {})

    def run(self, arc_config: dict, vault_state: Optional[dict] = None) -> dict:
        """执行世界模拟。返回各模块决策。"""
        # 构造 vault 上下文
        world_context = self._build_world_context(arc_config, vault_state)

        system_prompt = _SYSTEM_TEMPLATE.format(
            world_state=world_context.get("world_state", "（无）"),
            time_range=f"{arc_config.get('time_start', '?')}"
                       f" → {arc_config.get('time_end', '?')}",
            story_stage=world_context.get("story_stage", "缓冲期"),
            tension_target=arc_config.get("tension_target", 5),
            known_events=world_context.get("known_events", "（无已知事件）"),
        )

        user_prompt = (
            f"请输出在这个故事弧中，世界的各个层面如何反应。\n"
            f"涵盖章节：{arc_config.get('chapters', [])}\n"
            f"活跃区域：{arc_config.get('active_regions', [])}\n"
        )

        result = call_structured(system_prompt, user_prompt, _WORLD_SCHEMA,
                                 route_key="agent", temperature=0.5)
        return result or {}

    def _load_world_lore(self) -> str:
        """读取世界观设定——从 novel_config 的蒸馏字段优先，缺失时通用蒸馏 bible。

        不再硬编码任何小说的概念词：换小说只改内容，framework 不动。
        注入顺序：world_summary + world_core_principle（config，作者蒸馏好）>
        bible/世界观.md 通用蒸馏（去 frontmatter/标题/表格）> 空。
        """
        from lib.bible_utils import load_bible_file, distill_world_lore

        quality = (self.novel_config or {}).get("quality", {})
        summary = quality.get("world_summary", "")
        core = quality.get("world_core_principle", "")
        if summary or core:
            parts = []
            if summary:
                parts.append(f"世界观：{summary}")
            if core:
                parts.append(f"核心原则：{core}")
            return "\n".join(parts)

        if not self.novel_dir:
            return ""
        text = load_bible_file(self.novel_dir, "世界观.md")
        if not text:
            return ""
        lore = distill_world_lore(text)
        return lore or ""

    def _build_world_context(self, arc_config: dict,
                             vault_state: Optional[dict]) -> dict:
        """从 vault 和 arc_config 构建世界背景。"""
        ctx = {"world_state": "", "story_stage": "", "known_events": ""}

        # 1. 从 bible/世界观.md 加载核心设定（与 gen.py 同源）
        world_lore = self._load_world_lore()

        if vault_state:
            ws = vault_state.get("world_state", {})
            if isinstance(ws, dict):
                ctx["story_stage"] = ws.get("stage", "缓冲期")
                pub_events = [e for e in ws.get("public_events", []) if e]
                ctx["world_state"] = f"阶段: {ws.get('stage', '?')}\n"
                if world_lore:
                    ctx["world_state"] += world_lore + "\n"
                if pub_events:
                    ctx["world_state"] += f"公众事件: {'; '.join(pub_events)}\n"
                hidden = ws.get("hidden_truths", [])
                if hidden:
                    ctx["world_state"] += f"已发现的隐藏真相: {'; '.join(hidden[:3])}"

                # 区域效应
                regions = ws.get("region_effects", {})
                if isinstance(regions, dict):
                    for rname, reff in regions.items():
                        if isinstance(reff, dict):
                            ctx["world_state"] += (
                                f"\n{rname}: 变异等级{reff.get('mutation_level', 0)}, "
                                f"异常: {'; '.join(reff.get('active_anomalies', []))}"
                            )

        # known_events: 从 vault 的关键事件中取
        if vault_state and vault_state.get("key_events"):
            ctx["known_events"] = "\n".join(
                f"- {ev}" for ev in vault_state["key_events"]
            )

        return ctx
