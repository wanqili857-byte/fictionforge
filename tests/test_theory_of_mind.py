#!/usr/bin/env python3
"""
test_theory_of_mind.py — 理论心智层单元测试。

无 LLM / 无 I/O（解析全部走内联字符串）。覆盖：
- 真相表解析（含坏行/转义/false 事实）
- 信念 ↔ 事实三级匹配
- 知识 vs 真相对照（known / wrong / gaps）
- 事件/信念推进知识
- 跨角色 ToM 传播（共现 / 信任衰减）
- A 型反转追踪
- spec 标注 info_gaps + 渲染

用法:
    python3 tests/test_theory_of_mind.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from framework.agent_base import AgentState, Belief
from framework.theory_of_mind import (
    TruthTable, Fact, significant_terms,
    knowledge_snapshot, sync_knowledge_from_beliefs, sync_knowledge_from_events,
    sync_unknowns, propagate_tom_all, detect_type_a,
    annotate_spec, render_info_gaps,
)

FACTS_MD = """# 真相表

| id    | 类别     | 命题 |
|-------|----------|------|
| T-01  | 世界真相 | 静默号二十年前最后一条日志声称发现可证伪的文明遗迹，随后全员失联 |
| T-02  | 世界真相 | 残骸是数据层一段可自我改写的程序，会按观察者语言习惯生成档案 |
| T-03  | 角色真相 | 零号已被残骸部分改写，其关心行为来自被改写后的程序逻辑 |
| T-04  | 角色真相 | 陆离的文献学方法可复现，残骸生成的档案经测年确认伪造 |
| T-05  | 世界真相 | 全员失联的真正原因是被残骸摹写诱导进入数据舱 |
| T-06  | 世界真相 | 静默号船员仍活着并正常作息 | false |
"""


def make_state(name, knowledge=None, beliefs=None, tom=None, relationships=None):
    st = AgentState(name=name)
    if knowledge:
        st.knowledge = dict(knowledge)
    if beliefs:
        st.beliefs = list(beliefs)
    if tom:
        st.tom = dict(tom)
    if relationships:
        st.relationships = dict(relationships)
    return st


def belief(prop, status="active", subject="?"):
    return Belief(subject=subject, proposition=prop,
                  confidence=0.8, status=status)


# ── 真相表解析 ────────────────────────────────────────────────────────

def test_truth_table_parse_basic():
    t = TruthTable.parse(FACTS_MD)
    assert len(t.facts) == 6, f"expected 6 facts, got {len(t.facts)}"
    f01 = t.by_id("T-01")
    assert f01 is not None and f01.category == "世界真相"
    assert "静默号" in f01.proposition and f01.truth is True


def test_truth_table_parse_false_fact():
    t = TruthTable.parse(FACTS_MD)
    f06 = t.by_id("T-06")
    assert f06 is not None and f06.truth is False


def test_truth_table_parse_skips_bad_rows():
    md = (
        "| id | 类别 | 命题 |\n"
        "|---|---|---|\n"
        "| T-01 | 世界真相 | 第一条 |\n"
        "| 只有两列 | 坏行 |\n"
        "| T-02 | 世界真相 | 第二条 |\n"
        "\n"
        "| T-03 | 世界真相 | 第三条 |\n"
    )
    t = TruthTable.parse(md)
    ids = [f.id for f in t.facts]
    assert ids == ["T-01", "T-02", "T-03"], ids


def test_truth_table_parse_escaped_pipe():
    md = "| T-01 | 世界真相 | 密码是 a\\|b 组合 |\n"
    t = TruthTable.parse(md)
    assert t.facts[0].proposition == "密码是 a|b 组合"


def test_truth_table_parse_header_only_empty():
    t = TruthTable.parse("| id | 类别 | 命题 |\n|---|---|---|\n")
    assert t.facts == []
    assert TruthTable.parse("").facts == []


def test_truth_table_by_id_miss():
    t = TruthTable.parse(FACTS_MD)
    assert t.by_id("T-99") is None


# ── 信念 ↔ 事实匹配 ──────────────────────────────────────────────────

def test_match_belief_exact():
    t = TruthTable.parse(FACTS_MD)
    f = t.match_belief(belief(t.by_id("T-01").proposition))
    assert f is not None and f.id == "T-01"


def test_match_belief_containment():
    t = TruthTable.parse(FACTS_MD)
    prop = "静默号二十年前最后一条日志声称发现可证伪的文明遗迹，随后全员失联的真实原因"
    f = t.match_belief(belief(prop))
    assert f is not None and f.id == "T-01"


def test_match_belief_term_overlap():
    t = TruthTable.parse(FACTS_MD)
    f = t.match_belief(belief("残骸可以自我改写程序档案"))
    assert f is not None and f.id == "T-02"


def test_match_belief_no_hit():
    t = TruthTable.parse(FACTS_MD)
    assert t.match_belief(belief("今天食堂的菜很好吃")) is None


def test_significant_terms_drops_stopwords():
    terms = significant_terms("这是一个测试，关于静默号的事情")
    assert "静默号" in terms          # 长片切出 3-gram
    assert "测试" in terms            # 长片切出 2-gram
    assert "静默" in terms and "事情" in terms
    assert all(len(x) >= 2 for x in terms)
    assert "这是" not in terms        # 功能 n-gram 丢弃
    assert "关于" not in terms
    assert "的" not in terms          # 单字从不产出
    assert "一个" not in terms


# ── 知识 vs 真相对照 ─────────────────────────────────────────────────

def test_knowledge_snapshot_three_buckets():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.9}},
                    beliefs=[belief(t.by_id("T-06").proposition)])
    snap = knowledge_snapshot(st, t)
    assert [f.id for f in snap.known] == ["T-01"]
    assert len(snap.wrong_beliefs) == 1 and snap.wrong_beliefs[0]["fact"].id == "T-06"
    gap_ids = [f.id for f in snap.gaps]
    assert "T-02" in gap_ids and "T-05" in gap_ids and "T-01" not in gap_ids


def test_knowledge_snapshot_no_truth_table():
    st = make_state("陆离")
    snap = knowledge_snapshot(st, None)
    assert snap.known == [] and snap.gaps == []


# ── 信念/事件推进知识 ────────────────────────────────────────────────

def test_sync_knowledge_from_beliefs():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("零号", beliefs=[belief("残骸可以自我改写程序档案")])
    new = sync_knowledge_from_beliefs(st, t, 3)
    assert new == ["T-02"], new
    assert "T-02" in st.knowledge


def test_sync_belief_wrong_fact_not_added():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", beliefs=[belief(t.by_id("T-06").proposition)])
    new = sync_knowledge_from_beliefs(st, t, 3)
    assert new == [] and st.knowledge == {}


def test_sync_belief_revised_skipped():
    t = TruthTable.parse(FACTS_MD)
    b = belief(t.by_id("T-02").proposition, status="revised")
    st = make_state("零号", beliefs=[b])
    assert sync_knowledge_from_beliefs(st, t, 3) == []


def test_sync_knowledge_from_events():
    t = TruthTable.parse(FACTS_MD)
    knowledge = {}
    new = sync_knowledge_from_events(knowledge, ["残骸能生成档案"], t, 3)
    assert new == ["T-02"], new
    assert "T-02" in knowledge


def test_sync_knowledge_from_events_no_false_positive():
    t = TruthTable.parse(FACTS_MD)
    knowledge = {}
    assert sync_knowledge_from_events(knowledge, ["今天食堂的菜很好吃"], t, 3) == []


def test_sync_unknowns_derives_list():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.9}})
    sync_unknowns(st, t)
    assert "T-01" not in st.unknown_to_character
    assert set(st.unknown_to_character) == {f.id for f in t.facts} - {"T-01"}


# ── 跨角色 ToM 传播 ──────────────────────────────────────────────────

def _scene(agents, actions_text, day=1, loc="舱内"):
    return {
        "day": day, "location": loc, "agents": agents,
        "actions": [{"agent": a, "act": {"action": txt, "day": day, "location": loc}}
                    for a, txt in zip(agents, actions_text)],
    }


def test_propagate_tom_co_presence():
    t = TruthTable.parse(FACTS_MD)
    a = make_state("林默")
    b = make_state("陆离")
    agents = {"linmo": a, "luli": b}
    scene = _scene(["linmo", "luli"], ["残骸能生成档案", "他沉默地看着档案"])
    changes = propagate_tom_all(agents, [scene], t)
    assert a.tom["luli"].get("T-02") == "knows"
    assert b.tom["linmo"].get("T-02") == "knows"
    assert any(c["verdict"] == "knows" for c in changes)


def test_propagate_tom_absent_or_no_match():
    t = TruthTable.parse(FACTS_MD)
    a = make_state("林默")
    b = make_state("陆离")
    agents = {"linmo": a, "luli": b}
    # 场景无事实命中 → 无传播
    scene = _scene(["linmo", "luli"], ["他们聊了聊天气", "点头"])
    assert propagate_tom_all(agents, [scene], t) == []
    assert a.tom == {} and b.tom == {}


def test_propagate_tom_low_trust_sensitive():
    t = TruthTable.parse(FACTS_MD)
    a = make_state("林默", relationships={"陆离": {"type": "同事", "trust": 0.2}})
    b = make_state("陆离")
    agents = {"linmo": a, "luli": b}
    scene = _scene(["linmo", "luli"], ["零号关心行为来自被改写后的程序逻辑", "安静听着"])
    propagate_tom_all(agents, [scene], t)
    assert a.tom["luli"].get("T-03") == "uncertain"  # 角色真相 + 低信任


def test_propagate_tom_trust_not_downgraded():
    t = TruthTable.parse(FACTS_MD)
    a = make_state("林默", relationships={"陆离": {"type": "同事", "trust": 0.9}})
    b = make_state("陆离")
    agents = {"linmo": a, "luli": b}
    scene = _scene(["linmo", "luli"], ["零号关心行为来自被改写后的程序逻辑", "安静听着"])
    propagate_tom_all(agents, [scene], t)
    assert a.tom["luli"].get("T-03") == "knows"


# ── A 型反转追踪 ─────────────────────────────────────────────────────

def test_detect_type_a_new_knowledge():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    events = detect_type_a({"luli": st}, t, 3, prev_knowledge={"luli": set()})
    assert len(events) == 1
    assert events[0]["fact_id"] == "T-01"
    assert events[0]["confirmers"] == ["luli"]


def test_detect_type_a_no_change():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    events = detect_type_a({"luli": st}, t, 3,
                           prev_knowledge={"luli": {"T-01"}})
    assert events == []


def test_detect_type_a_no_baseline():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    # prev_knowledge=None → 无法判断增量，视为无变化
    assert detect_type_a({"luli": st}, t, 3) == []


# ── spec 标注 + 渲染 ─────────────────────────────────────────────────

def test_annotate_spec_adds_info_gaps():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    spec = {"chapter": 5, "sections": [{"id": "一"}]}
    out = annotate_spec(spec, {"luli": st}, t, 5)
    assert "info_gaps" in out and "info_gaps" not in spec  # 不改原对象
    ig = out["info_gaps"]
    assert ig["version"] == 1
    assert set(ig.keys()) == {"version", "per_character", "tom",
                              "type_a_candidates", "type_b_candidates"}
    assert ig["per_character"][0]["name"] == "陆离"


def test_annotate_spec_idempotent():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    spec = {"chapter": 5}
    out1 = annotate_spec(spec, {"luli": st}, t, 5)
    out2 = annotate_spec(out1, {"luli": st}, t, 5)
    assert out1 == out2


def test_annotate_spec_no_truth_table():
    spec = {"chapter": 5}
    out = annotate_spec(spec, {"luli": make_state("陆离")}, None, 5)
    assert out["info_gaps"]["per_character"] == []


def test_render_info_gaps_nonempty():
    t = TruthTable.parse(FACTS_MD)
    st = make_state("陆离", knowledge={"T-01": {"confidence": 0.8}})
    spec = annotate_spec({}, {"luli": st}, t, 5)
    text = render_info_gaps(spec["info_gaps"])
    assert "本章信息差" in text
    assert "T-01" in text and "陆离" in text


if __name__ == "__main__":
    print("=" * 50)
    print("  理论心智层单元测试")
    print("=" * 50)
    print()

    tests = [
        test_truth_table_parse_basic,
        test_truth_table_parse_false_fact,
        test_truth_table_parse_skips_bad_rows,
        test_truth_table_parse_escaped_pipe,
        test_truth_table_parse_header_only_empty,
        test_truth_table_by_id_miss,
        test_match_belief_exact,
        test_match_belief_containment,
        test_match_belief_term_overlap,
        test_match_belief_no_hit,
        test_significant_terms_drops_stopwords,
        test_knowledge_snapshot_three_buckets,
        test_knowledge_snapshot_no_truth_table,
        test_sync_knowledge_from_beliefs,
        test_sync_belief_wrong_fact_not_added,
        test_sync_belief_revised_skipped,
        test_sync_knowledge_from_events,
        test_sync_knowledge_from_events_no_false_positive,
        test_sync_unknowns_derives_list,
        test_propagate_tom_co_presence,
        test_propagate_tom_absent_or_no_match,
        test_propagate_tom_low_trust_sensitive,
        test_propagate_tom_trust_not_downgraded,
        test_detect_type_a_new_knowledge,
        test_detect_type_a_no_change,
        test_detect_type_a_no_baseline,
        test_annotate_spec_adds_info_gaps,
        test_annotate_spec_idempotent,
        test_annotate_spec_no_truth_table,
        test_render_info_gaps_nonempty,
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
