#!/usr/bin/env python3
"""
test_engine_core.py — 引擎核心组件单元测试。

无 LLM / 无 I/O 依赖。纯逻辑覆盖：
- MemoryRetriever（记忆检索排序）
- PerceptionFilter（感知过滤 + stage_order）
- EnvState（环境状态规则 + 恐慌计算）
- MemoryStore（序列化/反序列化，使用 tempdir）

用法:
    python3 tests/test_engine_core.py
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from framework.agent_base import Agent, AgentState, Memory, MemoryRetriever
from framework.percept_filter import PerceptionFilter
from framework.env_state import EnvState
from framework.vault_sync import MemoryStore
from framework.scene_director import (
    find_shared_scenes, resolve_shared_scenes,
)


def test_memory_retriever_importance():
    """重要性 8+ 排最高，3- 排最低。"""
    retriever = MemoryRetriever()
    mems = [
        Memory("1", "重要", "perception", 1, 9),
        Memory("2", "普通", "perception", 1, 3),
        Memory("3", "中等", "perception", 1, 6),
    ]
    result = retriever.retrieve(mems, current_chapter=2)
    ids = [m.id for m in result]
    assert ids[0] == "1", f"importance=9 应排第一, got {ids}"
    assert ids.index("3") < ids.index("2"), "importance=6 应排在 3 前面"
    print("  ✓ MemoryRetriever: importance 排序")


def test_memory_retriever_recent():
    """最近 2 章的记忆有加分。"""
    retriever = MemoryRetriever(recent_chapters=2)
    mems = [
        Memory("old", "旧记忆", "perception", 10, 6),
        Memory("new", "新记忆", "perception", 19, 6),
    ]
    result = retriever.retrieve(mems, current_chapter=20)
    ids = [m.id for m in result]
    assert "new" in ids
    assert "old" in ids  # importance=6 仍然会被检索
    # 新 > 旧
    new_idx = ids.index("new")
    old_idx = ids.index("old")
    assert new_idx < old_idx, "最近记忆应排在前面"
    print("  ✓ MemoryRetriever: 时效性排序")


def test_memory_retriever_tag_match():
    """标签匹配加分。"""
    retriever = MemoryRetriever()
    mems = [
        Memory("tagged", "有标签", "perception", 5, 2, tags=["异常"]),
        Memory("plain", "无标签", "perception", 5, 2),
    ]
    result = retriever.retrieve(mems, current_chapter=6, tags=["异常"])
    ids = [m.id for m in result]
    assert "tagged" in ids, "标签匹配应被检索"
    # same importance + same recency → tagged one should rank higher
    assert ids[0] == "tagged", f"标签匹配应排前, got {ids}"
    print("  ✓ MemoryRetriever: 标签匹配")


def test_memory_retriever_max():
    """检索上限 20 条。"""
    retriever = MemoryRetriever(max_memories=5)
    mems = [Memory(str(i), f"记忆{i}", "perception", 1, 10) for i in range(100)]
    result = retriever.retrieve(mems, current_chapter=2)
    assert len(result) == 5, f"上限应为 5, 实际 {len(result)}"
    print("  ✓ MemoryRetriever: 检索上限")


def test_memory_retriever_empty():
    """空输入返回空列表。"""
    retriever = MemoryRetriever()
    assert retriever.retrieve([], current_chapter=1) == []
    assert retriever.format_for_prompt([]) == "（无相关记忆）"
    print("  ✓ MemoryRetriever: 空输入")


def test_percept_filter_public_always_visible():
    """public 层始终可见。"""
    pf = PerceptionFilter()
    output = {"public": {"weather": "晴"}, "traces": [], "hidden": []}
    agent = AgentState(name="test", location="城市")
    result = pf.filter(output, agent)
    assert result["public"]["weather"] == "晴"
    print("  ✓ PerceptionFilter: public 始终可见")


def test_percept_filter_location_gate():
    """trace 需要地点匹配。"""
    pf = PerceptionFilter()
    traces = [
        {"description": "城市的异常", "locations": ["城市"]},
        {"description": "郊区的异常", "locations": ["郊区"]},
    ]
    output = {"public": {}, "traces": traces, "hidden": []}
    agent = AgentState(name="主角", location="城市")
    result = pf.filter(output, agent)
    assert len(result["traces"]) == 1
    assert result["traces"][0]["description"] == "城市的异常"
    print("  ✓ PerceptionFilter: 地点条件")


def test_percept_filter_stage_order():
    """感知阶段条件按顺序判断。"""
    pf = PerceptionFilter()
    traces = [
        {"description": "怀疑可见", "locations": ["城市"],
         "min_perception_stage": "怀疑"},
    ]
    output = {"public": {}, "traces": traces, "hidden": []}

    # 不满足阶段
    agent1 = AgentState(name="主角", location="城市", perception_stage="初现")
    result1 = pf.filter(output, agent1)
    assert len(result1["traces"]) == 0

    # 满足阶段
    agent2 = AgentState(name="主角", location="城市", perception_stage="怀疑")
    result2 = pf.filter(output, agent2)
    assert len(result2["traces"]) == 1

    # 更高阶段也可见
    agent3 = AgentState(name="主角", location="城市", perception_stage="确认")
    result3 = pf.filter(output, agent3)
    assert len(result3["traces"]) == 1

    print("  ✓ PerceptionFilter: 感知阶段条件")


def test_percept_filter_custom_stage_order():
    """自定义 stage_order 可传入。"""
    custom_order = ["", "初期", "中期", "后期"]
    pf = PerceptionFilter(stage_order=custom_order)
    assert pf.stage_order == custom_order

    traces = [
        {"description": "中期可见", "locations": ["城市"],
         "min_perception_stage": "中期"},
    ]
    output = {"public": {}, "traces": traces, "hidden": []}
    agent = AgentState(name="主角", location="城市", perception_stage="初期")
    result = pf.filter(output, agent)
    assert len(result["traces"]) == 0  # 初期 < 中期

    agent2 = AgentState(name="主角", location="城市", perception_stage="后期")
    result2 = pf.filter(output, agent2)
    assert len(result2["traces"]) == 1  # 后期 >= 中期

    print("  ✓ PerceptionFilter: 自定义 stage_order")


def test_env_state_advance_time():
    """时间推进 + 日夜更新。"""
    env = EnvState()
    assert env.time.absolute_day == 1
    assert env.time.hour == 8.0

    env.advance_time(3)
    assert env.time.hour == 11.0
    assert not env.time.is_night

    env.advance_time(15)
    assert env.time.hour == 2.0  # 跨天
    assert env.time.absolute_day == 2
    assert env.time.is_night
    print("  ✓ EnvState: 时间推进")


def test_env_state_panic_cap():
    """恐慌值上限 10。"""
    env = EnvState()
    # 设一个过高的值
    env.aggregated_panic = 15
    # 没有直接 cap — 通过 from_vault_reader 和 apply_region_effects 保证
    # apply_region_effects 内部有 min(10, ...)
    loc = env.get_location("测试区")
    loc.mutation_level = 5
    env.apply_region_effects({"测试区": {"mutation_level": 5, "active_anomalies": []}})
    assert loc.panic_level <= 10, f"panic_level={loc.panic_level} 应 ≤10"
    print("  ✓ EnvState: 恐慌上限")


def test_env_state_location_auto_create():
    """get_location 自动创建新地点。"""
    env = EnvState()
    loc = env.get_location("新地点")
    assert loc.name == "新地点"
    assert loc.accessible
    print("  ✓ EnvState: 自动创建地点")


def test_env_state_region_effects_infrastructure():
    """变异等级影响基础设施。"""
    env = EnvState()
    env.apply_region_effects({
        "港口": {"mutation_level": 3, "active_anomalies": ["异常"]},
    })
    loc = env.locations["港口"]
    assert loc.power == "中断"
    assert loc.communication == "中断"
    assert not loc.accessible
    print("  ✓ EnvState: 变异等级 → 基础设施")


def test_memory_store_roundtrip():
    """Memory 序列化/反序列化来回。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(tmp)
        mems = [
            Memory("m1", "测试记忆", "perception", 1, 8, tags=["test"], location="城市"),
        ]
        store.save("主角", mems, "测试弧")
        loaded = store.load("主角")
        assert len(loaded) == 1
        assert loaded[0].id == "m1"
        assert loaded[0].content == "测试记忆"
        assert loaded[0].importance == 8
        assert loaded[0].tags == ["test"]
        assert loaded[0].location == "城市"
    print("  ✓ MemoryStore: 序列化/反序列化")


def test_memory_store_clear():
    """清理存储。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(tmp)
        store.save("mom", [Memory("m1", "x", "perception", 1, 5)])
        assert len(store.load("mom")) == 1
        store.clear("mom")
        assert len(store.load("mom")) == 0
    print("  ✓ MemoryStore: 清理")


def test_memory_store_nonexistent():
    """不存在的 agent 返回空列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(tmp)
        assert store.load("不存在") == []
    print("  ✓ MemoryStore: 不存在返回空")


def test_scene_director_find_shared_scenes():
    """同天同地 ≥2 角色 = 共享场景。"""
    outputs = {
        "主角": {"actions": [
            {"day": 12, "time": "09:00", "location": "酒店", "action": "到达"},
            {"day": 12, "time": "12:00", "location": "城市", "action": "吃饭"},
        ]},
        "配角": {"actions": [
            {"day": 12, "time": "09:00", "location": "酒店", "action": "也在"},
        ]},
        "配角乙": {"actions": [
            {"day": 13, "time": "20:00", "location": "城市", "action": "独处"},
        ]},
    }
    shared = find_shared_scenes(outputs)
    assert len(shared) == 1, f"应只有酒店一个共享场景, got {len(shared)}"
    assert shared[0]["location"] == "酒店"
    assert set(shared[0]["agents"]) == {"主角", "配角"}
    print("  ✓ scene_director: 共享场景检测")


def test_scene_director_no_shared_passthrough():
    """无共享场景 → 原样返回（同一对象）。"""
    outputs = {
        "主角": {"actions": [{"day": 12, "location": "家", "action": "在家"}]},
        "配角": {"actions": [{"day": 12, "location": "超市", "action": "购物"}]},
    }
    result = resolve_shared_scenes(outputs)
    assert result is outputs, "无共享场景应直接返回原 dict"
    print("  ✓ scene_director: 无共享场景直接返回")


def test_scene_director_resolve_with_mock():
    """mock api：共享场景被解析后的 turns 替换独立提案。"""
    outputs = {
        "主角": {"actions": [
            {"day": 12, "time": "09:00", "location": "酒店",
             "action": "主角独立到达", "inner": "太安静"},
        ]},
        "配角": {"actions": [
            {"day": 12, "time": "09:10", "location": "酒店",
             "action": "配角独立到达", "visible_to_linmo": False},
        ]},
    }

    def fake_api(system, user, schema, route_key="", temperature=0.0):
        assert "禁止词" in system
        return {
            "day": 12,
            "location": "酒店",
            "turns": [
                {"agent": "主角", "time": "09:00",
                 "action": "主角走进大厅，看到配角", "inner": "她怎么在这。"},
                {"agent": "配角", "time": "09:02",
                 "action": "配角先开口：" "你怎么也来了", "inner": ""},
            ],
        }

    result = resolve_shared_scenes(outputs, None, None, api_call_fn=fake_api)

    # 独立提案被移除，解析 turns 插入
    lm = result["主角"]["actions"]
    assert len(lm) == 1, f"主角应有 1 条解析后动作, got {len(lm)}"
    assert "看到配角" in lm[0]["action"]
    assert lm[0]["inner"] == "她怎么在这。"

    mom = result["配角"]["actions"]
    assert len(mom) == 1
    assert "先开口" in mom[0]["action"]
    # 共享场景主角在场 → 可见性强制为真
    assert mom[0]["visible_to_protagonist"] is True
    print("  ✓ scene_director: mock 解析 + 替换 + 可见性")


def test_scene_director_failure_fallback():
    """LLM 解析失败 → 保留独立提案，不丢数据。"""
    outputs = {
        "主角": {"actions": [
            {"day": 12, "location": "酒店", "action": "独立动作"},
        ]},
        "配角": {"actions": [
            {"day": 12, "location": "酒店", "action": "独立动作"},
        ]},
    }

    def failing_api(system, user, schema, route_key="", temperature=0.0):
        return {}

    result = resolve_shared_scenes(outputs, None, None, api_call_fn=failing_api)
    assert result["主角"]["actions"][0]["action"] == "独立动作"
    assert result["配角"]["actions"][0]["action"] == "独立动作"
    print("  ✓ scene_director: 失败回退保留原提案")


def test_agent_belief_revision():
    """AGM 式信念修正：revises 时旧信念标 revised，不删除。"""

    class MiniAgent(Agent):
        def perceive(self, world_output):
            return {}
        def build_system_prompt(self):
            return ""
        def build_decision_prompt(self, perceived):
            return ""
        def decide(self, perceived, api_call_fn=None):
            return {}

    agent = MiniAgent("测试", AgentState(name="测试"))
    agent.apply_belief_updates([
        {"subject": "世界", "proposition": "度假村在网上消失了", "confidence": 0.8},
    ])
    assert agent.state.beliefs[0].status == "active"

    # 认知反转：新信念推翻旧信念
    agent.apply_belief_updates([
        {"subject": "世界", "proposition": "度假村只是改了名", "confidence": 0.6,
         "revises": "度假村在网上消失了", "evidence": "找到新域名"},
    ])
    beliefs = agent.state.beliefs
    assert len(beliefs) == 2
    assert beliefs[0].status == "revised"
    assert beliefs[0].revised_evidence == "找到新域名"
    assert beliefs[1].status == "active"
    assert beliefs[1].proposition == "度假村只是改了名"
    print("  ✓ Agent: AGM 式信念修正")


def test_agent_reflect():
    """弧末反思：记忆压缩成高阶洞察，存为 reflection 记忆。"""

    class MiniAgent(Agent):
        def perceive(self, world_output):
            return {}
        def build_system_prompt(self):
            return ""
        def build_decision_prompt(self, perceived):
            return ""
        def decide(self, perceived, api_call_fn=None):
            return {}

    agent = MiniAgent("测试", AgentState(
        name="测试",
        memories=[
            Memory("m1", "看到度假村在网上消失", "perception", 5, 8),
            Memory("m2", "配角说她见过那个前台", "perception", 5, 6),
        ],
    ))

    def fake_api(system, user, schema, route_key="", temperature=0.0):
        assert "经历反思" in system
        assert "你当前的信念" in system
        return {"reflections": [
            {"content": "数据也会撒谎——至少记录可以。", "importance": 9, "tags": ["数据"]},
            {"content": "配角比我更擅长看人。", "importance": 7, "tags": ["配角"]},
        ]}

    added = agent.reflect(arc_config={"chapters": [5, 6, 7]}, api_call_fn=fake_api)
    assert len(added) == 2
    mems = agent.state.memories
    assert mems[-1].type == "reflection"
    assert mems[-1].created_at_chapter == 5
    assert all("reflection" in m.tags for m in mems[-2:])
    print("  ✓ Agent: 弧末反思记忆巩固")


def test_agent_reflect_failure():
    """反思 LLM 失败 → 返回空，不崩。"""
    agent = Agent.__new__(Agent)
    agent.name = "测试"
    agent.state = AgentState(name="测试")
    agent.retriever = MemoryRetriever()

    def failing_api(system, user, schema, route_key="", temperature=0.0):
        return {}

    added = agent.reflect(arc_config={"chapters": [1]}, api_call_fn=failing_api)
    assert added == []
    print("  ✓ Agent: 反思失败不中断")


def test_env_state_to_dict():
    """to_dict 输出格式正确。"""
    env = EnvState()
    d = env.to_dict()
    assert "time" in d
    assert "weather" in d
    assert "locations" in d
    assert "resources" in d
    assert "aggregated_panic" in d
    assert d["aggregated_panic"] == 0
    print("  ✓ EnvState: to_dict 格式")


if __name__ == "__main__":
    print("=" * 50)
    print("  引擎核心组件单元测试")
    print("=" * 50)
    print()

    tests = [
        test_memory_retriever_importance,
        test_memory_retriever_recent,
        test_memory_retriever_tag_match,
        test_memory_retriever_max,
        test_memory_retriever_empty,
        test_percept_filter_public_always_visible,
        test_percept_filter_location_gate,
        test_percept_filter_stage_order,
        test_percept_filter_custom_stage_order,
        test_env_state_advance_time,
        test_env_state_panic_cap,
        test_env_state_location_auto_create,
        test_env_state_region_effects_infrastructure,
        test_env_state_to_dict,
        test_memory_store_roundtrip,
        test_memory_store_clear,
        test_memory_store_nonexistent,
        test_scene_director_find_shared_scenes,
        test_scene_director_no_shared_passthrough,
        test_scene_director_resolve_with_mock,
        test_scene_director_failure_fallback,
        test_agent_belief_revision,
        test_agent_reflect,
        test_agent_reflect_failure,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print(f"结果: {passed}/{len(tests)} 通过", end="")
    if failed:
        print(f", {failed} 失败", end="")
    print()
    sys.exit(0 if failed == 0 else 1)
