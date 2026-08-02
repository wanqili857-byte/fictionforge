"""
scene_director.py — 两阶段对手戏：共享场景的交互解析。

阶段1（tick_runner）：各 agent 独立 propose 行动线（并行，互不见对方输出）。
阶段2（本模块）：检测同天同地的共享场景（≥2 角色在场），对每个场景做一次
LLM「对手戏导演」调用——把双方意图合成一幕真实的对手戏（谁先开口、如何回应、
各自的行动和内心），替换掉该场景里各 agent 的独立提案。

失败/无共享场景时原样返回，不丢数据。共享场景中的动作对主角可见
（主角在同场），覆盖原 agent 的可见性标记。
"""

from __future__ import annotations
from typing import Optional

from lib.log import get_logger
from .api import call_structured

log = get_logger("engine.scene_director")

# 可见性键：新键优先，兼容旧键
_VISIBILITY_KEYS = ("visible_to_protagonist", "visible_to_linmo")


_SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "day": {"type": "integer"},
        "location": {"type": "string"},
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "角色名（与下方意图里的名字一致）"},
                    "time": {"type": "string"},
                    "action": {
                        "type": "string",
                        "description": "这一幕里该角色的具体动作/对白，2-3 句，写成具体的戏（物体、动作、对白、身体反应），不是日程表",
                    },
                    "inner": {
                        "type": "string",
                        "description": "该角色的内心独白（A层），1-2 句，锋利不恶毒，没有就空串",
                    },
                },
                "required": ["agent", "action"],
            },
        },
    },
    "required": ["day", "location", "turns"],
}


_SYSTEM_TEMPLATE = """你是一部小说的「对手戏导演」。

以下多个角色在同一场景同时出现。基于他们的独立意图，写这一幕真实的对戏：
谁先开口、对方怎么回应、各自的行动和内心。让双方真的在对话和反应，
而不是各自独白。

## 场景
{scene_context}

## 角色意图
{agent_intents}

## 世界状态
{world_state}

## 规则
1. 禁止词：「{forbidden_words}」——绝对不能用
2. 信息只能通过合理渠道获取——角色不能"恰好知道"对方没说过的事
3. 每个 turn 写一幕具体的戏：动作、对白、身体反应，不写"他们聊了聊"
4. inner 是内心独白，锋利不恶毒，1-2 句，没有就空串
5. 输出严格 JSON 符合给定 schema"""


def find_shared_scenes(agent_outputs: dict) -> list[dict]:
    """检测共享场景：(天, 地点) → 参与角色列表。返回 ≥2 角色的场景。

    纯逻辑，不调用 LLM。agent_outputs: {agent_name: {actions: [...]}}。
    """
    by_scene: dict[tuple, dict] = {}
    for agent_name, output in agent_outputs.items():
        for act in output.get("actions", []):
            key = (act.get("day", 0), act.get("location", "?"))
            sc = by_scene.setdefault(key, {
                "day": key[0],
                "location": key[1],
                "agents": [],
                "actions": [],
            })
            if agent_name not in sc["agents"]:
                sc["agents"].append(agent_name)
            sc["actions"].append({"agent": agent_name, "act": act})
    return [s for s in by_scene.values() if len(s["agents"]) >= 2]


def resolve_shared_scenes(agent_outputs: dict,
                          world_structured: Optional[dict] = None,
                          forbidden_words: Optional[list] = None,
                          api_call_fn=None) -> dict:
    """对共享场景做交互解析，返回修正后的 agent_outputs。

    每个共享场景一次 LLM 调用（api_call_fn 便于测试 mock）。
    调用失败/无共享场景 → 原样返回（浅拷贝，不污染原 dict）。
    """
    shared = find_shared_scenes(agent_outputs)
    if not shared:
        return agent_outputs

    api_fn = api_call_fn or call_structured
    forbidden = "」「".join(forbidden_words) if forbidden_words else "突然」「忽然」「只见"
    world_text = _format_world(world_structured)

    result = {name: dict(out) for name, out in agent_outputs.items()}

    for scene in shared:
        turns = _resolve_one_scene(scene, world_text, forbidden, api_fn)
        if not turns:
            log.warning(f"  [scene_director] 场景解析失败，保留独立提案: "
                        f"第{scene['day']}天 @{scene['location']}")
            continue
        _apply_turns(result, scene, turns)
        log.info(f"  [scene_director] 共享场景已解析: 第{scene['day']}天 "
                 f"@{scene['location']}（{len(turns)} 个 turn）")

    return result


def _format_world(world_structured: Optional[dict]) -> str:
    """世界状态压缩为一段文本。无输入返回占位。"""
    if not world_structured:
        return "（无额外上下文）"
    pub = world_structured.get("public", {})
    parts = []
    for k, v in pub.items():
        if isinstance(v, list):
            if v:
                parts.append(f"- {k}: {'; '.join(str(x) for x in v[:3])}")
        elif isinstance(v, dict):
            if v:
                parts.append(f"- {k}: {v}")
        elif v:
            parts.append(f"- {k}: {v}")
    return "\n".join(parts) if parts else "（无可见异常）"


def _resolve_one_scene(scene: dict, world_text: str, forbidden: str,
                       api_fn) -> Optional[list[dict]]:
    """对单个共享场景调用 LLM，返回 turns 列表。失败返回 None。"""
    intents = []
    for a in scene["actions"]:
        act = a["act"]
        line = f"- {a['agent']}：{act.get('action', '')}"
        inner = act.get("inner", "")
        if inner:
            line += f"　心想：{inner}"
        intents.append(line)

    system = _SYSTEM_TEMPLATE.format(
        scene_context=f"第{scene['day']}天 @{scene['location']}",
        agent_intents="\n".join(intents),
        world_state=world_text,
        forbidden_words=forbidden,
    )
    user = f"请输出第{scene['day']}天 @{scene['location']} 这一幕的对戏。"

    result = api_fn(system, user, _SCENE_SCHEMA, route_key="agent", temperature=0.7)
    if not result or not result.get("turns"):
        return None
    turns = result["turns"]
    if not isinstance(turns, list) or not turns:
        return None
    return turns


def _apply_turns(agent_outputs: dict, scene: dict, turns: list[dict]):
    """把解析后的对手戏 turns 写回各 agent 的 actions。

    移除该场景里各 agent 的独立提案，插入解析后的 turn。
    共享场景 = 主角在场，涉及可见性字段的 agent 一律置为可见。
    """
    day, loc = scene["day"], scene["location"]
    for agent in scene["agents"]:
        out = agent_outputs.get(agent)
        if out is None:
            continue
        acts = out.get("actions", [])
        # 该 agent 是否用了可见性字段（决定解析后 turn 是否需要补可见性）
        uses_visibility = any(
            any(k in a for k in _VISIBILITY_KEYS) for a in acts
        )
        kept = [a for a in acts
                if not (a.get("day") == day and a.get("location") == loc)]
        new_entries = []
        for t in turns:
            if t.get("agent") != agent:
                continue
            entry = {
                "day": day,
                "time": t.get("time", ""),
                "location": loc,
                "action": t.get("action", ""),
                "inner": t.get("inner", ""),
            }
            if uses_visibility:
                entry["visible_to_protagonist"] = True
            new_entries.append(entry)
        out["actions"] = kept + new_entries
